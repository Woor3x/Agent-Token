"""Plan validation node — calls IdP /plan/validate when wired, else local check."""
from __future__ import annotations

import os
from typing import Any

import httpx

from agents.common.logging import get_logger

from .planner import validate_dag

_log = get_logger("agents.doc_assistant.plan_validate")


async def plan_validate_node(state: dict[str, Any]) -> dict[str, Any]:
    dag = state.get("dag") or []
    validate_dag(dag)  # local structural check always runs

    idp_url = os.environ.get("IDP_PLAN_VALIDATE_URL", "")
    if not idp_url:
        _log.info("skipping remote /plan/validate (unset)")
        return state
    try:  # pragma: no cover — live branch
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.post(idp_url, json={"plan_id": state.get("plan_id"), "tasks": dag})
            r.raise_for_status()
    except Exception as e:  # pragma: no cover
        _log.warning(f"plan_validate unreachable: {e}")
    return state
