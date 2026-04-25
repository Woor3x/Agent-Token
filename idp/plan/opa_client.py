from typing import Any

import httpx

from config import settings
from errors import OpaUnavailable

OPA_POLICY_PATH = "/v1/data/agent/authz/plan_allow"
OPA_TIMEOUT_SEC = 5.0


async def query_opa(input_data: dict) -> dict:
    url = f"{settings.opa_url}{OPA_POLICY_PATH}"
    try:
        async with httpx.AsyncClient(timeout=OPA_TIMEOUT_SEC) as client:
            response = await client.post(url, json={"input": input_data})
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        raise OpaUnavailable("OPA request timed out")
    except httpx.HTTPStatusError as exc:
        raise OpaUnavailable(f"OPA returned HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        raise OpaUnavailable(f"OPA unreachable: {exc}")


async def check_plan_allowed(input_data: dict) -> tuple[bool, list[str]]:
    result = await query_opa(input_data)
    allowed = result.get("result", False)
    deny_reasons = result.get("deny_reasons", [])
    if not isinstance(deny_reasons, list):
        deny_reasons = []
    return bool(allowed), deny_reasons
