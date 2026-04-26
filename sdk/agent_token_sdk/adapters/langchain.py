"""LangChain tool factory (方案-SDK §8.2).

``ctx_provider`` returns the per-call context (``user_token``, ``plan_id``, …)
so the tool interface stays a plain ``(action, resource, params)`` signature for
the LLM. If ``langchain_core`` is present we decorate with ``@tool``; otherwise
we return a vanilla callable with a ``.name`` / ``.description`` attached, which
remains useful for tests and non-LangChain hosts.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from ..client import AgentClient


def make_a2a_tool(
    client: AgentClient,
    target: str,
    description: str,
    ctx_provider: Callable[[], dict[str, Any]],
) -> Any:
    async def _call(action: str, resource: str, params: dict[str, Any] | None = None) -> str:
        ctx = ctx_provider()
        res = await client.invoke(
            target=target,
            intent={"action": action, "resource": resource, "params": params or {}},
            **ctx,
        )
        return json.dumps(res.get("data", res))

    try:  # pragma: no cover — only when langchain_core is installed
        from langchain_core.tools import tool

        decorated = tool(description=description)(_call)
        decorated.name = f"call_{target}"
        return decorated
    except Exception:
        _call.name = f"call_{target}"  # type: ignore[attr-defined]
        _call.description = description  # type: ignore[attr-defined]
        return _call
