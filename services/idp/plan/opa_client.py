import time
from typing import Any

import httpx

from config import settings
from errors import OpaUnavailable

_PLAN_ALLOW_PATH = "/v1/data/agent/authz/plan_allow"
_TIMEOUT_SEC = 5.0


async def query_opa_plan(opa_input: dict) -> dict:
    """POST to plan_allow; return parsed result dict. Raises OpaUnavailable on any failure."""
    url = f"{settings.opa_url}{_PLAN_ALLOW_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.post(url, json={"input": opa_input})
            resp.raise_for_status()
            body = resp.json()
    except httpx.TimeoutException:
        raise OpaUnavailable("OPA request timed out")
    except httpx.HTTPStatusError as exc:
        raise OpaUnavailable(f"OPA returned HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise OpaUnavailable(f"OPA unreachable: {exc}")

    result = body.get("result")
    if result is None:
        raise OpaUnavailable("OPA response missing 'result' key")

    return result  # {"overall": "allow|deny", "per_task": [...], "policy_version": "..."}


def build_plan_input(
    orchestrator_id: str,
    orch_caps: list[dict],
    user_sub: str,
    tasks: list[dict[str, str]],
    delegation_depth: int = 1,
) -> dict:
    """Build the OPA plan_allow input from validated orchestrator/user/task data.

    tasks: list of {"id": task_id, "agent": callee_id, "action": action, "resource": resource}
    """
    return {
        "orchestrator": {
            "agent_id": orchestrator_id,
            "caps": orch_caps,
        },
        "user": {"sub": user_sub},
        "plan": tasks,
        "context": {
            "time": int(time.time()),
            "delegation_depth": delegation_depth,
        },
    }
