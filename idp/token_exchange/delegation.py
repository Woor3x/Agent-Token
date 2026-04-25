from agents.loader import AgentCapability, get_agent_capability
from errors import DelegationNotAllowed, InvalidRequest


def check_delegation(
    orchestrator_id: str,
    callee_id: str,
    callee_cap: AgentCapability,
) -> None:
    accept_from = callee_cap.delegation.accept_from
    if not accept_from:
        raise DelegationNotAllowed(
            f"Agent {callee_id} does not accept delegation from anyone"
        )

    if orchestrator_id not in accept_from and "user" not in accept_from:
        raise DelegationNotAllowed(
            f"Agent {callee_id} does not accept delegation from {orchestrator_id}. "
            f"Accepted: {accept_from}"
        )


def check_orchestrator_can_invoke(
    orchestrator_cap: AgentCapability,
    callee_id: str,
) -> None:
    a2a_targets = [
        cap.resource_pattern
        for cap in orchestrator_cap.capabilities
        if cap.action == "a2a.invoke"
    ]
    target_pattern = f"agent:{callee_id}"

    import fnmatch
    allowed = any(fnmatch.fnmatch(target_pattern, p) for p in a2a_targets)
    if not allowed:
        raise DelegationNotAllowed(
            f"Orchestrator {orchestrator_cap.agent_id} is not allowed to invoke {callee_id}. "
            f"a2a.invoke patterns: {a2a_targets}"
        )
