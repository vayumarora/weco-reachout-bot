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
Hey [first name if known, otherwise omit the greeting line entirely]

Saw you ran weco on [the use case described in plain, high-level language a non-engineer could grok]. [ONE sentence — at most TWO — that adds genuine, specific value. Pull from the actual data provided. If nothing specific stands out as worth saying, OMIT this sentence entirely.]

We have a slack/discord community with our users with free compute credits and direct access to our team in case you run into any issues. Let us know how we can help

{SENDER_NAME}
```

## ABSOLUTE rules — violating these makes the email worse than not sending

1. **Be factually correct.** Every claim about their run must be grounded in the data above. If the data shows their metric IMPROVED (e.g., val_loss went from 0.46 → 0.21), do NOT say "noticed runs had no improvement". Read carefully. If you are not sure whether something improved or not, do not claim either.
2. **The middle sentence must EARN its place.** Filler kills this email. "Happy to dig in if helpful" attached to nothing specific is filler. Either:
   (a) cite a specific data point that shows you actually paid attention ("nice to see val_loss come down from 0.46 → 0.21 on StethoBench"), OR
   (b) make a specific concrete suggestion or offer ("happy to look at the runs that errored on the F1 metric — we've seen index errors there before"), OR
   (c) omit the sentence entirely.
3. **Never sound invasive.** You have access to their runs, source code, and internal notes. Use this to be relevant — never to flex that you've been watching. Don't quote source code. Don't say "I noticed your run errored on line 42 of foo.py". Don't reference internal Notion notes verbatim.
4. **Plain language.** "GPU kernel work" not "matmul kernel optimization with autotuning". "LLM prompt tuning for extraction" not "few-shot prompt engineering for NER". "model fine-tuning for medical reasoning" not "LoRA adaptation on a medical-domain transformer".
5. **No greeting if no first name.** Don't write "Hey there" — it's stilted. Just open with the "Saw you ran weco on…" line.
6. **No emojis. At most one exclamation mark in the whole email (probably zero). No "I hope this email finds you well." No "circling back." No "wanted to reach out."**
7. **Sign with just "{SENDER_NAME}".** No title, no company, no links.
8. **Subject line**: short, specific, lowercase OK. Examples: "your weco run on prompt tuning", "saw your kernel run", "quick note on the stethobench runs". Avoid "Following up", "Quick question".

## How to write the middle sentence (priority order — pick the best applicable)

1. **They had a clear win** → acknowledge it specifically with the number. *"saw your val_loss come down from 0.46 → 0.21 on StethoBench — nice progress."* This is the highest-value option when the data supports it.
2. **They hit errors / terminations on a clear pattern** → offer specific help, not generic. *"noticed a chunk of runs terminated on the F1 metric — happy to take a look if you want a second pair of eyes."*
3. **Their use case maps to a pattern we've seen** → name the parallel without naming the other user. *"we've seen folks doing reward-shaping work get gains by widening the eval batch — could be worth a try."*
4. **Concrete next thing to try** → one specific suggestion grounded in their run. *"if you're hitting a ceiling on Stage 1, the move other users have made is opening up the search to the model class itself."*
5. **Nothing specific stands out** → omit the sentence entirely. The two-paragraph version is fine.

## Self-check before returning

Read your draft body once more, asking:
- Is every factual claim about their run actually supported by the data above? (If not, fix or remove it.)
- Is the middle sentence specific enough that they'd know it was written for them, not a template? (If no, omit it.)
- Does it sound like a friendly note from a founder, not a sales email? (If no, cut more.)

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
