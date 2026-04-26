"""Dispatcher node: topo-sort DAG, fan-out via SDK."""
from __future__ import annotations

import asyncio
from typing import Any

from agents.common.logging import get_logger

_log = get_logger("agents.doc_assistant.dispatcher")


def _topo_layers(tasks: list[dict]) -> list[list[dict]]:
    by_id = {t["id"]: t for t in tasks}
    remaining = set(by_id)
    done: set[str] = set()
    layers: list[list[dict]] = []
    while remaining:
        layer = [
            by_id[i] for i in remaining
            if all(d in done for d in (by_id[i].get("deps") or []))
        ]
        if not layer:
            raise ValueError("cycle in DAG")
        layers.append(layer)
        for t in layer:
            remaining.discard(t["id"])
            done.add(t["id"])
    return layers


class DispatchFailed(RuntimeError):
    def __init__(self, task_id: str, cause: BaseException):
        self.task_id = task_id
        self.cause = cause
        super().__init__(f"task {task_id} failed: {cause}")


async def dispatcher_node(state: dict[str, Any]) -> dict[str, Any]:
    sdk = state["sdk"]
    dag = state["dag"]
    results: dict[str, Any] = {}
    for layer in _topo_layers(dag):
        coros = []
        ids = []
        for t in layer:
            # The final doc_writer step is handled by doc_writer_node.
            if t["agent"] == "doc_assistant":
                continue
            ids.append(t["id"])
            coros.append(sdk.invoke(
                target_agent=t["agent"],
                intent={
                    "action": t["action"],
                    "resource": t["resource"],
                    "params": t.get("params") or {},
                },
                trace_id=state["trace_id"],
                plan_id=state["plan_id"],
                task_id=t["id"],
            ))
        if not coros:
            continue
        outs = await asyncio.gather(*coros, return_exceptions=True)
        for tid, out in zip(ids, outs):
            if isinstance(out, BaseException):
                raise DispatchFailed(tid, out)
            results[tid] = out
    return {**state, "results": results}
