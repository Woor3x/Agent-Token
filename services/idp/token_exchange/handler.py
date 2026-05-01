import time
from typing import Optional

from fastapi import APIRouter, Form, Header, Request


def _canonical_exchange_url(request: Request) -> str:
    """Reconstruct /token/exchange URL honouring X-Forwarded-* headers.

    When the IdP sits behind a reverse proxy (nginx / Envoy / ALB), the proxy
    rewrites the Host and scheme before the request reaches uvicorn.
    The DPoP proof is signed against the *external* URL that the client sees,
    so we must reconstruct that URL instead of using ``request.base_url`` which
    reflects only the internal bind address.
    """
    proto = (
        request.headers.get("X-Forwarded-Proto")
        or request.url.scheme
    )
    host = (
        request.headers.get("X-Forwarded-Host")
        or request.headers.get("Host")
        or request.url.netloc
    )
    # Strip-path prefix set by the proxy (e.g. /idp when mounted at /idp/*)
    prefix = request.headers.get("X-Forwarded-Prefix", "").rstrip("/")
    return f"{proto}://{host}{prefix}/token/exchange"

from agents.loader import get_agent_capability
from audit.writer import get_audit_writer
from config import settings
from dpop.validator import verify_dpop_proof
from errors import (
    DelegationNotAllowed, EmptyEffectiveScope, InvalidRequest,
)
from storage.redis import incr_with_window, sismember
from token_exchange.assertion import verify_client_assertion
from token_exchange.context import apply_context
from token_exchange.delegation import check_delegation, check_orchestrator_can_invoke
from token_exchange.executor import check_executor
from token_exchange.intent import extract_target_agent, parse_scope
from token_exchange.intersect import intersect
from token_exchange.signer import sign_delegated_token
from token_exchange.subject_token import verify_subject_token
from users.perms import load_permissions

router = APIRouter()

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


@router.post("/token/exchange")
async def token_exchange(
    request: Request,
    grant_type: str = Form(...),
    client_assertion_type: str = Form(default="urn:ietf:params:oauth:client-assertion-type:jwt-bearer"),
    client_assertion: str = Form(...),
    subject_token: str = Form(...),
    subject_token_type: str = Form(default=SUBJECT_TOKEN_TYPE),
    scope: str = Form(...),
    audience: str = Form(...),
    resource: Optional[str] = Form(default=None),
    purpose: Optional[str] = Form(default=None),
    plan_id: Optional[str] = Form(default=None),
    task_id: Optional[str] = Form(default=None),
    trace_id: Optional[str] = Form(default=None),
    parent_span: Optional[str] = Form(default=None),
    dpop: Optional[str] = Header(default=None, alias="DPoP"),
):
    if grant_type != GRANT_TYPE:
        raise InvalidRequest(f"Unsupported grant_type: {grant_type}")

    client_ip = request.client.host if request.client else "unknown"

    # Phase 1: Verify client assertion (orchestrator identity)
    orchestrator = await verify_client_assertion(client_assertion)

    # Phase 2: Verify subject token (user identity)
    user = await verify_subject_token(subject_token)

    # Phase 3: Verify DPoP proof
    if not dpop:
        raise InvalidRequest("DPoP proof header is required for token exchange")
    dpop_claims = await verify_dpop_proof(
        dpop,
        expected_htm="POST",
        expected_htu=_canonical_exchange_url(request),  # Fix A: honours X-Forwarded-* headers
    )
    dpop_jkt = dpop_claims.jkt

    # Phase 4: Parse scope → (action, resource)
    action, resource_value = parse_scope(scope)

    # Phase 5: Delegation legitimacy check
    target_agent_id = extract_target_agent(audience)
    callee_cap = get_agent_capability(target_agent_id)
    if callee_cap is None:
        raise InvalidRequest(f"Unknown callee agent: {target_agent_id}")

    orchestrator_cap = get_agent_capability(orchestrator.agent_id)
    if orchestrator_cap is None:
        raise DelegationNotAllowed(f"Orchestrator {orchestrator.agent_id} has no capability definition")

    check_orchestrator_can_invoke(orchestrator_cap, target_agent_id)
    check_delegation(orchestrator.agent_id, target_agent_id, callee_cap)

    # Phase 6: Single executor check
    check_executor(target_agent_id, action)

    # Phase 7: Compute effective scope via intersection
    callee_caps_raw = [{"action": c.action, "resource_pattern": c.resource_pattern} for c in callee_cap.capabilities]
    user_perms = await load_permissions(user.sub)
    effective_scope = intersect(callee_caps_raw, user_perms, [(action, resource_value)])

    if not effective_scope:
        raise EmptyEffectiveScope(
            f"Effective scope is empty: action={action}, resource={resource_value}"
        )

    # Phase 8: Apply context constraints
    ctx = {
        "user": user.sub,
        "client_ip": client_ip,
        "orchestrator": orchestrator.agent_id,
        "callee": target_agent_id,
    }
    effective_scope = await apply_context(effective_scope, ctx)

    if not effective_scope:
        raise EmptyEffectiveScope("Effective scope became empty after context evaluation")

    # Phase 9: Rate limiting per agent+action
    _cap_entry = next(
        (c for c in callee_cap.capabilities if c.action == action), None
    )
    rate_limit = (
        _cap_entry.constraints.get("max_calls_per_minute", 100)
        if _cap_entry else 100
    )
    rate_key = f"rate:agent:{orchestrator.agent_id}:{action}"
    count, allowed = await incr_with_window(rate_key, 60, rate_limit)
    if not allowed:
        from errors import RateLimited
        raise RateLimited(
            f"Agent {orchestrator.agent_id} rate limit for {action}: {count}/{rate_limit} per min"
        )

    # Phase 10: Sign delegated token and write audit
    token_claims = {
        "sub": user.sub,
        "act": {"sub": orchestrator.agent_id},
        # Fix B: use "agent:<id>" prefix per spec (§3 audience binding).
        # OPA audience_match rule checks token.aud == "agent:{target_agent}".
        "aud": f"agent:{target_agent_id}",
        "scope": " ".join(effective_scope),
        "plan_id": plan_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "parent_span": parent_span,
        "purpose": purpose,
        "resource": resource,
    }
    token_claims["cnf"] = {"jkt": dpop_jkt}

    token_claims = {k: v for k, v in token_claims.items() if v is not None}

    delegated_token, token_jti = sign_delegated_token(token_claims)

    writer = get_audit_writer()
    await writer.write({
        "event_type": "token.issue",
        "trace_id": trace_id,
        "plan_id": plan_id,
        "task_id": task_id,
        "sub": user.sub,
        "act": orchestrator.agent_id,
        "aud": f"agent:{target_agent_id}",   # consistent with token.aud
        "decision": "allow",
        "payload": {
            "token_jti": token_jti,
            "scope": effective_scope,
            "action": action,
            "resource": resource_value,
            "orchestrator": orchestrator.agent_id,
            "callee": target_agent_id,
            "dpop_bound": dpop_jkt is not None,
        },
    })

    return {
        "access_token": delegated_token,
        "token_type": "Bearer",
        "expires_in": 120,
        "scope": " ".join(effective_scope),
        "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
    }
