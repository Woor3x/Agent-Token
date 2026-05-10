import fnmatch


def cap_match(cap: dict, action: str, resource: str) -> bool:
    if cap.get("action") != action:
        return False
    pattern = cap.get("resource_pattern", "")
    return fnmatch.fnmatch(resource, pattern)


def intersect(
    callee_caps: list[dict],
    user_perms: list[dict],
    requested: list[tuple[str, str]],
) -> list[str]:
    result = []
    for action, resource in requested:
        callee_ok = any(cap_match(c, action, resource) for c in callee_caps)
        user_ok = any(cap_match(p, action, resource) for p in user_perms)
        if callee_ok and user_ok:
            result.append(f"{action}:{resource}")
    return result
