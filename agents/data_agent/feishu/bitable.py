"""Feishu bitable read client with pagination + 429 retry."""
from __future__ import annotations

import httpx

from ._http import parse_or_raise, request_with_retry

_ENDPOINT = "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"


async def list_records(
    *,
    base: str,
    token: str,
    app_token: str,
    table_id: str,
    page_size: int = 100,
    view_id: str | None = None,
    client: httpx.AsyncClient | None = None,
    max_pages: int = 10,
) -> list[dict]:
    """Return up to ``max_pages * page_size`` records.

    Walks ``page_token`` until ``has_more`` is false or the page cap is hit.
    Capping the loop prevents an unbounded fetch when an upstream view ends
    up unexpectedly large; raise the cap explicitly when bulk export is
    actually wanted.
    """
    path = _ENDPOINT.format(app_token=app_token, table_id=table_id)
    url = f"{base.rstrip('/')}{path}"
    own = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    out: list[dict] = []
    page_token: str | None = None
    pages = 0
    try:
        while True:
            params: dict = {"page_size": page_size}
            if view_id:
                params["view_id"] = view_id
            if page_token:
                params["page_token"] = page_token
            r = await request_with_retry(
                c,
                "GET",
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            body = parse_or_raise(r, endpoint=path)
            data = body.get("data") or {}
            out.extend(data.get("items") or [])
            pages += 1
            page_token = data.get("page_token") or None
            if not data.get("has_more") or not page_token or pages >= max_pages:
                break
    finally:
        if own:
            await c.aclose()
    return out
