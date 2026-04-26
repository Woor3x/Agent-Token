"""WebAgent business handler (see 方案-Agents §6.2)."""
from __future__ import annotations

from typing import Any

from agents.common.auth import VerifiedClaims
from agents.common.capability import Capability
from agents.common.logging import get_logger

from .search import client as search_client
from .search.fetcher import FetchBlocked, _extract_text, http_fetch, summarize

_log = get_logger("agents.web_agent")


class WebAgentHandler:
    def __init__(self, *, allowlist: dict | None = None) -> None:
        self._allowlist = allowlist

    async def __call__(
        self, body: dict, claims: VerifiedClaims, cap: Capability
    ) -> dict[str, Any]:
        intent = body.get("intent") or {}
        action = intent.get("action")
        resource = intent.get("resource") or ""
        params = intent.get("params") or {}

        cap_item = cap.find(action, resource)
        if cap_item is None:
            raise PermissionError(f"capability miss: {action} {resource}")
        constraints = cap_item.constraints or {}

        if action == "web.search":
            q = params.get("query") or resource
            if not q or q == "*":
                raise ValueError("query required")
            max_results = min(
                int(params.get("max_results", 5)),
                int(constraints.get("max_results", 10)),
            )
            hits = await search_client.search(q, max_results=max_results)
            return {"query": q, "results": hits, "count": len(hits)}
        if action == "web.fetch":
            try:
                raw = await http_fetch(
                    resource,
                    timeout=int(constraints.get("timeout_ms", 5000)) / 1000,
                    max_size=int(constraints.get("max_size_kb", 512)) * 1024,
                    allowlist=self._allowlist,
                )
            except FetchBlocked as e:
                raise PermissionError(f"fetch_blocked:{e}") from e
            text = _extract_text(raw)
            return {"url": resource, "summary": summarize(text), "length": len(text)}
        raise ValueError(f"unsupported action: {action}")
