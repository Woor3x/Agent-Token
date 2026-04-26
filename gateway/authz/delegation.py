"""Delegation chain verifier — recursive act JWT claim parsing + cycle detection."""
from errors import authz_depth_exceeded, authz_delegation_rejected


def verify_delegation(claims: dict, max_depth: int) -> list[str]:
    """Walk the act chain, return list of delegated subjects.

    Raises AuthzError on depth exceeded or cycle.
    """
    chain: list[str] = []
    act = claims.get("act")
    while act:
        sub = act.get("sub", "")
        chain.append(sub)
        act = act.get("act")

    if len(chain) > max_depth:
        raise authz_depth_exceeded()

    # Cycle: same subject appears twice in the chain
    if len(set(chain)) != len(chain):
        raise authz_delegation_rejected("cycle")

    return chain
