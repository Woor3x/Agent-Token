package agent.authz

import future.keywords.if
import future.keywords.in

# DEPRECATED — plan_allow is no longer called by IdP /plan/validate.
#
# Reason: having two independent OPA decision paths (plan_allow here and
# authz.allow in a2a.rego) with different input schemas and different glob
# separators ([":", "/", "*"] vs [":", "/"]) could produce contradictory
# decisions for the same request.  The authoritative policy enforcement is
# done once, by Gateway, via authz.allow at execution time.
#
# /plan/validate now performs only IdP-local checks (delegation, executor,
# scope intersection) and no longer queries OPA.
#
# This file is retained so the OPA bundle continues to load without errors.
# It will be removed in a future cleanup commit once all references are gone.
#
# ─────────────────────────────────────────────────────────────────────────────
# plan_allow：DAG 批量决策（已废弃，不再被 IdP 调用）
#
# input 结构：
#   orchestrator: { agent_id, caps: [{action, resource_pattern}] }
#   user:         { sub }
#   plan:         [ { id, agent, action, resource } ]
#   context:      { time, delegation_depth }
# ─────────────────────────────────────────────────────────────────────────────

plan_allow := result if {
    per_task := [
        {"id": t.id, "allow": _task_ok(t), "reasons": _task_reasons(t)}
        | some t in input.plan
    ]
    count([1 | some p in per_task; not p.allow]) == 0
    result := {
        "overall": "allow",
        "per_task": per_task,
        "policy_version": policy_version,
    }
}

plan_allow := result if {
    per_task := [
        {"id": t.id, "allow": _task_ok(t), "reasons": _task_reasons(t)}
        | some t in input.plan
    ]
    count([1 | some p in per_task; not p.allow]) > 0
    result := {
        "overall": "deny",
        "per_task": per_task,
        "policy_version": policy_version,
    }
}

# ── 单 task 校验 ──────────────────────────────────────────────────────────────

_task_ok(t) if {
    # 1. executor_map 校验
    data.executor_map[t.action] == t.agent

    # 2. agent capability glob 匹配
    _any_cap_covers(t)

    # 3. user permission glob 匹配
    _any_user_perm_covers(t)
}

_any_cap_covers(t) if {
    some cap in data.agents[t.agent].capabilities
    cap.action == t.action
    glob.match(cap.resource_pattern, [":", "/", "*"], t.resource)
}

_any_user_perm_covers(t) if {
    some p in data.users[input.user.sub].permissions
    p.action == t.action
    glob.match(p.resource_pattern, [":", "/", "*"], t.resource)
}

# ── 单 task 原因收集 ──────────────────────────────────────────────────────────

_task_reasons(t) := reasons if {
    exec_reasons   := ["executor_mismatch" | data.executor_map[t.action] != t.agent]
    cap_reasons    := ["scope_exceeded"    | not _any_cap_covers(t)]
    user_reasons   := ["user_denied"       | not _any_user_perm_covers(t)]
    reasons := array.concat(exec_reasons, array.concat(cap_reasons, user_reasons))
}
