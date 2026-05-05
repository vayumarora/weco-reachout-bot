"""Read the Notion User Activity DB for richer context on a user.

The DB is populated by Weco User Tracking/src/notion_db.py — each page corresponds
to one user (keyed by email as the title) and contains LLM-generated summaries:
what they're doing, is it working, what went right/wrong, use case, status, etc.
"""
from __future__ import annotations

import logging
from typing import Optional

from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError

from config import NOTION_API_KEY, NOTION_USER_ACTIVITY_DB_ID

logger = logging.getLogger(__name__)

_client: Optional[NotionClient] = None
_data_source_id: Optional[str] = None


def _get_client() -> NotionClient:
    global _client
    if _client is None:
        if not NOTION_API_KEY:
            raise RuntimeError("NOTION_API_KEY is not set")
        _client = NotionClient(auth=NOTION_API_KEY, timeout_ms=60_000)
    return _client


def _get_data_source_id() -> str:
    global _data_source_id
    if _data_source_id:
        return _data_source_id
    try:
        db = _get_client().databases.retrieve(database_id=NOTION_USER_ACTIVITY_DB_ID)
        ds = db.get("data_sources", [])
        _data_source_id = ds[0]["id"] if ds else NOTION_USER_ACTIVITY_DB_ID
    except Exception as e:
        logger.warning(f"Could not resolve data source: {e}")
        _data_source_id = NOTION_USER_ACTIVITY_DB_ID
    return _data_source_id


def find_page_by_email(email: str) -> Optional[dict]:
    """Find the Notion page for a user. Returns the raw page dict or None."""
    try:
        ds_id = _get_data_source_id()
        resp = _get_client().data_sources.query(
            data_source_id=ds_id,
            filter={"property": "Email", "title": {"equals": email}},
        )
        results = resp.get("results", [])
        if results:
            return results[0]
    except APIResponseError as e:
        logger.warning(f"Notion query failed for {email}: {e}")
    return None


def _plain_text(rich: list) -> str:
    return "".join(r.get("plain_text", "") for r in (rich or []))


def extract_user_summary(page: dict) -> dict:
    """Pull the human-readable property values off a page."""
    props = page.get("properties", {}) or {}
    out: dict = {}

    def get_text(prop_name: str) -> str:
        p = props.get(prop_name) or {}
        ptype = p.get("type")
        if ptype == "rich_text":
            return _plain_text(p.get("rich_text", []))
        if ptype == "title":
            return _plain_text(p.get("title", []))
        if ptype == "select":
            sel = p.get("select") or {}
            return sel.get("name", "")
        if ptype == "multi_select":
            return ", ".join(s.get("name", "") for s in p.get("multi_select", []))
        if ptype == "number":
            n = p.get("number")
            return str(n) if n is not None else ""
        if ptype == "date":
            d = p.get("date") or {}
            return d.get("start", "") or ""
        return ""

    # Match the property names the User Tracking pipeline actually writes (see
    # Weco User Tracking/src/notion_db.py).
    for field in [
        "Email",
        "Name",
        "Company",
        "Status",
        "Use Case",
        "What They're Doing",
        "Is It Working",
        "Product",
        "Runs",
        "Signup → 1st Run",
        "First Run",
        "Last Run",
        "Last Updated",
    ]:
        out[field] = get_text(field)

    # Convenience alias so drafter doesn't have to know the exact column name
    out["Display Name"] = out.get("Name", "")
    return out


def fetch_page_body_markdown(page_id: str, max_blocks: int = 80) -> str:
    """Fetch the page body as a rough markdown blob (best-effort)."""
    try:
        blocks = _get_client().blocks.children.list(block_id=page_id, page_size=max_blocks)
    except APIResponseError as e:
        logger.warning(f"Could not list blocks for {page_id}: {e}")
        return ""

    parts = []
    for b in blocks.get("results", []):
        btype = b.get("type")
        data = b.get(btype) or {}
        text = _plain_text(data.get("rich_text", []))
        if not text:
            continue
        if btype == "heading_1":
            parts.append(f"# {text}")
        elif btype == "heading_2":
            parts.append(f"## {text}")
        elif btype == "heading_3":
            parts.append(f"### {text}")
        elif btype in ("bulleted_list_item", "numbered_list_item"):
            parts.append(f"- {text}")
        elif btype == "quote":
            parts.append(f"> {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


def get_user_context(email: str) -> dict:
    """High-level: return everything the drafter wants to know from Notion."""
    page = find_page_by_email(email)
    if not page:
        return {"found": False}

    summary = extract_user_summary(page)
    body = fetch_page_body_markdown(page["id"])
    return {
        "found": True,
        "page_url": page.get("url"),
        "summary": summary,
        "body": body,
    }
