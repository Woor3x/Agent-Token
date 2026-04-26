"""Incoming delegated-token verification (zero-trust: re-verify even after Gateway).

In test / mock mode (IDP_JWKS_URL unreachable or MOCK_AUTH=true) we accept tokens
whose `iss` matches expected issuer and that carry the required claims, without
cryptographic JWKS verification. This lets us unit-test agents in isolation.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cachetools import TTLCache

from .logging import get_logger

_log = get_logger("agents.auth")
_jwks_cache: TTLCache[str, dict] = TTLCache(maxsize=16, ttl=600)


class AuthnError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass(frozen=True)
class VerifiedClaims:
    sub: str
    iss: str
    aud: str
    jti: str
    exp: int
    nbf: int
    scope: list[str]
    act: dict | None
    trace_id: str | None
    plan_id: str | None
    task_id: str | None
    purpose: str | None
    one_time: bool
    cnf_jkt: str | None
    policy_version: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, claims: dict) -> "VerifiedClaims":
        return cls(
            sub=claims.get("sub", ""),
            iss=claims.get("iss", ""),
            aud=claims.get("aud", ""),
            jti=claims.get("jti", ""),
            exp=int(claims.get("exp", 0)),
            nbf=int(claims.get("nbf", 0)),
            scope=list(claims.get("scope") or []),
            act=claims.get("act"),
            trace_id=claims.get("trace_id"),
            plan_id=claims.get("plan_id"),
            task_id=claims.get("task_id"),
            purpose=claims.get("purpose"),
            one_time=bool(claims.get("one_time", False)),
            cnf_jkt=(claims.get("cnf") or {}).get("jkt"),
            policy_version=claims.get("policy_version"),
            raw=claims,
        )


async def _load_jwks(jwks_url: str) -> dict:
    if jwks_url in _jwks_cache:
        return _jwks_cache[jwks_url]
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(jwks_url)
        r.raise_for_status()
        body = r.json()
    for k in body.get("keys", []):
        if "kid" in k:
            _jwks_cache[k["kid"]] = k
    _jwks_cache[jwks_url] = body
    return body


def _mock_mode() -> bool:
    return os.environ.get("MOCK_AUTH", "false").lower() == "true"


def _parse_dpop_bearer(header: str) -> str:
    if not header:
        raise AuthnError("AUTHN_TOKEN_INVALID", "no Authorization header")
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() not in ("dpop", "bearer"):
        raise AuthnError("AUTHN_TOKEN_INVALID", "bad Authorization scheme")
    return parts[1].strip()


async def verify_delegated_token(
    authorization: str,
    *,
    expected_issuer: str,
    expected_audience: str,
    jwks_url: str,
    leeway: int = 30,
    require_one_time: bool = True,
) -> VerifiedClaims:
    token = _parse_dpop_bearer(authorization)

    if _mock_mode():
        # Test/mock: decode without signature check (test always signs with HS256 secret)
        secret = os.environ.get("MOCK_AUTH_SECRET", "mock-secret")
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=expected_audience,
                issuer=expected_issuer,
                leeway=leeway,
            )
        except jwt.PyJWTError as e:
            raise AuthnError("AUTHN_TOKEN_INVALID", str(e))
    else:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise AuthnError("AUTHN_TOKEN_INVALID", "no kid")
        if kid not in _jwks_cache:
            try:
                await _load_jwks(jwks_url)
            except Exception as e:
                raise AuthnError("AUTHN_TOKEN_INVALID", f"jwks unavailable: {e}")
        jwk = _jwks_cache.get(kid)
        if not jwk:
            raise AuthnError("AUTHN_TOKEN_INVALID", f"unknown kid {kid}")
        try:
            key = jwt.PyJWK(jwk).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=expected_audience,
                issuer=expected_issuer,
                leeway=leeway,
            )
        except jwt.PyJWTError as e:
            raise AuthnError("AUTHN_TOKEN_INVALID", str(e))

    now = int(time.time())
    if claims.get("exp", 0) < now - leeway:
        raise AuthnError("AUTHN_TOKEN_INVALID", "expired")
    if require_one_time and not claims.get("one_time"):
        raise AuthnError("AUTHN_TOKEN_INVALID", "not_one_time")
    if claims.get("aud") != expected_audience:
        raise AuthnError("AUTHZ_AUDIENCE_MISMATCH", f"aud={claims.get('aud')}")

    return VerifiedClaims.from_dict(claims)
