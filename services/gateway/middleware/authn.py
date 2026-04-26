"""AuthN middleware: JWT verify → 4-dim revocation → DPoP validate."""
import logging

import jwt
import redis.asyncio as aioredis
from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from errors import authn_invalid, authn_revoked, authn_dpop_invalid, GatewayError
from revoke.bloom import revoke_bloom
from jwttoken.dpop import verify_dpop
from jwttoken.jwks_cache import jwks_cache

logger = logging.getLogger(__name__)


def _parse_dpop_bearer(auth_header: str) -> str:
    """Extract token from 'DPoP <token>'."""
    if not auth_header:
        raise authn_invalid("missing Authorization header")
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].upper() != "DPOP":
        raise authn_invalid("expected DPoP scheme")
    return parts[1].strip()


async def authn_middleware(request: Request, call_next):
    # skip health / metrics / admin endpoints from full authn
    path = request.url.path
    if path in ("/healthz", "/metrics"):
        return await call_next(request)
    if path.startswith("/admin/"):
        return await call_next(request)

    try:
        token = _parse_dpop_bearer(request.headers.get("Authorization", ""))

        # JWKS verify
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise authn_invalid("missing kid")
        key = await jwks_cache.get(kid)

        target_agent = request.headers.get("X-Target-Agent", "")
        # M1 IdP signs ``aud = bare agent_id`` (no ``agent:`` prefix). Original
        # M2 gateway expected the prefix; relax to bare to match M1.
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            issuer=settings.idp_issuer,
            audience=target_agent if target_agent else None,
            options={"require": ["exp", "nbf", "iat", "jti", "sub", "aud"]},
            leeway=30,
        )

        if not claims.get("one_time"):
            raise authn_invalid("not a one-time token")

        # 4-dim revocation
        redis: aioredis.Redis = request.app.state.redis
        jti = claims["jti"]
        sub = claims.get("sub", "")
        trace_id = claims.get("trace_id", "")
        plan_id = claims.get("plan_id", "")

        if revoke_bloom.might_contain(jti):
            if await redis.sismember("revoked:jtis", jti):
                raise authn_revoked("jti")
        if sub and await redis.sismember("revoked:subs", sub):
            raise authn_revoked("sub")
        if trace_id and await redis.sismember("revoked:traces", trace_id):
            raise authn_revoked("trace")
        if plan_id and await redis.sismember("revoked:plans", plan_id):
            raise authn_revoked("plan")

        # DPoP proof
        dpop_token = request.headers.get("DPoP", "")
        if not dpop_token:
            raise authn_dpop_invalid("missing DPoP header")

        cnf = claims.get("cnf", {})
        expected_jkt = cnf.get("jkt", "")
        if not expected_jkt:
            raise authn_invalid("token missing cnf.jkt")

        dpop_claims = verify_dpop(
            dpop_token,
            expected_jkt=expected_jkt,
            expected_htu=str(request.url),
            expected_htm=request.method,
            max_iat_skew=settings.dpop_max_iat_skew,
        )

        # DPoP jti replay guard
        dpop_jti = dpop_claims["jti"]
        set_ok = await redis.set(
            f"dpop:jti:{dpop_jti}", 1, nx=True, ex=settings.dpop_jti_ttl
        )
        if not set_ok:
            raise authn_dpop_invalid("replay")

        request.state.token_claims = claims
        request.state.dpop_claims = dpop_claims

    except GatewayError as exc:
        from errors import _error_body
        return JSONResponse(status_code=exc.http_status, content=_error_body(request, exc))
    except jwt.InvalidTokenError as exc:
        err = authn_invalid(str(exc))
        from errors import _error_body
        return JSONResponse(status_code=err.http_status, content=_error_body(request, err))

    return await call_next(request)
