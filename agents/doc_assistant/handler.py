"""DocAssistant handler: orchestrate or direct doc.write."""
from __future__ import annotations

from typing import Any

from agents.common.auth import VerifiedClaims
from agents.common.capability import Capability
from agents.common.llm import LLMProvider
from agents.common.logging import get_logger
from agents.common.ulid import new_ulid
from agents.data_agent.feishu.oauth import FeishuOAuth

import httpx

from .graph import run_graph
from .sdk import AsgiSdkClient

_log = get_logger("agents.doc_assistant")


class DocAssistantHandler:
    """Orchestrator entry invoked by AgentServer."""

    def __init__(
        self,
        *,
        feishu_base: str,
        peer_apps: dict[str, Any],
        client_factory: Any | None = None,
        llm: LLMProvider | None = None,
    ) -> None:
        self._feishu_base = feishu_base
        self._peer_apps = peer_apps
        self._oauth = FeishuOAuth(base=feishu_base)
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10.0))
        self._llm = llm

    async def __call__(
        self, body: dict, claims: VerifiedClaims, cap: Capability
    ) -> dict[str, Any]:
        intent = body.get("intent") or {}
        action = intent.get("action")
        resource = intent.get("resource") or ""

        cap_item = cap.find(action, resource)
        if cap_item is None:
            raise PermissionError(f"capability miss: {action} {resource}")

        if action == "orchestrate":
            sdk = AsgiSdkClient(apps=self._peer_apps, user_sub=claims.sub)
            state: dict[str, Any] = {
                "user_prompt": (intent.get("params") or {}).get("prompt", ""),
                "user_token": claims.raw,
                "trace_id": claims.trace_id or new_ulid(),
                "plan_id": claims.plan_id or new_ulid(),
                "sdk": sdk,
                "feishu_base": self._feishu_base,
                "feishu_oauth": self._oauth,
                "client_factory": self._client_factory,
                "llm": self._llm,
            }
            final = await run_graph(state)
            return {
                "plan_id": final["plan_id"],
                "dag": final["dag"],
                "results": final.get("results", {}),
                "doc": final.get("doc"),
            }
        if action == "feishu.doc.write":
            blocks = (intent.get("params") or {}).get("blocks") or []
            title = (intent.get("params") or {}).get("title", "Doc")
            from .nodes.doc_writer import _create_and_write

            async with self._client_factory() as c:
                token = await self._oauth.get_tenant_token(client=c)
                return await _create_and_write(
                    base=self._feishu_base, token=token, title=title, blocks=blocks, client=c,
                )
        raise ValueError(f"unsupported action: {action}")
