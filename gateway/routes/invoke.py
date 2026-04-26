"""POST /a2a/invoke — single A2A call."""
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from authz.delegation import verify_delegation
from authz.one_shot import consume_one_shot
from authz.opa_client import check_authz
from config import settings
from errors import AuthzError, GatewayError, _error_body
from intent.parser_structured import parse_structured
from middleware.audit import audit_writer
from routing.registry import registry
from routing.upstream_client import call_upstream

router = APIRouter()


@router.post("/a2a/invoke")
async def invoke(request: Request):
    start_ms = time.monotonic() * 1000
    claims: dict = request.state.token_claims
    target_agent = request.headers.get("X-Target-Agent", "")
    plan_id = request.headers.get("X-Plan-Id", claims.get("plan_id", ""))
    trace_id = getattr(request.state, "trace_id", "")

    body_bytes = await request.body()
    try:
        import json
        body = json.loads(body_bytes)
    except Exception:
        from errors import IntentError
        raise IntentError("INTENT_INVALID", "invalid JSON body")

    intent = parse_structured(body)
    request.state.intent = intent

    # Delegation chain
    verify_delegation(claims, settings.delegation_max_depth)

    # OPA authz
    source_ip = request.client.host if request.client else ""
    context = {
        "time": int(time.time()),
        "source_ip": source_ip,
        "trace_id": trace_id,
        "recent_calls": 0,
        "delegation_depth": len(claims.get("act", {}) and [claims["act"]] or []),
    }
    allow, reasons = await check_authz(claims, intent, target_agent, context)
    if not allow:
        _emit_audit(request, claims, intent, target_agent, "deny", reasons, start_ms)
        exc = AuthzError("AUTHZ_SCOPE_EXCEEDED", f"policy denied: {reasons}")
        return JSONResponse(status_code=403, content=_error_body(request, exc))

    # One-shot consume
    redis = request.app.state.redis
    await consume_one_shot(redis, claims)

    # Route upstream
    cfg = registry.get(target_agent)
    extra_headers = {
        "X-Policy-Version": settings.policy_version,
        "X-Audit-Id": f"evt_{uuid.uuid4().hex[:12]}",
    }
    response = await call_upstream(request, target_agent, cfg, body_bytes, extra_headers)

    duration = time.monotonic() * 1000 - start_ms
    _emit_audit(request, claims, intent, target_agent, "allow", [], start_ms, duration)

    response.headers["X-Policy-Version"] = settings.policy_version
    response.headers["X-Trace-Id"] = trace_id
    return response


def _emit_audit(
    request: Request,
    claims: dict,
    intent: dict,
    target_agent: str,
    decision: str,
    reasons: list,
    start_ms: float,
    duration: float | None = None,
) -> None:
    audit_writer.emit({
        "trace_id": getattr(request.state, "trace_id", ""),
        "plan_id": claims.get("plan_id", ""),
        "sub": claims.get("sub", ""),
        "target_agent": target_agent,
        "action": intent.get("action", ""),
        "resource": intent.get("resource", ""),
        "decision": decision,
        "deny_reasons": reasons,
        "jti": claims.get("jti", ""),
        "dpop_jti": getattr(request.state, "dpop_claims", {}).get("jti", ""),
        "source_ip": request.client.host if request.client else "",
        "duration_ms": duration,
    })
