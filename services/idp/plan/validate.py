import time
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.loader import get_agent_capability
from audit.writer import get_audit_writer
from config import settings
from errors import InvalidRequest, OpaUnavailable
from plan.opa_client import build_plan_input, query_opa_plan
from token_exchange.delegation import check_delegation, check_orchestrator_can_invoke
from token_exchange.executor import check_executor
from token_exchange.intent import extract_target_agent, parse_scope
from token_exchange.intersect import intersect
from users.perms import load_permissions

router = APIRouter()


class TaskSpec(BaseModel):
    task_id: str
    orchestrator_id: str
    callee_id: str
    user_id: str
    scope: str
    audience: str
    resource: Optional[str] = None
    purpose: Optional[str] = None


class PlanValidateRequest(BaseModel):
    plan_id: str
    trace_id: Optional[str] = None
    tasks: list[TaskSpec]


class TaskDecision(BaseModel):
    task_id: str
    decision: str
    deny_reasons: list[str] = []
    effective_scope: list[str] = []


class PlanValidateResponse(BaseModel):
    plan_id: str
    overall: str
    tasks: list[TaskDecision]


@router.post("/plan/validate", response_model=PlanValidateResponse)
async def validate_plan(request: Request, body: PlanValidateRequest):
    # ── Phase 1: IdP local checks for each task ──────────────────────────────
    # Collect per-task: local deny reasons, effective_scope, and OPA task dict.

    local_reasons: dict[str, list[str]] = {}   # task_id → [reason, ...]
    effective_scopes: dict[str, list[str]] = {} # task_id → [scope, ...]
    opa_tasks: list[dict] = []                  # built for the OPA batch call

    # Use the first task's orchestrator / user as the plan-level identity.
    # (All tasks in one plan belong to the same delegated request.)
    plan_orchestrator_id = body.tasks[0].orchestrator_id if body.tasks else ""
    plan_user_sub = body.tasks[0].user_id if body.tasks else ""
    orch_caps: list[dict] = []

    for task in body.tasks:
        reasons: list[str] = []
        effective_scope: list[str] = []
        action = ""
        resource_value = ""

        try:
            action, resource_value = parse_scope(task.scope)

            target_agent_id = extract_target_agent(task.audience)
            callee_cap = get_agent_capability(target_agent_id)
            if callee_cap is None:
                raise InvalidRequest(f"Unknown callee agent: {target_agent_id}")

            orch_cap = get_agent_capability(task.orchestrator_id)
            if orch_cap is None:
                raise InvalidRequest(f"Unknown orchestrator: {task.orchestrator_id}")

            # Capture orchestrator capabilities once (from first task).
            if not orch_caps:
                orch_caps = [
                    {"action": c.action, "resource_pattern": c.resource_pattern}
                    for c in orch_cap.capabilities
                ]

            check_orchestrator_can_invoke(orch_cap, target_agent_id)
            check_delegation(task.orchestrator_id, target_agent_id, callee_cap)
            check_executor(target_agent_id, action)

            callee_caps_raw = [
                {"action": c.action, "resource_pattern": c.resource_pattern}
                for c in callee_cap.capabilities
            ]
            user_perms = await load_permissions(task.user_id)
            effective_scope = intersect(callee_caps_raw, user_perms, [(action, resource_value)])

            if not effective_scope:
                reasons.append("empty_effective_scope")

        except Exception as exc:
            reasons.append(str(exc))

        local_reasons[task.task_id] = reasons
        effective_scopes[task.task_id] = effective_scope

        opa_tasks.append({
            "id":       task.task_id,
            "agent":    task.callee_id,
            "action":   action,
            "resource": resource_value,
        })

    # ── Phase 2: single OPA batch call ───────────────────────────────────────

    opa_result: dict = {}
    opa_error: Optional[str] = None

    try:
        opa_input = build_plan_input(
            orchestrator_id=plan_orchestrator_id,
            orch_caps=orch_caps,
            user_sub=plan_user_sub,
            tasks=opa_tasks,
        )
        opa_result = await query_opa_plan(opa_input)
    except OpaUnavailable as exc:
        opa_error = str(exc)

    # Build per_task lookup from OPA response.
    per_task_map: dict[str, dict] = {}
    if opa_result:
        for opa_task in opa_result.get("per_task", []):
            per_task_map[opa_task["id"]] = opa_task

    # ── Phase 3: merge local + OPA decisions ─────────────────────────────────

    task_decisions: list[TaskDecision] = []
    overall_allow = True

    for task in body.tasks:
        reasons = list(local_reasons.get(task.task_id, []))

        if opa_error:
            reasons.append(f"opa_unavailable: {opa_error}")
        else:
            opa_task = per_task_map.get(task.task_id, {})
            if not opa_task.get("allow", False):
                reasons.extend(opa_task.get("reasons", ["opa_denied"]))

        decision = "allow" if not reasons else "deny"
        if decision == "deny":
            overall_allow = False

        task_decisions.append(TaskDecision(
            task_id=task.task_id,
            decision=decision,
            deny_reasons=reasons,
            effective_scope=effective_scopes.get(task.task_id, []),
        ))

    overall = "allow" if overall_allow else "deny"

    writer = get_audit_writer()
    await writer.write({
        "event_type": "plan.validate",
        "plan_id": body.plan_id,
        "trace_id": body.trace_id,
        "decision": overall,
        "payload": {
            "plan_id":    body.plan_id,
            "task_count": len(body.tasks),
            "overall":    overall,
            "policy_version": opa_result.get("policy_version", settings.policy_version),
        },
    })

    return PlanValidateResponse(
        plan_id=body.plan_id,
        overall=overall,
        tasks=task_decisions,
    )
