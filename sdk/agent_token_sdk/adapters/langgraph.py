"""LangGraph node factory (方案-SDK §8.1).

The returned coroutine has the shape LangGraph expects:
``async def node(state: dict) -> dict`` — so callers can
``graph.add_node("call_data", make_a2a_node(client, "data_agent"))``. It reads
``intent`` / ``user_token`` / ``purpose`` / ``plan_id`` / ``task_id`` /
``trace_id`` from ``state`` and writes the agent response under
``state["a2a_result"]``.

Works standalone — this module never imports ``langgraph`` itself.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..client import AgentClient

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def make_a2a_node(client: AgentClient, target: str) -> NodeFn:
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        res = await client.invoke(
            target=target,
            intent=state["intent"],
            on_behalf_of=state["user_token"],
            purpose=state.get("purpose", ""),
            plan_id=state.get("plan_id"),
            task_id=state.get("task_id"),
            trace_id=state.get("trace_id"),
        )
        return {**state, "a2a_result": res.get("data", res)}

    return node
