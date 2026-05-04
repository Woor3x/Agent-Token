"""Feishu contact read client."""
from __future__ import annotations

import httpx

from ._http import parse_or_raise, request_with_retry

_ENDPOINT = "/open-apis/contact/v3/departments/{dept_id}/users"


async def list_users(
    *,
    base: str,
    token: str,
    dept_id: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    path = _ENDPOINT.format(dept_id=dept_id)
    url = f"{base.rstrip('/')}{path}"
    own = client is None
    c = client or httpx.AsyncClient(timeout=5.0)
    try:
        r = await request_with_retry(
            c, "GET", url, headers={"Authorization": f"Bearer {token}"}
        )
        body = parse_or_raise(r, endpoint=path)
    finally:
        if own:
            await c.aclose()
    return (body.get("data") or {}).get("items") or []
