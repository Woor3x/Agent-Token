"""OPA authz client — 5ms timeout, fail-closed."""
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_OPA_URL = f"{settings.opa_url}/allow"


async def check_authz(
    claims: dict,
    intent: dict,
    target_agent: str,
    context: dict,
) -> tuple[bool, list[str]]:
    """Return (allow, deny_reasons). Any error → (False, ['opa_unavailable'])."""
    payload = {
        "input": {
            "token": claims,
            "intent": intent,
            "target_agent": target_agent,
            "context": context,
        }
    }
    try:
        async with httpx.AsyncClient(
            timeout=settings.opa_timeout_ms / 1000,
            verify=settings.mtls_ca if settings.mtls_enabled else True,
        ) as client:
            r = await client.post(_OPA_URL, json=payload)
            r.raise_for_status()
            result = r.json().get("result", {})
            allow = bool(result.get("allow", False))
            reasons = result.get("reasons", [])
            return allow, reasons
    except Exception as exc:
        logger.warning("opa unavailable: %s — fail-closed", exc)
        return False, ["opa_unavailable"]
