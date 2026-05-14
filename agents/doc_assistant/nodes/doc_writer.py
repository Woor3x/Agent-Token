"""doc_writer node: persist the synthesized doc.

Three back-ends coexist, picked from ``DOC_STORAGE``:

* ``feishu`` (default) — real Open Platform. The synthesizer's block list is
  converted via :mod:`_feishu_blocks` and written to a new Docx under the
  user-configured cloud folder (``FEISHU_DOCX_FOLDER_TOKEN``). A local cache
  of the same blocks is *also* persisted under :mod:`agents.doc_assistant.
  storage` keyed by the Feishu ``document_id`` so the Web UI can render an
  in-app preview without needing user-scope OAuth, while still surfacing the
  canonical ``https://feishu.cn/docx/{id}`` URL as a jump link.
* ``feishu-mock`` (when ``FEISHU_BASE`` looks like a mock host) — legacy
  ``/blocks/batch_update`` shape, kept so in-process ASGI tests pass.
* ``local`` — JSON file only, no Feishu round-trip. Keyed by
  ``doc_local_<ulid>``. Useful for unit tests and offline development; the
  Web UI falls back to the same ``GET /docs/{id}`` preview path.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from agents.common.logging import get_logger
from agents.data_agent.feishu.oauth import FeishuOAuth

from .. import storage
from ._feishu_blocks import to_feishu_children

_log = get_logger("agents.doc_assistant.doc_writer")

_MOCK_HOST_TOKENS = ("feishu-mock", "testserver", "127.0.0.1", "localhost")
# Feishu sometimes hands back a doc whose root block isn't yet writable for a
# few hundred ms. A short pre-flight sleep avoids the spurious 400 that
# otherwise surfaces from the very first ``children`` POST.
_POST_CREATE_RACE_MS = 250


def _is_mock_host(base: str) -> bool:
    h = base.lower()
    return any(t in h for t in _MOCK_HOST_TOKENS)


def _doc_url(base: str, doc_id: str) -> str:
    # Best-effort tenant URL. ``base`` looks like https://open.feishu.cn → flip
    # the subdomain to the tenant subdomain for a clickable link, but fall
    # back to the canonical feishu.cn host when we can't tell.
    if "feishu.cn" in base or "larksuite.com" in base:
        return f"https://feishu.cn/docx/{doc_id}"
    return f"{base.rstrip('/')}/docx/{doc_id}"


async def _create_doc(
    *,
    base: str,
    token: str,
    title: str,
    folder_token: str,
    client: httpx.AsyncClient,
) -> str:
    body: dict[str, Any] = {"title": title}
    # Empty folder_token defaults to the app's own root drive — only safe
    # against the mock; real Feishu rejects writes there for tenant tokens.
    if folder_token:
        body["folder_token"] = folder_token
    r = await client.post(
        f"{base.rstrip('/')}/open-apis/docx/v1/documents",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()["data"]["document"]["document_id"]


async def _append_blocks_mock(
    *,
    base: str,
    token: str,
    doc_id: str,
    blocks: list[dict],
    client: httpx.AsyncClient,
) -> None:
    r = await client.post(
        f"{base.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/blocks/batch_update",
        json={"requests": blocks},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()


async def _append_blocks_real(
    *,
    base: str,
    token: str,
    doc_id: str,
    blocks: list[dict],
    client: httpx.AsyncClient,
) -> None:
    # Root block id of a Docx is the document_id itself.
    children = to_feishu_children(blocks)
    if not children:
        return
    r = await client.post(
        f"{base.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
        json={"children": children, "index": -1},
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code != 200:
        # Surface the upstream body once so smoke / debug can see what Feishu
        # disliked. Production callers would translate to ``FeishuError``.
        _log.warning(
            "feishu docx children POST failed status=%s body=%s",
            r.status_code, r.text[:300],
        )
        r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"feishu docx children non-zero: {body}")


async def _create_and_write(
    *,
    base: str,
    token: str,
    title: str,
    blocks: list[dict],
    folder_token: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict:
    own = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        doc_id = await _create_doc(
            base=base, token=token, title=title,
            folder_token=folder_token, client=c,
        )
        if _is_mock_host(base):
            await _append_blocks_mock(
                base=base, token=token, doc_id=doc_id, blocks=blocks, client=c,
            )
        else:
            await asyncio.sleep(_POST_CREATE_RACE_MS / 1000.0)
            await _append_blocks_real(
                base=base, token=token, doc_id=doc_id, blocks=blocks, client=c,
            )
        return {"document_id": doc_id, "url": _doc_url(base, doc_id)}
    finally:
        if own:
            await c.aclose()


async def doc_writer_node(state: dict[str, Any]) -> dict[str, Any]:
    # Prefer the synthesizer-derived title (LLM picks one from the user prompt);
    # fall back to whatever the planner stuffed into the doc.write task params;
    # final fallback keeps a sane Chinese default.
    title = (state.get("doc_title") or "").strip() or next(
        (t.get("params", {}).get("title", "执行报告")
         for t in state["dag"] if t.get("action") == "feishu.doc.write"),
        "执行报告",
    )
    blocks = state.get("blocks") or []

    # Per-request override > env. Default branches to Feishu cloud so the
    # generated report lands in the user's configured Docx folder. A local
    # cache mirrors the blocks for the front-end's in-app preview path.
    storage_mode = (state.get("doc_storage") or os.environ.get("DOC_STORAGE", "feishu")).lower()
    if storage_mode == "local":
        record = storage.save(title=title, blocks=blocks)
        return {
            **state,
            "doc": {
                "document_id": record["document_id"],
                "url": f"/docs/{record['document_id']}",
                "storage": "local",
                # Surface title + block count so chat UI can render a useful
                # summary line instead of falling back to "任务完成（无文档输出）".
                "title": title,
                "block_count": len(blocks),
            },
        }

    base = state["feishu_base"]
    oauth: FeishuOAuth = state.get("feishu_oauth") or FeishuOAuth(base=base)
    factory = state.get("client_factory") or (lambda: httpx.AsyncClient(timeout=10.0))
    folder_token = state.get("feishu_folder_token") or os.environ.get(
        "FEISHU_DOCX_FOLDER_TOKEN", ""
    )
    async with factory() as c:
        token = await oauth.get_tenant_token(client=c)
        out = await _create_and_write(
            base=base,
            token=token,
            title=title,
            blocks=blocks,
            folder_token=folder_token,
            client=c,
        )
    # Dual-write: cache blocks locally under the Feishu doc_id so the Web UI
    # can render an in-app preview without needing user-scope OAuth on Feishu.
    # If the cache write fails (disk full, permission), keep the Feishu URL
    # in the response — the user can still jump to the cloud doc directly.
    try:
        storage.save(
            title=title,
            blocks=blocks,
            doc_id=out["document_id"],
            url=out.get("url"),
            storage="feishu",
        )
    except OSError as exc:
        _log.warning("local preview cache write failed doc_id=%s err=%s", out["document_id"], exc)
    out["storage"] = "feishu"
    out["title"] = title
    out["block_count"] = len(blocks)
    return {**state, "doc": out}
