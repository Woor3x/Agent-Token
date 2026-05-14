"""LangGraph node factory (方案-SDK §8.1).

The returned coroutine has the shape LangGraph expects:
``async def node(state: dict) -> dict`` — so callers can
``graph.add_node("call_data", make_a2a_node(client, "data_agent"))``.

State keys consumed (parity with the LangChain / AutoGen adapters):

* ``intent``           — ``{action, resource, params}`` (required)
* ``user_token``       — subject token to delegate on behalf of (required)
* ``purpose``          — free-form audit label (optional)
* ``plan_id``          — plan correlation id (optional)
* ``task_id``          — task correlation id (optional)
* ``trace_id``         — distributed trace id (optional)
* ``idempotency_key``  — replay-safety key forwarded to the gateway (optional)

On success the response payload is written under ``state["a2a_result"]``.
On :class:`agent_token_sdk.errors.A2AError` the error is captured into
``state["a2a_error"]`` as ``{"code", "message", "trace_id"}`` instead of
crashing the graph — the orchestrator can branch on its presence. Other
exceptions still propagate so unexpected bugs aren't silently swallowed.

Works standalone — this module never imports ``langgraph`` itself.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..client import AgentClient
from ..errors import A2AError

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def make_a2a_node(client: AgentClient, target: str) -> NodeFn:
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        try:
            res = await client.invoke(
                target=target,
                intent=state["intent"],
                on_behalf_of=state["user_token"],
                purpose=state.get("purpose", ""),
                plan_id=state.get("plan_id"),
                task_id=state.get("task_id"),
                trace_id=state.get("trace_id"),
                idempotency_key=state.get("idempotency_key"),
            )
        except A2AError as exc:
            return {
                **state,
                "a2a_error": {
                    "code": exc.code,
                    "message": exc.message,
                    "trace_id": exc.trace_id,
                },
            }
        return {**state, "a2a_result": res.get("data", res)}

    return node
