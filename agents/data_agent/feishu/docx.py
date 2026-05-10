"""Feishu Docx read — pulls a document's blocks and flattens them to text.

Used by the report pipeline when the user picks a docx (instead of a bitable)
in the front-end picker. We expose only a read path; the agent doesn't write
back to user-space docx (that's doc_writer's job, and only for tenant-owned
storage). Real Feishu endpoint:

    GET /open-apis/docx/v1/documents/{document_id}/blocks

The mock service implements the same path with a slightly simpler body shape.
"""
from __future__ import annotations

import httpx

from ._http import parse_or_raise, request_with_retry

_BLOCKS_ENDPOINT = "/open-apis/docx/v1/documents/{document_id}/blocks"


def _block_to_text(b: dict) -> str:
    """Reduce a Feishu Docx block to plain text.

    Feishu's block shape varies by ``block_type`` — for text/heading/bullet
    types the content lives under a typed key (``text``/``heading1``/...) with
    an ``elements`` array of ``{text_run:{content:..}}`` runs. We tolerate
    older mock-style ``{block_type, text}`` records too.
    """
    # Mock shorthand: {"block_type": "text", "text": "..."}
    if isinstance(b.get("text"), str):
        return b["text"]

    # Real shape: typed payload keys
    candidates = ("text", "heading1", "heading2", "heading3", "heading4",
                  "heading5", "heading6", "bullet", "ordered", "quote", "code")
    for key in candidates:
        node = b.get(key)
        if not isinstance(node, dict):
            continue
        elements = node.get("elements") or []
        parts: list[str] = []
        for e in elements:
            tr = e.get("text_run") or {}
            c = tr.get("content")
            if isinstance(c, str):
                parts.append(c)
        if parts:
            joined = "".join(parts)
            # Mark headings so downstream synth can keep structure
            if key.startswith("heading"):
                level = key[-1] if key[-1].isdigit() else "2"
                return f"{'#' * int(level)} {joined}"
            if key == "bullet":
                return f"- {joined}"
            return joined
    return ""


async def read_document(
    *,
    base: str,
    token: str,
    document_id: str,
    page_size: int = 500,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Return ``{document_id, blocks: [{block_type, text}], block_count}``.

    Stops after one page (page_size up to 500 covers typical reports). Caller
    can re-paginate by passing ``page_token`` if it's ever needed.
    """
    path = _BLOCKS_ENDPOINT.format(document_id=document_id)
    url = f"{base.rstrip('/')}{path}"
    own = client is None
    c = client or httpx.AsyncClient(timeout=15.0)
    try:
        r = await request_with_retry(
            c, "GET", url,
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": page_size},
        )
        body = parse_or_raise(r, endpoint=path)
    finally:
        if own:
            await c.aclose()

    items = (body.get("data") or {}).get("items") or []
    blocks: list[dict] = []
    for it in items:
        text = _block_to_text(it)
        if not text:
            continue
        blocks.append({
            "block_type": it.get("block_type", "text") if isinstance(it.get("block_type"), str) else "text",
            "text": text,
        })
    return {
        "document_id": document_id,
        "blocks": blocks,
        "block_count": len(blocks),
    }
