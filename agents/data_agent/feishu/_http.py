"""Shared HTTP helpers for Feishu OpenAPI clients.

Centralises the parts that every endpoint repeats: 429 / 5xx retry with
``Retry-After`` honouring, and turning a non-zero ``code`` business response
into a sanitised :class:`FeishuError`. Body content never appears in the
exception message — only ``code``/``status``/``msg[:120]`` + request id.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .errors import FeishuError

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> httpx.Response:
    """POST/GET/DELETE with exponential backoff on transient failures."""
    last_resp: httpx.Response | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = await client.request(
                method, url, headers=headers, params=params, json=json
            )
        except httpx.HTTPError as e:
            if attempt == max_attempts:
                raise FeishuError(
                    code=-1, msg=type(e).__name__, endpoint=url
                ) from e
            await asyncio.sleep(_backoff(attempt, base_delay))
            continue
        last_resp = r
        if r.status_code not in _RETRYABLE_STATUS or attempt == max_attempts:
            return r
        ra = r.headers.get("Retry-After")
        sleep_for = _retry_after(ra, attempt, base_delay)
        await asyncio.sleep(sleep_for)
    assert last_resp is not None
    return last_resp


def parse_or_raise(r: httpx.Response, *, endpoint: str) -> dict[str, Any]:
    try:
        body = r.json()
    except Exception:
        raise FeishuError(
            code=-1,
            msg=f"non-json body status={r.status_code}",
            status=r.status_code,
            endpoint=endpoint,
            request_id=r.headers.get("X-Request-Id"),
        )
    if not isinstance(body, dict):
        raise FeishuError(
            code=-1,
            msg=f"unexpected body type {type(body).__name__}",
            status=r.status_code,
            endpoint=endpoint,
            request_id=r.headers.get("X-Request-Id"),
        )
    if body.get("code") != 0:
        raise FeishuError(
            code=int(body.get("code", -1) or -1),
            msg=str(body.get("msg", ""))[:200],
            status=r.status_code,
            endpoint=endpoint,
            request_id=r.headers.get("X-Request-Id"),
        )
    return body


def _backoff(attempt: int, base: float) -> float:
    return min(base * (2 ** (attempt - 1)), 5.0)


def _retry_after(header: str | None, attempt: int, base: float) -> float:
    if header:
        try:
            return min(float(header), 8.0)
        except ValueError:
            pass
    return _backoff(attempt, base)
