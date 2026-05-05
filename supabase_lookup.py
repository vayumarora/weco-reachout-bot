"""Look up a user's run history in Supabase for personalization context.

Mirrors the read patterns in Weco User Tracking/src/supabase_client.py but trimmed
to just what the drafter needs.
"""
from __future__ import annotations

import logging
from typing import Optional

from supabase import create_client

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("Supabase env vars are not set")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def find_user_by_email(email: str) -> Optional[dict]:
    """Find a user in user_funnel by email. Returns dict or None."""
    try:
        resp = (
            _get_client()
            .table("user_funnel")
            .select("user_id,email,signed_up_at,first_run_at,second_run_at")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]
    except Exception as e:
        logger.warning(f"user_funnel lookup failed for {email}: {e}")
    return None


def fetch_recent_runs(user_id: str, limit: int = 10) -> list[dict]:
    """Fetch the most recent N runs for a user. Includes source_code peek."""
    try:
        resp = (
            _get_client()
            .table("runs")
            .select(
                "id,name,run_type,metric_name,maximize,steps,status,"
                "created_at,updated_at,termination_reason,additional_instructions,"
                "code_generator,source_code"
            )
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as e:
        logger.warning(f"runs lookup failed for {user_id}: {e}")
        return []


def summarize_runs(runs: list[dict]) -> str:
    """Compact human-readable summary of runs for the LLM prompt.

    Designed for the drafter — strips out source code (too long) but keeps the
    signal: what they're optimizing, status, errors, instructions.
    """
    if not runs:
        return "No runs yet."

    lines = []
    for r in runs:
        name = r.get("name") or "(unnamed)"
        metric = r.get("metric_name") or "?"
        direction = "↑" if r.get("maximize") else "↓"
        status = r.get("status") or "?"
        steps = r.get("steps") or 0
        created = (r.get("created_at") or "")[:10]
        instructions = (r.get("additional_instructions") or "").strip()
        term = (r.get("termination_reason") or "").strip()

        line = f"- [{created}] {name} — optimizing {metric} ({direction}, {steps} steps, {status})"
        if instructions:
            # Cap instructions to keep prompt small
            snippet = instructions[:300] + ("…" if len(instructions) > 300 else "")
            line += f"\n    instructions: {snippet}"
        if term and status in ("error", "terminated"):
            line += f"\n    termination: {term[:200]}"
        lines.append(line)

    return "\n".join(lines)


def peek_source_code(runs: list[dict], max_chars: int = 2000) -> str:
    """Peek at source code for the most recent run that errored or terminated.

    The drafter uses this only when something went wrong — to suggest a real fix,
    not to be invasive.
    """
    for r in runs:
        if r.get("status") not in ("error", "terminated"):
            continue
        sc = r.get("source_code")
        if not sc:
            continue
        # source_code is a dict mapping file path → code
        if isinstance(sc, dict):
            joined = []
            for path, code in sc.items():
                if not isinstance(code, str):
                    continue
                joined.append(f"# {path}\n{code}")
            text = "\n\n".join(joined)
        elif isinstance(sc, str):
            text = sc
        else:
            continue
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(truncated)"
        return text
    return ""
