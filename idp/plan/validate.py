from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.loader import get_agent_capability
from audit.writer import get_audit_writer
from config import settings
from errors import InvalidRequest, OpaUnavailable
from plan.opa_client import check_plan_allowed
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
    task_decisions: list[TaskDecision] = []
    overall_allow = True

    for task in body.tasks:
        deny_reasons = []
        effective_scope: list[str] = []

        try:
            action, resource_value = parse_scope(task.scope)

            target_agent_id = extract_target_agent(task.audience)
            callee_cap = get_agent_capability(target_agent_id)
            if callee_cap is None:
                raise InvalidRequest(f"Unknown callee agent: {target_agent_id}")

            orch_cap = get_agent_capability(task.orchestrator_id)
            if orch_cap is None:
                raise InvalidRequest(f"Unknown orchestrator: {task.orchestrator_id}")

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
                deny_reasons.append("empty_effective_scope")

            opa_input = {
                "plan_id": body.plan_id,
                "task_id": task.task_id,
                "orchestrator_id": task.orchestrator_id,
                "callee_id": target_agent_id,
                "user_id": task.user_id,
                "action": action,
                "resource": resource_value,
                "effective_scope": effective_scope,
                "policy_version": settings.policy_version,
            }

            try:
                opa_allowed, opa_deny_reasons = await check_plan_allowed(opa_input)
                if not opa_allowed:
                    deny_reasons.extend(opa_deny_reasons or ["opa_denied"])
            except OpaUnavailable as exc:
                deny_reasons.append(f"opa_unavailable: {exc.message}")

            decision = "allow" if not deny_reasons else "deny"

        except Exception as exc:
            deny_reasons.append(str(exc))
            decision = "deny"

        if decision == "deny":
            overall_allow = False

        task_decisions.append(TaskDecision(
            task_id=task.task_id,
            decision=decision,
            deny_reasons=deny_reasons,
            effective_scope=effective_scope,
        ))

    overall = "allow" if overall_allow else "deny"

    writer = get_audit_writer()
    await writer.write({
        "event_type": "plan.validate",
        "plan_id": body.plan_id,
        "trace_id": body.trace_id,
        "decision": overall,
        "payload": {
            "plan_id": body.plan_id,
            "task_count": len(body.tasks),
            "overall": overall,
        },
    })

    return PlanValidateResponse(
        plan_id=body.plan_id,
        overall=overall,
        tasks=task_decisions,
    )
