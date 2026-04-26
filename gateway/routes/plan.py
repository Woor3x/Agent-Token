"""POST /a2a/plan/submit — DAG plan execution.
GET  /a2a/plan/{plan_id}/status — poll plan status.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import settings
from errors import UpstreamError, _error_body
from middleware.audit import audit_writer
from routing.registry import registry
from routing.upstream_client import call_upstream

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory plan store (replace with Redis for multi-instance)
_plans: dict[str, dict] = {}


@router.post("/a2a/plan/submit")
async def plan_submit(request: Request):
    body_bytes = await request.body()
    body = json.loads(body_bytes)

    plan_id = body.get("plan_id") or f"plan_{uuid.uuid4().hex[:12]}"
    tasks: list[dict] = body.get("tasks", [])
    context: dict = body.get("context", {})
    trace_id = getattr(request.state, "trace_id", "")
    claims: dict = request.state.token_claims

    _plans[plan_id] = {
        "plan_id": plan_id,
        "status": "running",
        "tasks": {t["id"]: {"id": t["id"], "status": "pending"} for t in tasks},
        "trace_id": trace_id,
        "submitted_at": time.time(),
    }

    asyncio.create_task(
        _execute_dag(request.app, plan_id, tasks, context, claims, trace_id)
    )

    return JSONResponse({"plan_id": plan_id, "trace_id": trace_id, "status": "running"})


@router.get("/a2a/plan/{plan_id}/status")
async def plan_status(plan_id: str, request: Request):
    plan = _plans.get(plan_id)
    if plan is None:
        from errors import GatewayError
        err = GatewayError("UPSTREAM_FAIL", f"plan not found: {plan_id}")
        err.http_status = 404
        return JSONResponse(status_code=404, content=_error_body(request, err))
    tasks_list = [
        {"id": t["id"], "status": t["status"], "result_ref": t.get("result_ref")}
        for t in plan["tasks"].values()
    ]
    return JSONResponse({
        "plan_id": plan_id,
        "status": plan["status"],
        "tasks": tasks_list,
    })


async def _execute_dag(
    app: Any,
    plan_id: str,
    tasks: list[dict],
    context: dict,
    claims: dict,
    trace_id: str,
) -> None:
    """Simple topological execution of DAG tasks (fan-out for independent tasks)."""
    plan = _plans[plan_id]

    # Build dependency map
    completed: set[str] = set()
    remaining = list(tasks)
    failed = False

    while remaining and not failed:
        # Collect tasks whose dependencies are satisfied
        ready = [
            t for t in remaining
            if set(t.get("depends_on", [])).issubset(completed)
        ]
        if not ready:
            logger.error("plan %s: deadlock — no ready tasks", plan_id)
            failed = True
            break

        # Fan-out ready tasks concurrently
        results = await asyncio.gather(
            *[_run_task(app, plan_id, t, claims, trace_id) for t in ready],
            return_exceptions=True,
        )

        for task, result in zip(ready, results):
            remaining.remove(task)
            tid = task["id"]
            if isinstance(result, Exception):
                plan["tasks"][tid]["status"] = "failed"
                plan["tasks"][tid]["error"] = str(result)
                logger.error("plan %s task %s failed: %s", plan_id, tid, result)
                failed = True
            else:
                plan["tasks"][tid]["status"] = "completed"
                plan["tasks"][tid]["result_ref"] = result
                completed.add(tid)

    plan["status"] = "failed" if failed else "completed"
    audit_writer.emit({
        "trace_id": trace_id,
        "plan_id": plan_id,
        "sub": claims.get("sub", ""),
        "action": "orchestrate",
        "resource": "*",
        "decision": "completed" if not failed else "failed",
    })


async def _run_task(
    app: Any,
    plan_id: str,
    task: dict,
    claims: dict,
    trace_id: str,
) -> str:
    """Execute a single DAG task against its target agent."""
    plan = _plans[plan_id]
    tid = task["id"]
    plan["tasks"][tid]["status"] = "running"

    agent_id = task.get("agent_id", "")
    cfg = registry.get(agent_id)
    upstream_url = f"{cfg.upstream.rstrip('/')}/a2a/task"

    payload = json.dumps({"task_id": tid, "plan_id": plan_id, **task}).encode()
    timeout = cfg.timeout_ms / 1000

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            upstream_url,
            content=payload,
            headers={
                "Content-Type": "application/json",
                "traceparent": f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01",
            },
        )
        r.raise_for_status()
        return r.json().get("result_ref", tid)
