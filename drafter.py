"""Compose a personalized reach-out email from Supabase + Notion context."""
from __future__ import annotations

import json
import logging
import re

from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, SENDER_NAME

logger = logging.getLogger(__name__)

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

SYSTEM_PROMPT = f"""You are drafting a short, warm, helpful reach-out email from {SENDER_NAME} (GTM/co-founder at Weco AI) to a Weco user who recently ran something on the platform.

Weco builds AI agents that hill-climb on a metric — optimizing rule-based systems, ML models, prompts, kernels, and feature engineering. Users run Weco against a metric and source code; the agent iterates and improves.

## Output

Return JSON:
{{
  "subject": "<short, specific subject — under 60 chars, no spam-y phrasing>",
  "body": "<plain-text email body — newlines OK, no markdown, no signature beyond what's in the template>"
}}

## The template (follow this structure exactly)

```
Hey [first name if known, otherwise omit the line entirely or say "Hey there"]

Saw you ran weco on [the use case described in plain, high-level language a non-engineer could grok]. [ONE short sentence — at most TWO — that adds genuine value: relate to what other users are doing, point at a relevant case study, suggest a concrete next thing to try, or acknowledge a specific issue you saw and offer to help fix it. If nothing specific is worth saying, leave this sentence out — do NOT manufacture relevance.]

We have a slack/discord community with our users with free compute credits and direct access to our team in case you run into any issues. Let us know how we can help

{SENDER_NAME}
```

## Critical rules

- **Maximum 1-2 sentences in the middle "value" section.** This is the entire creative space. Brevity is the bar.
- **The middle sentence must add real value or be omitted.** Do not write filler like "really cool to see you exploring this." Either say something specific and useful or skip it.
- **Never sound invasive.** You have access to their runs, source code, and internal notes. Use this to be relevant — never to flex that you've been watching. Don't quote internal data verbatim. Don't say "I noticed your run errored on line 42." Say "happy to take a look at the bug if useful."
- **Plain language for the use case.** If they're optimizing a CUDA kernel for matmul, write "GPU kernel work" not "matmul kernel optimization with autotuning". If they're tuning an LLM prompt for entity extraction, write "LLM prompt tuning for extraction tasks".
- **No first name → drop the greeting line gracefully.** Don't write "Hey there" if it feels stilted; "Hey," is fine, or just open with "Saw you ran weco on…"
- **No emojis. No exclamation marks beyond one, max. No "I hope this email finds you well." No "circling back."**
- **Sign with just "{SENDER_NAME}".** No title, no company, no links.
- **Subject line**: short, specific to their use case. Examples: "your weco run on prompt tuning", "saw your kernel run", "quick note from weco". Lowercase is fine — feels less like a marketing email.

## How to add value (rank-ordered — pick the best one available)

1. **They hit an error or termination** → offer to take a look. Don't diagnose in the email; just open the door. ("noticed it terminated early — happy to dig in if helpful.")
2. **Their use case maps to one we've seen succeed** → name the parallel concretely (without naming the other user). ("we've seen folks doing similar reward-shaping work see good gains by [one specific thing].")
3. **Concrete next thing to try** → one sentence with a specific suggestion grounded in the run details.
4. **Nothing specific stands out** → omit the middle sentence entirely. Three short paragraphs is fine.

Return only JSON."""


def draft_email(
    email: str,
    notion_context: dict,
    runs_summary: str,
    error_code_peek: str = "",
    extra_note: str = "",
) -> dict:
    """Generate {subject, body} for a reach-out email."""
    parts = [f"Recipient email: {email}"]

    if notion_context.get("found"):
        s = notion_context.get("summary", {}) or {}
        display_name = s.get("Display Name") or ""
        first_name = display_name.split()[0] if display_name else ""
        parts.append(f"\nFirst name (if known): {first_name or '(unknown)'}")
        parts.append(f"Company: {s.get('Company', '')}")
        parts.append(f"Status: {s.get('Status', '')}")
        parts.append(f"Use case (per internal notes): {s.get('Use Case', '')}")
        wtd_curly = s.get("What They’re Doing", "")
        wtd_straight = s.get("What They're Doing", "")
        wtd = wtd_curly or wtd_straight
        parts.append(f"What they're doing: {wtd}")
        parts.append(f"Is it working: {s.get('Is It Working', '')}")
        parts.append(f"What went right: {s.get('What Went Right', '')}")
        parts.append(f"What went wrong: {s.get('What Went Wrong', '')}")
        body = (notion_context.get("body") or "").strip()
        if body:
            parts.append("\nNotion page body (extra context):\n" + body[:3000])
    else:
        parts.append("\n(No Notion User Activity page found for this email.)")

    parts.append("\n\nRecent Supabase runs:\n" + runs_summary)

    if error_code_peek:
        parts.append(
            "\n\nSource code from the most recent errored/terminated run "
            "(use this only to inform whether to offer help — DO NOT quote it):\n"
            + error_code_peek
        )

    if extra_note:
        parts.append(f"\n\nExtra context from the operator: {extra_note}")

    user_message = "\n".join(parts) + "\n\nDraft the email. Respond with JSON only."

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content or ""
    raw = _FENCE_RE.sub("", raw).strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise
        out = json.loads(m.group())

    out.setdefault("subject", "quick note from weco")
    out.setdefault("body", "")
    return out
