from config import ACTION_ENUM, RESOURCE_REGEX
from errors import InvalidRequest


def parse_scope(scope_str: str) -> tuple[str, str]:
    if not scope_str or ":" not in scope_str:
        raise InvalidRequest(f"scope format must be 'action:resource', got: {scope_str!r}")

    action, resource = scope_str.split(":", 1)
    action = action.strip()
    resource = resource.strip()

    if action not in ACTION_ENUM:
        raise InvalidRequest(f"Unknown action: {action!r}. Allowed: {sorted(ACTION_ENUM)}")

    pattern = RESOURCE_REGEX.get(action)
    if pattern and not pattern.match(resource):
        raise InvalidRequest(f"Resource {resource!r} does not match pattern for action {action!r}")

    return action, resource


def extract_target_agent(audience: str) -> str:
    if audience.startswith("agent:"):
        return audience[len("agent:"):]
    return audience
