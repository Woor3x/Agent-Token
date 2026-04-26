"""POST /a2a/nl — NL prompt entry point."""
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from authz.delegation import verify_delegation
from authz.one_shot import consume_one_shot
from authz.opa_client import check_authz
from config import settings
from errors import AuthzError, _error_body
from intent.parser_nl import parse_nl
from middleware.audit import audit_writer
from routing.registry import registry
from routing.upstream_client import call_upstream

router = APIRouter()


@router.post("/a2a/nl")
async def nl_invoke(request: Request):
    import json

    start_ms = time.monotonic() * 1000
    claims: dict = request.state.token_claims
    target_agent = request.headers.get("X-Target-Agent", "")
    trace_id = getattr(request.state, "trace_id", "")

    body_bytes = await request.body()
    body = json.loads(body_bytes)

    prompt = body.get("prompt", "")
    user_ctx = body.get("context", {})

    intent, raw_prompt = await parse_nl(prompt, user_ctx)
    request.state.intent = intent

    verify_delegation(claims, settings.delegation_max_depth)

    source_ip = request.client.host if request.client else ""
    context = {
        "time": int(time.time()),
        "source_ip": source_ip,
        "trace_id": trace_id,
        "recent_calls": 0,
        "delegation_depth": 0,
    }
    allow, reasons = await check_authz(claims, intent, target_agent, context)
    if not allow:
        audit_writer.emit({
            "trace_id": trace_id,
            "plan_id": claims.get("plan_id", ""),
            "sub": claims.get("sub", ""),
            "target_agent": target_agent,
            "action": intent.get("action", ""),
            "resource": intent.get("resource", ""),
            "decision": "deny",
            "deny_reasons": reasons,
            "jti": claims.get("jti", ""),
            "raw_prompt": raw_prompt,
            "source_ip": source_ip,
        })
        exc = AuthzError("AUTHZ_SCOPE_EXCEEDED", f"policy denied: {reasons}")
        return JSONResponse(status_code=403, content=_error_body(request, exc))

    redis = request.app.state.redis
    await consume_one_shot(redis, claims)

    cfg = registry.get(target_agent)
    # Forward the original body to upstream
    response = await call_upstream(request, target_agent, cfg, body_bytes)

    duration = time.monotonic() * 1000 - start_ms
    plan_id = f"plan_{uuid.uuid4().hex[:12]}"
    audit_writer.emit({
        "trace_id": trace_id,
        "plan_id": plan_id,
        "sub": claims.get("sub", ""),
        "target_agent": target_agent,
        "action": intent.get("action", ""),
        "resource": intent.get("resource", ""),
        "decision": "allow",
        "deny_reasons": [],
        "jti": claims.get("jti", ""),
        "raw_prompt": raw_prompt,
        "source_ip": source_ip,
        "duration_ms": duration,
    })

    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Policy-Version"] = settings.policy_version
    return response
