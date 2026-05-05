"""
Slack-triggered reach-out drafter.

Two ways to trigger:

1. Slash command: `/reachout email@domain.com [optional note]`
   → Bot replies in DM with a button that opens Superhuman compose.

2. Emoji reaction: react with :envelope: on any message in #user-activity
   (or anywhere the bot is invited). The bot extracts an email from the
   message text and DMs the operator with a draft button.

Flow inside the bot:
  trigger → resolve email → fetch Notion + Supabase context → Claude drafts
  subject+body → build mailto: URL → DM the operator a "Open in Superhuman" button.

Clicking the button uses Slack's native `url` action (no callback needed) which
opens the mailto: link in the OS, which Superhuman handles when set as default
mail client.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import (
    HOST,
    PORT,
    SLACK_BOT_TOKEN,
    SLACK_SIGNING_SECRET,
    TRIGGER_EMOJIS,
    VAYUM_SLACK_USER_ID,
)
from drafter import draft_email
from notion_lookup import get_user_context
from superhuman import build_mailto
from supabase_lookup import (
    fetch_recent_runs,
    find_user_by_email,
    peek_source_code,
    summarize_runs,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
slack = WebClient(token=SLACK_BOT_TOKEN)
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="reachout-worker")

# Dedup reaction events — Slack retries aggressively
_seen_event_ids: set[str] = set()
_seen_event_ids_max = 1000

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


# ── Signature verification ──────────────────────────────────────────────────


def _verify_slack_signature(body: bytes, headers) -> bool:
    if not SLACK_SIGNING_SECRET:
        logger.warning("SLACK_SIGNING_SECRET not set — accepting unsigned request")
        return True
    ts = headers.get("X-Slack-Request-Timestamp", "")
    sig = headers.get("X-Slack-Signature", "")
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except ValueError:
        return False
    base = b"v0:" + ts.encode() + b":" + body
    expected = (
        "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, sig)


# ── Core drafting flow ──────────────────────────────────────────────────────


def _build_draft_message(email: str, subject: str, body: str, page_url: str | None) -> dict:
    """Build the Slack message payload (blocks) shown to the operator."""
    mailto = build_mailto(email, subject, body)

    preview_lines = [f"*To:* {email}", f"*Subject:* {subject}", "", body]
    preview_text = "\n".join(preview_lines)

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":envelope_with_arrow: *Draft for {email}*"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "```" + preview_text + "```"},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📧 Open in Superhuman"},
                    "style": "primary",
                    "url": mailto,
                    "action_id": "open_superhuman",
                }
            ],
        },
    ]

    if page_url:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"<{page_url}|User Activity in Notion>"}
                ],
            }
        )

    return {"blocks": blocks, "text": f"Draft for {email}"}


def _draft_and_dm(operator_user_id: str, email: str, extra_note: str = ""):
    """Background job: gather context, draft email, DM operator with button."""
    try:
        notion_ctx = get_user_context(email)
        user = find_user_by_email(email)
        runs = fetch_recent_runs(user["user_id"]) if user else []
        runs_summary = summarize_runs(runs)
        error_peek = peek_source_code(runs) if runs else ""

        result = draft_email(
            email=email,
            notion_context=notion_ctx,
            runs_summary=runs_summary,
            error_code_peek=error_peek,
            extra_note=extra_note,
        )
        msg = _build_draft_message(
            email=email,
            subject=result["subject"],
            body=result["body"],
            page_url=notion_ctx.get("page_url"),
        )
        slack.chat_postMessage(channel=operator_user_id, **msg)
        logger.info("draft delivered: %s → %s", email, operator_user_id)
    except Exception as e:
        logger.exception("draft generation failed")
        try:
            slack.chat_postMessage(
                channel=operator_user_id,
                text=f":x: Couldn't draft a reach-out for `{email}`: {e}",
            )
        except SlackApiError:
            pass


# ── Slash command: /reachout ────────────────────────────────────────────────


@app.route("/slack/commands", methods=["POST"])
def slack_commands():
    if not _verify_slack_signature(request.get_data(), request.headers):
        return ("invalid signature", 401)

    command = request.form.get("command", "")
    text = (request.form.get("text") or "").strip()
    user_id = request.form.get("user_id", "")

    if command != "/reachout":
        return jsonify({"text": f"Unknown command: {command}"}), 200

    # Parse: first token = email, rest = optional note.
    if not text:
        return jsonify({
            "response_type": "ephemeral",
            "text": "Usage: `/reachout email@domain.com [optional note]`",
        }), 200

    parts = text.split(maxsplit=1)
    email = parts[0].strip().strip("<>").lstrip("mailto:")
    note = parts[1] if len(parts) > 1 else ""

    if not EMAIL_RE.fullmatch(email):
        return jsonify({
            "response_type": "ephemeral",
            "text": f"That doesn't look like a valid email: `{email}`",
        }), 200

    _executor.submit(_draft_and_dm, user_id, email, note)

    return jsonify({
        "response_type": "ephemeral",
        "text": f":hourglass_flowing_sand: Drafting a reach-out for `{email}` — I'll DM you the button in ~10s.",
    }), 200


# ── Events: reaction_added with :envelope: ──────────────────────────────────


def _walk_blocks_for_text(node) -> list[str]:
    """Recursively pull plain text out of Slack block_kit / rich_text structures.

    Handles both Block Kit composition objects (mrkdwn/plain_text) and
    rich_text element types (text/link/user/channel).
    """
    out: list[str] = []
    if isinstance(node, dict):
        ntype = node.get("type")
        # Block Kit text composition objects: {"type": "mrkdwn"|"plain_text", "text": "..."}
        if ntype in ("mrkdwn", "plain_text") and isinstance(node.get("text"), str):
            out.append(node["text"])
        elif ntype == "text" and isinstance(node.get("text"), str):
            out.append(node["text"])
        elif ntype == "link":
            url = node.get("url", "")
            txt = node.get("text", "")
            if url:
                out.append(url)
            if isinstance(txt, str) and txt:
                out.append(txt)
        elif ntype == "user":
            uid = node.get("user_id")
            if uid:
                out.append(f"<@{uid}>")
        elif ntype == "channel":
            cid = node.get("channel_id")
            if cid:
                out.append(f"<#{cid}>")
        # Recurse into all child values to catch nested elements
        for v in node.values():
            if isinstance(v, (dict, list)):
                out.extend(_walk_blocks_for_text(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_blocks_for_text(item))
    return out


def _resolve_message_text(channel: str, ts: str) -> str:
    """Fetch the original message that was reacted to.

    Joins together: the top-level text field, all attachment fallbacks, and any
    rich text inside blocks. Bot-posted messages often have empty `text` and put
    everything in blocks.
    """
    try:
        resp = slack.conversations_history(channel=channel, latest=ts, inclusive=True, limit=1)
        msgs = resp.data.get("messages", [])
        if not msgs:
            return ""
        msg = msgs[0]
        # Debug: log a compact view of the message structure
        try:
            keys = sorted(msg.keys())
            n_blocks = len(msg.get("blocks") or [])
            n_atts = len(msg.get("attachments") or [])
            logger.info(
                "message structure: keys=%s blocks=%d attachments=%d subtype=%s bot=%s",
                keys, n_blocks, n_atts, msg.get("subtype"), msg.get("bot_id"),
            )
            import json as _json
            logger.info("message raw (truncated): %s", _json.dumps(msg)[:2000])
        except Exception:
            pass
        parts: list[str] = []
        top = msg.get("text") or ""
        if top:
            parts.append(top)
        # Walk blocks for rich-text content (where bots typically put their content)
        blocks = msg.get("blocks") or []
        parts.extend(_walk_blocks_for_text(blocks))
        # Walk attachments too — some legacy bots post here
        for att in msg.get("attachments") or []:
            for k in ("text", "pretext", "fallback", "title"):
                v = att.get(k)
                if v:
                    parts.append(v)
            parts.extend(_walk_blocks_for_text(att.get("blocks") or []))
        return "\n".join(parts)
    except SlackApiError as e:
        logger.warning("conversations_history failed: %s", e.response.get("error"))
    return ""


@app.route("/slack/events", methods=["POST"])
def slack_events():
    if not _verify_slack_signature(request.get_data(), request.headers):
        return ("invalid signature", 401)

    payload = request.get_json(silent=True) or {}

    # URL verification handshake when Slack first hits this endpoint.
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge")})

    # Dedup retries by event_id.
    event_id = payload.get("event_id")
    if event_id:
        if event_id in _seen_event_ids:
            return ("", 200)
        _seen_event_ids.add(event_id)
        if len(_seen_event_ids) > _seen_event_ids_max:
            _seen_event_ids.clear()

    event = payload.get("event") or {}
    if event.get("type") != "reaction_added":
        return ("", 200)

    reaction = event.get("reaction", "")
    if reaction not in TRIGGER_EMOJIS:
        logger.info("ignoring reaction :%s: (not in trigger set %s)", reaction, sorted(TRIGGER_EMOJIS))
        return ("", 200)

    reactor = event.get("user", "")
    item = event.get("item") or {}
    if item.get("type") != "message":
        logger.info("reaction on non-message item: %s", item.get("type"))
        return ("", 200)

    logger.info("reaction :%s: by %s on message in %s", reaction, reactor, item.get("channel"))

    channel = item.get("channel", "")
    ts = item.get("ts", "")

    # Operator: the person who reacted. Falls back to VAYUM_SLACK_USER_ID if set.
    operator = reactor or VAYUM_SLACK_USER_ID
    if not operator:
        logger.warning("no operator user — set VAYUM_SLACK_USER_ID as fallback")
        return ("", 200)

    text = _resolve_message_text(channel, ts)
    logger.info("resolved message text (%d chars): %s", len(text), text[:300])
    raw_emails = EMAIL_RE.findall(text)
    # Dedup, preserve order, cap at 3 to avoid runaway drafts
    seen: set[str] = set()
    emails: list[str] = []
    for e in raw_emails:
        e = e.lower()
        if e not in seen:
            seen.add(e)
            emails.append(e)
        if len(emails) >= 3:
            break
    logger.info("found %d unique email(s): %s", len(emails), emails)
    if not emails:
        try:
            slack.chat_postMessage(
                channel=operator,
                text=(
                    ":mag: I saw your :envelope: reaction but couldn't find an email "
                    "in that message. Use `/reachout email@domain.com` to draft manually."
                ),
            )
        except SlackApiError:
            pass
        return ("", 200)

    for email in emails:
        _executor.submit(_draft_and_dm, operator, email)
    return ("", 200)


# ── Health ──────────────────────────────────────────────────────────────────


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "weco-reachout-bot", "status": "ok"}), 200


if __name__ == "__main__":
    logger.info(f"Starting Weco Reachout Bot on {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
