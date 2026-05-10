"""Topology-aware ASCII flow renderer for the synthesizer.

Renders the executed DAG as a layered, monospace flow diagram suitable for
embedding in a Feishu Docx code block. The renderer is deterministic — same
DAG always produces the same string — so doc snapshots stay stable in tests.

Each task becomes a one-line entry::

    [t1] web_agent.web.search        query="XSS attack types"

Branching tasks (multiple downstream deps in the same layer) are joined with
unicode arrows. Empty layers are skipped.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_HEAD = "执行流程"
_RULE = "─" * 56


def _topo_layers(dag: list[dict]) -> list[list[dict]]:
    """Kahn-style layered topo sort. Mirrors dispatcher's notion of layers
    so the rendered flow matches the actual execution order.
    """
    by_id = {t["id"]: t for t in dag}
    remaining = set(by_id)
    done: set[str] = set()
    layers: list[list[dict]] = []
    while remaining:
        layer = [
            by_id[i] for i in remaining
            if all(d in done for d in (by_id[i].get("deps") or []))
        ]
        if not layer:
            # Cycle / dangling dep — render whatever is left as one final layer
            # rather than blowing up: the diagram is informational, not load-
            # bearing.
            layers.append([by_id[i] for i in remaining])
            break
        layers.append(layer)
        for t in layer:
            remaining.discard(t["id"])
            done.add(t["id"])
    return layers


def _short_label(task: dict[str, Any]) -> str:
    action = task.get("action", "")
    params = task.get("params") or {}
    res = str(task.get("resource") or "")
    if action == "web.search":
        q = str(params.get("query") or "").strip()
        top_k = params.get("fetch_top_k")
        bits = []
        if q:
            bits.append(f'query="{q[:40]}"')
        if top_k:
            bits.append(f"fetch_top_k={top_k}")
        return "  ".join(bits)
    if action == "web.fetch":
        host = ""
        try:
            host = urlparse(res).hostname or ""
        except Exception:
            host = ""
        return host or res[:40]
    if action.startswith("feishu.") and res:
        return res[:48]
    return res[:48]


def _format_task(task: dict[str, Any]) -> str:
    tid = task.get("id", "?")
    agent = task.get("agent", "?")
    action = task.get("action", "?")
    label = _short_label(task)
    head = f"[{tid}] {agent}.{action}"
    if label:
        return f"{head}  {label}"
    return head


def render_dag_ascii(dag: list[dict]) -> str:
    """Render the DAG as a layered ASCII flow diagram.

    Returns a multi-line string. Empty DAG → empty string.
    """
    if not dag:
        return ""
    layers = _topo_layers(dag)
    if not layers:
        return ""

    lines: list[str] = [_HEAD, _RULE]
    for li, layer in enumerate(layers):
        if li > 0:
            lines.append("    │")
            lines.append("    ▼")
        if len(layer) == 1:
            lines.append("  " + _format_task(layer[0]))
        else:
            # Fan-out: list each parallel task with a branch arrow.
            for t in layer:
                lines.append("  ├─▶ " + _format_task(t))
    lines.append(_RULE)
    return "\n".join(lines)


def should_render(dag: list[dict], mode: str) -> bool:
    """Decide whether to emit the ASCII flow.

    ``mode`` ∈ ``{auto, always, off}``. ``auto`` requires at least 2 non-
    ``doc.write`` tasks so single-task plans don't get a useless one-row chart.
    """
    m = (mode or "auto").lower()
    if m == "off":
        return False
    if m == "always":
        return bool(dag)
    # auto
    non_writers = [t for t in dag if t.get("action") != "feishu.doc.write"]
    return len(non_writers) >= 2
