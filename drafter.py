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
  "body": "<plain-text email body — newlines OK, no markdown except the Discord link, no signature beyond what's in the template>"
}}

## The template (follow this structure exactly)

```
Hey [first name],

Saw you ran Weco on [use case in plain language]. [ONE compliment sentence ONLY if the data genuinely warrants it — cite a specific metric movement. If nothing stands out, OMIT this sentence entirely.] [If there IS a metric worth mentioning and you didn't already cover it in the compliment: "Nice to see [metric] [movement]." Otherwise omit.]

If you're having trouble seeing any improvements or want to push it further, drop in our Discord (https://discord.gg/Y5bnVtNs3g) — we'll personally help you tune your eval, and we'll keep you in credits while you iterate. Standout runs get featured in our community showcase. You'll also see what other users are working on — the team's in there and we read everything.

What's the bigger problem you're working toward?

{SENDER_NAME}
```

## ABSOLUTE rules — violating these makes the email worse than not sending

1. **Be factually correct.** Every claim about their run must be grounded in the data provided. If the data shows their metric IMPROVED (e.g., val_loss went from 0.46 → 0.21), do NOT say "noticed runs had no improvement". Read carefully. If you are not sure whether something improved or not, do not claim either.
2. **The compliment/metric sentence must EARN its place.** Only include it if there's a real, specific data point. "Nice to see val_loss come down from 0.46 → 0.21" is good. Generic filler like "looks like you're making progress" with no number is not. If nothing specific stands out, go straight from the use case sentence to the Discord paragraph.
3. **Never sound invasive.** You have access to their runs, source code, and internal notes. Use this to be relevant — never to flex that you've been watching. Don't quote source code. Don't reference internal Notion notes verbatim.
4. **Plain language.** "GPU kernel work" not "matmul kernel optimization with autotuning". "LLM prompt tuning" not "few-shot prompt engineering for NER". "model training for medical reasoning" not "LoRA adaptation on a medical-domain transformer".
5. **No greeting if no first name.** Don't write "Hey there" — just open with "Saw you ran Weco on…"
6. **No emojis. At most one exclamation mark in the whole email (probably zero). No "I hope this email finds you well." No "circling back." No "wanted to reach out."**
7. **Sign with just "{SENDER_NAME}".** No title, no company, no extra links.
8. **Subject line**: short, specific, lowercase OK. Examples: "your weco run on prompt tuning", "saw your kernel run", "quick note on the stethobench runs". Avoid "Following up", "Quick question".
9. **The Discord paragraph is ALWAYS included verbatim** (with the actual Discord link). Don't rephrase it or skip it.
10. **Always end with "What's the bigger problem you're working toward?"** — this is the call to action. Don't rephrase it.

## Self-check before returning

Read your draft body once more, asking:
- Is every factual claim about their run actually supported by the data? (If not, fix or remove it.)
- Does the compliment sentence cite a specific number? (If not, omit it.)
- Is the Discord paragraph included word-for-word?
- Does it end with "What's the bigger problem you're working toward?"
- Does it sound like a friendly note from a founder, not a sales email?

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
