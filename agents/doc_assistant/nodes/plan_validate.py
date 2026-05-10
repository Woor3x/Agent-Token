"""Plan validation node — calls IdP /plan/validate when wired, else local check."""
from __future__ import annotations

import os
from typing import Any

import httpx

from agents.common.logging import get_logger

from .planner import validate_dag

_log = get_logger("agents.doc_assistant.plan_validate")


def _dag_to_task_specs(dag: list[dict], orchestrator_id: str, user_id: str) -> list[dict]:
    """Convert planner DAG entries to IdP TaskSpec format."""
    specs = []
    for t in dag:
        action = t.get("action", "")
        resource = t.get("resource", "*")
        specs.append({
            "task_id": t["id"],
            "orchestrator_id": orchestrator_id,
            "callee_id": t["agent"],
            "user_id": user_id,
            "scope": f"{action}:{resource}",
            "audience": f"agent:{t['agent']}",
            "resource": resource,
            "purpose": "orchestrate",
        })
    return specs


async def plan_validate_node(state: dict[str, Any]) -> dict[str, Any]:
    dag = state.get("dag") or []
    validate_dag(dag)  # local structural check always runs

    idp_url = os.environ.get("IDP_PLAN_VALIDATE_URL", "")
    if not idp_url:
        _log.info("skipping remote /plan/validate (unset)")
        return state

    user_sub = state.get("user_sub", "user:unknown")
    task_specs = _dag_to_task_specs(dag, orchestrator_id="doc_assistant", user_id=user_sub)

    try:  # pragma: no cover — live branch
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(
                idp_url,
                json={
                    "plan_id": state.get("plan_id"),
                    "trace_id": state.get("trace_id"),
                    "tasks": task_specs,
                },
            )
            r.raise_for_status()
            result = r.json()
            if result.get("overall") == "deny":
                _log.warning(f"plan pre-check denied: {result}")
    except Exception as e:  # pragma: no cover
        _log.warning(f"plan_validate unreachable: {e}")
    return state
