"""Feishu drive listing — used by the UI's file picker.

Two read shapes:

* :func:`list_files` — lists the children of a folder (defaults to the app's
  root drive when no token is provided). Filters to type=``bitable`` so the UI
  shows only Multi-Dimensional Tables that the report pipeline can analyse.
  Real Feishu endpoint: ``GET /open-apis/drive/v1/files``.
* :func:`list_bitable_tables` — given an ``app_token``, list the tables inside
  the bitable so the user can pick which sheet to analyse.
  Real Feishu endpoint: ``GET /open-apis/bitable/v1/apps/{app_token}/tables``.
"""
from __future__ import annotations

import httpx

from ._http import parse_or_raise, request_with_retry

_FILES_ENDPOINT = "/open-apis/drive/v1/files"
_TABLES_ENDPOINT = "/open-apis/bitable/v1/apps/{app_token}/tables"


async def list_files(
    *,
    base: str,
    token: str,
    folder_token: str | None = None,
    file_type: str = "bitable",
    page_size: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Return a flat list of drive children (one page).

    Each item exposes ``token``, ``name``, ``type`` and ``modified_time``
    keys when supplied by upstream — defensively defaulted on omission so
    the front-end never sees ``undefined``.
    """
    url = f"{base.rstrip('/')}{_FILES_ENDPOINT}"
    own = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        params: dict = {"page_size": page_size}
        if folder_token:
            params["folder_token"] = folder_token
        r = await request_with_retry(
            c, "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params
        )
        body = parse_or_raise(r, endpoint=_FILES_ENDPOINT)
        items = (body.get("data") or {}).get("files") or []
    finally:
        if own:
            await c.aclose()
    out: list[dict] = []
    # Keep folders alongside the wanted file_type(s) so the UI can let users
    # drill down into subfolders (Feishu drive list is non-recursive). Pass
    # ``file_type=""`` or ``file_type="any"`` to skip filtering entirely.
    # Multi-type filtering: comma-separated list, e.g. ``bitable,docx``.
    if not file_type or file_type == "any":
        allowed = None
    else:
        allowed = {t.strip() for t in file_type.split(",") if t.strip()} | {"folder"}
    for it in items:
        if allowed is not None and it.get("type") not in allowed:
            continue
        out.append(
            {
                "token": it.get("token", ""),
                "name": it.get("name", ""),
                "type": it.get("type", ""),
                "url": it.get("url", ""),
                "modified_time": it.get("modified_time", 0),
            }
        )
    return out


async def list_bitable_tables(
    *,
    base: str,
    token: str,
    app_token: str,
    page_size: int = 50,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """List tables (a.k.a. sheets) inside a bitable app."""
    path = _TABLES_ENDPOINT.format(app_token=app_token)
    url = f"{base.rstrip('/')}{path}"
    own = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        r = await request_with_retry(
            c,
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": page_size},
        )
        body = parse_or_raise(r, endpoint=path)
        items = (body.get("data") or {}).get("items") or []
    finally:
        if own:
            await c.aclose()
    return [
        {"table_id": it.get("table_id", ""), "name": it.get("name", "")}
        for it in items
    ]
