"""Feishu calendar read client."""
from __future__ import annotations

import httpx


async def list_events(
    *,
    base: str,
    token: str,
    cal_id: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    url = f"{base.rstrip('/')}/open-apis/calendar/v4/calendars/{cal_id}/events"
    own = client is None
    c = client or httpx.AsyncClient(timeout=5.0)
    try:
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        body = r.json()
    finally:
        if own:
            await c.aclose()
    if body.get("code") != 0:
        raise RuntimeError(f"feishu calendar error: {body}")
    return body.get("data", {}).get("items", [])
