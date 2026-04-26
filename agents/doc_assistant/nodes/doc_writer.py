"""doc_writer node: call Feishu docx OpenAPI via DocAssistant's own write token."""
from __future__ import annotations

from typing import Any

import httpx

from agents.common.logging import get_logger
from agents.data_agent.feishu.oauth import FeishuOAuth

_log = get_logger("agents.doc_assistant.doc_writer")


async def _create_and_write(
    *,
    base: str,
    token: str,
    title: str,
    blocks: list[dict],
    client: httpx.AsyncClient | None = None,
) -> dict:
    own = client is None
    c = client or httpx.AsyncClient(timeout=10.0)
    try:
        r = await c.post(
            f"{base.rstrip('/')}/open-apis/docx/v1/documents",
            json={"folder_token": "", "title": title},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        doc_id = r.json()["data"]["document"]["document_id"]
        r2 = await c.post(
            f"{base.rstrip('/')}/open-apis/docx/v1/documents/{doc_id}/blocks/batch_update",
            json={"requests": blocks},
            headers={"Authorization": f"Bearer {token}"},
        )
        r2.raise_for_status()
        return {"document_id": doc_id, "url": f"https://feishu.cn/docx/{doc_id}"}
    finally:
        if own:
            await c.aclose()


async def doc_writer_node(state: dict[str, Any]) -> dict[str, Any]:
    base = state["feishu_base"]
    oauth: FeishuOAuth = state.get("feishu_oauth") or FeishuOAuth(base=base)
    factory = state.get("client_factory") or (lambda: httpx.AsyncClient(timeout=10.0))
    title = next(
        (t.get("params", {}).get("title", "Auto Report")
         for t in state["dag"] if t.get("action") == "feishu.doc.write"),
        "Auto Report",
    )
    async with factory() as c:
        token = await oauth.get_tenant_token(client=c)
        out = await _create_and_write(
            base=base, token=token, title=title, blocks=state.get("blocks") or [], client=c,
        )
    return {**state, "doc": out}
