from agents.loader import AgentCapability, get_capabilities
from errors import InvalidRequest


def check_sod(orchestrators: list[AgentCapability], executors: list[AgentCapability]) -> None:
    orchestrator_actions: set[str] = set()
    for orch in orchestrators:
        for cap in orch.capabilities:
            orchestrator_actions.add(cap.action)

    executor_actions: set[str] = set()
    for exe in executors:
        for cap in exe.capabilities:
            executor_actions.add(cap.action)

    overlap = orchestrator_actions & executor_actions
    if overlap:
        raise InvalidRequest(
            f"SoD violation: orchestrators and executors share actions: {sorted(overlap)}"
        )


def run_global_sod_check() -> None:
    capabilities = get_capabilities()
    orchestrators = [c for c in capabilities.values() if c.role == "orchestrator"]
    executors = [c for c in capabilities.values() if c.role == "executor"]
    check_sod(orchestrators, executors)


def check_agent_sod(agent_cap: AgentCapability, existing_capabilities: dict[str, AgentCapability]) -> None:
    existing_role_opposite = [
        c for aid, c in existing_capabilities.items()
        if c.role != agent_cap.role and aid != agent_cap.agent_id
    ]
    agent_actions = {cap.action for cap in agent_cap.capabilities}

    for existing in existing_role_opposite:
        existing_actions = {cap.action for cap in existing.capabilities}
        overlap = agent_actions & existing_actions
        if overlap:
            raise InvalidRequest(
                f"SoD violation: new agent '{agent_cap.agent_id}' (role={agent_cap.role}) "
                f"conflicts with '{existing.agent_id}' on actions: {sorted(overlap)}"
            )
