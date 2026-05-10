"""Dispatcher node: topo-sort DAG, fan-out via SDK.

After each ``web.search`` task completes, the dispatcher dynamically appends
``web.fetch`` tasks for the top-N hits (controlled by
``params.fetch_top_k`` on the search task, default 0 = disabled). New fetch
ids are injected into the deps of every not-yet-dispatched task that already
depended on the search task — so the doc-writer layer waits for fetches to
finish before synthesizing.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlparse

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


def _safe_https(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme == "https" and bool(p.hostname)


def _extract_search_hits(out: Any) -> list[dict]:
    """Pull ``results`` array out of a web.search SDK response.

    SDK response shape varies (envelope or raw); accept both ``{data: {...}}``
    and ``{results: [...]}``.
    """
    if not isinstance(out, dict):
        return []
    data = out.get("data") if isinstance(out.get("data"), dict) else out
    hits = data.get("results") if isinstance(data, dict) else None
    return hits if isinstance(hits, list) else []


def _build_fetch_tasks(
    *,
    search_task: dict,
    hits: list[dict],
    top_k: int,
    next_id_seed: int,
    query: str | None,
) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    n = next_id_seed
    for h in hits:
        if len(out) >= top_k:
            break
        url = (h or {}).get("url") or ""
        if not url or url in seen or not _safe_https(url):
            continue
        seen.add(url)
        out.append({
            "id": f"t{n}",
            "agent": "web_agent",
            "action": "web.fetch",
            "resource": url,
            "params": {"query": query} if query else {},
            "deps": [search_task["id"]],
            "_auto_expanded": True,
        })
        n += 1
    return out


def _next_id_seed(dag: list[dict]) -> int:
    mx = 0
    for t in dag:
        tid = (t.get("id") or "").lstrip("t")
        if tid.isdigit():
            mx = max(mx, int(tid))
    return mx + 1


async def dispatcher_node(state: dict[str, Any]) -> dict[str, Any]:
    sdk = state["sdk"]
    dag = state["dag"]
    # Default fetch_top_k: applied when a web.search task does not declare
    # ``params.fetch_top_k``. 2 = "search + 2 fetch + LLM synth" out of the
    # box. Set to 0 in CI/cost-sensitive envs to disable auto fetch.
    default_top_k = int(os.environ.get("WEB_AUTO_FETCH_TOP_K", "2"))

    results: dict[str, Any] = {}
    dispatched: set[str] = set()
    while True:
        layers = _topo_layers(dag)
        # Pick the first layer with at least one undispatched, non-doc_assistant task.
        next_layer: list[dict] | None = None
        for layer in layers:
            pending = [
                t for t in layer
                if t["id"] not in dispatched and t["agent"] != "doc_assistant"
            ]
            if pending:
                next_layer = pending
                break
            # Mark doc_assistant tasks as dispatched so we move on; doc_writer
            # node handles them downstream.
            for t in layer:
                if t["agent"] == "doc_assistant":
                    dispatched.add(t["id"])
        if not next_layer:
            break

        coros = []
        ids = []
        for t in next_layer:
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
        outs = await asyncio.gather(*coros, return_exceptions=True)

        for tid, out in zip(ids, outs):
            if isinstance(out, BaseException):
                raise DispatchFailed(tid, out)
            results[tid] = out
            dispatched.add(tid)

        # Dynamic expansion: for each web.search task in this layer, append
        # web.fetch tasks for top-N hits and patch downstream deps.
        for t in next_layer:
            if t.get("action") != "web.search":
                continue
            params = t.get("params") or {}
            top_k = int(params.get("fetch_top_k", default_top_k))
            if top_k <= 0:
                continue
            hits = _extract_search_hits(results.get(t["id"]))
            if not hits:
                continue
            seed = _next_id_seed(dag)
            new_tasks = _build_fetch_tasks(
                search_task=t,
                hits=hits,
                top_k=top_k,
                next_id_seed=seed,
                query=params.get("query"),
            )
            if not new_tasks:
                continue
            new_ids = [nt["id"] for nt in new_tasks]
            # Inject new fetch ids into deps of every not-yet-dispatched task
            # that already depended on this search task — keeps the doc.write
            # layer blocked until fetches complete.
            for downstream in dag:
                if downstream["id"] in dispatched:
                    continue
                deps = downstream.get("deps") or []
                if t["id"] in deps:
                    downstream["deps"] = list(deps) + new_ids
            dag.extend(new_tasks)
            _log.info(
                f"dispatcher.expand search={t['id']} +{len(new_tasks)} fetch tasks "
                f"(top_k={top_k})"
            )

    return {**state, "dag": dag, "results": results}
