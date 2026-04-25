package agent.authz

import future.keywords.if
import future.keywords.in

default plan_allow = false

plan_allow if {
    count(deny_reasons) == 0
}

deny_reasons[reason] if {
    input.effective_scope == []
    reason := "empty_effective_scope"
}

deny_reasons[reason] if {
    not valid_policy_version
    reason := sprintf("policy_version_mismatch: got %v", [input.policy_version])
}

deny_reasons[reason] if {
    not valid_orchestrator_role
    reason := sprintf("invalid_orchestrator_role: %v", [input.orchestrator_id])
}

deny_reasons[reason] if {
    input.action == "a2a.invoke"
    not valid_a2a_target
    reason := sprintf("invalid_a2a_target: %v", [input.resource])
}

valid_policy_version if {
    input.policy_version == "v1.2.0"
}

valid_orchestrator_role if {
    input.orchestrator_id != ""
}

valid_a2a_target if {
    startswith(input.resource, "agent:")
}
