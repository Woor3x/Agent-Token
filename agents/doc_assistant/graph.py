"""LangGraph state machine for DocAssistant.

Falls back to a hand-rolled linear driver when ``langgraph`` is unavailable —
same node signatures, same order (planner → plan_validate → dispatcher →
synthesizer → doc_writer), so behavior is identical.
"""
from __future__ import annotations

from typing import Any

from .nodes.dispatcher import dispatcher_node
from .nodes.doc_writer import doc_writer_node
from .nodes.plan_validate import plan_validate_node
from .nodes.planner import planner_node
from .nodes.synthesizer import synthesizer_node

_NODES = [
    planner_node,
    plan_validate_node,
    dispatcher_node,
    synthesizer_node,
    doc_writer_node,
]


async def run_graph(state: dict[str, Any]) -> dict[str, Any]:
    try:
        from langgraph.graph import END, StateGraph  # pragma: no cover
    except Exception:
        # Linear fallback (covers demo + tests without langgraph installed).
        cur = state
        for node in _NODES:
            cur = await node(cur)
        return cur

    # pragma: no cover — only when langgraph is present.
    graph = StateGraph(dict)
    graph.add_node("planner", planner_node)
    graph.add_node("plan_validate", plan_validate_node)
    graph.add_node("dispatcher", dispatcher_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("doc_writer", doc_writer_node)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "plan_validate")
    graph.add_edge("plan_validate", "dispatcher")
    graph.add_edge("dispatcher", "synthesizer")
    graph.add_edge("synthesizer", "doc_writer")
    graph.add_edge("doc_writer", END)
    compiled = graph.compile()
    return await compiled.ainvoke(state)
