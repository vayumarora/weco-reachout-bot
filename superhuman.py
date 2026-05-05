"""Build a mailto: URL that opens Superhuman compose with subject + body pre-filled.

Requires Superhuman to be set as the default mail handler on the user's machine.
"""
from __future__ import annotations

from urllib.parse import quote


def build_mailto(to: str, subject: str, body: str) -> str:
    """Build a mailto: URL with URL-encoded subject and body.

    mailto: spec uses RFC 2368 — newlines in body should be %0A.
    """
    # quote() with safe="" encodes everything; we want %20 for space (default).
    qs_subject = quote(subject, safe="")
    qs_body = quote(body, safe="")
    return f"mailto:{to}?subject={qs_subject}&body={qs_body}"
