"""LangChain tool factory (方案-SDK §8.2).

When ``langchain_core`` is installed we build a ``StructuredTool`` via
``StructuredTool.from_function`` with an explicit ``args_schema`` so the tool
is fully compatible with LangChain ``AgentExecutor`` / ReAct loops:

* avoids ``BaseTool`` pydantic-v2 frozen-field traps from late ``setattr``;
* gives the LLM a precise schema (``action`` / ``resource`` / ``params``)
  instead of relying on type-hint inference;
* registers the coroutine via the ``coroutine=`` kwarg so async dispatch
  works through ``ainvoke`` / ``arun``.

When ``langchain_core`` is absent we fall back to a vanilla callable with
``.name`` / ``.description`` attached — still drivable by tests and
non-LangChain hosts. The caller-facing ``(action, resource, params)``
signature is identical across both paths.

``ctx_provider`` returns per-call context (``on_behalf_of`` / ``purpose`` /
``plan_id`` / ``task_id`` / ``trace_id`` / ``idempotency_key``) — kept out of
the tool signature so the LLM does not have to fabricate it.
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
        from langchain_core.tools import StructuredTool
        from pydantic import BaseModel, Field

        class _A2AArgs(BaseModel):
            action: str = Field(
                description=(
                    "Action name from the target agent's capability, "
                    "e.g. 'feishu.bitable.read' or 'web.search'."
                )
            )
            resource: str = Field(
                description=(
                    "Resource identifier matching the action's capability pattern, "
                    "e.g. 'app_token:xxx/table:yyy' or an https URL."
                )
            )
            params: dict[str, Any] = Field(
                default_factory=dict,
                description="Optional action-specific parameters.",
            )

        return StructuredTool.from_function(
            coroutine=_call,
            name=f"call_{target}",
            description=description,
            args_schema=_A2AArgs,
        )
    except Exception:
        _call.name = f"call_{target}"  # type: ignore[attr-defined]
        _call.description = description  # type: ignore[attr-defined]
        return _call
