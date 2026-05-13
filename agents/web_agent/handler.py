"""WebAgent business handler (see 方案-Agents §6.2)."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from agents.common.auth import VerifiedClaims
from agents.common.capability import Capability
from agents.common.logging import get_logger

from .search import client as search_client
from .search.fetcher import (
    FetchBlocked,
    _extract_text,
    http_fetch,
    summarize,
    summarize_with_llm,
)


def _sanitize_hit(h: dict) -> dict:
    """Run search-result snippet/title through the same sanitiser used on
    fetched HTML so upstream backends (Tavily, etc.) can't leak mojibake or
    inline JS into the synthesizer prompt.
    """
    out = dict(h or {})
    title = (out.get("title") or "").strip()
    snippet = (out.get("snippet") or "").strip()
    if title:
        out["title"] = _extract_text(title, max_chars=200)
    if snippet:
        out["snippet"] = _extract_text(snippet, max_chars=600)
    return out

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
            hits = [_sanitize_hit(h) for h in hits]
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
            except (httpx.HTTPError, OSError) as e:
                # Transient network failures (ConnectTimeout, RemoteProtocolError,
                # DNS issues, TLS reset, …) on auto-expanded fetch tasks must NOT
                # abort the entire DAG — the synthesizer can still produce a
                # report from the search hits. Return a degraded record so the
                # caller can surface "fetch failed" without raising.
                _log.warning(
                    f"web.fetch failed url={resource} err={type(e).__name__}: {e}"
                )
                return {
                    "url": resource,
                    "summary": "",
                    "length": 0,
                    "error": f"{type(e).__name__}: {e}"[:200],
                }
            text = await asyncio.get_running_loop().run_in_executor(
                None, _extract_text, raw
            )
            query = params.get("query")
            # 2c: LLM-synthesized summary, raw text never returned/stored.
            summary = await summarize_with_llm(text, query=query)
            return {"url": resource, "summary": summary, "length": len(text)}
        raise ValueError(f"unsupported action: {action}")
