"""Feishu bitable read client."""
from __future__ import annotations

import httpx


async def list_records(
    *,
    base: str,
    token: str,
    app_token: str,
    table_id: str,
    page_size: int = 100,
    view_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    url = f"{base.rstrip('/')}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    params: dict = {"page_size": page_size}
    if view_id:
        params["view_id"] = view_id
    own = client is None
    c = client or httpx.AsyncClient(timeout=5.0)
    try:
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        r.raise_for_status()
        body = r.json()
    finally:
        if own:
            await c.aclose()
    if body.get("code") != 0:
        raise RuntimeError(f"feishu bitable error: {body}")
    return body.get("data", {}).get("items", [])
