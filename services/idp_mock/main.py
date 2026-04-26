"""IdP Mock standalone server (FastAPI on :9100).

Endpoints
---------
* ``POST /token/exchange`` — RFC 8693 token-exchange endpoint. Verifies the
  presence of all RFC 7521 client-assertion + delegation form fields, decodes
  the assertion **without signature verification** (mock mode) and mints a
  short-lived HS256 delegated token bound to the requested DPoP key thumbprint.
* ``POST /plan/validate`` — placeholder plan-validation hook used by the
  orchestrator. Returns ``{status: ok, plan_id, task_count}``.
* ``GET  /jwks`` — empty JWKS (HS256 mock has no public key set). Real
  deployment would return RS256 public keys here.
* ``GET  /healthz`` — liveness probe.

Configured via env vars: ``IDP_ISSUER``, ``MOCK_AUTH_SECRET``, ``IDP_TOKEN_TTL``.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _env_issuer() -> str:
    return os.environ.get("IDP_ISSUER", "https://idp.local")


def _env_secret() -> str:
    return os.environ.get("MOCK_AUTH_SECRET", "mock-secret")


def _env_ttl() -> int:
    try:
        return int(os.environ.get("IDP_TOKEN_TTL", "120"))
    except ValueError:
        return 120


def _subject_sub(subject_token: str) -> str:
    try:
        claims = jwt.decode(subject_token, options={"verify_signature": False})
        return claims.get("sub", "user:unknown")
    except Exception:
        return "user:unknown"


def create_app() -> FastAPI:
    app = FastAPI(title="idp-mock", version="1.0.0")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "idp-mock", "issuer": _env_issuer()}

    @app.get("/jwks")
    async def jwks() -> dict:
        # HS256 mock — no public keys; real IdP would expose RS256 JWKS here.
        return {"keys": []}

    @app.post("/token/exchange")
    async def token_exchange(request: Request) -> JSONResponse:
        form = await request.form()
        required = {
            "grant_type", "client_assertion_type", "client_assertion",
            "subject_token", "subject_token_type", "requested_token_type",
            "audience", "scope", "resource",
        }
        missing = [k for k in required if not form.get(k)]
        if missing:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_request", "missing": missing},
            )
        try:
            assertion_claims = jwt.decode(
                form["client_assertion"], options={"verify_signature": False}
            )
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_client", "detail": str(e)},
            )
        if assertion_claims.get("iss") != assertion_claims.get("sub"):
            return JSONResponse(status_code=400, content={"error": "invalid_client"})

        scope = form["scope"]
        audience = form["audience"]
        dpop_jkt = form.get("dpop_jkt", "mock-jkt")
        ttl = _env_ttl()
        now = int(time.time())
        delegated_claims: dict[str, Any] = {
            "iss": _env_issuer(),
            "sub": _subject_sub(form["subject_token"]),
            "aud": audience,
            "scope": [scope],
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "jti": uuid.uuid4().hex,
            "one_time": True,
            "cnf": {"jkt": dpop_jkt},
            "act": {"sub": assertion_claims["sub"], "act": None},
            "purpose": form.get("purpose", ""),
            "plan_id": form.get("plan_id", ""),
            "task_id": form.get("task_id", ""),
            "trace_id": form.get("trace_id", ""),
            "policy_version": "v1.2.0",
        }
        token = jwt.encode(delegated_claims, _env_secret(), algorithm="HS256")
        return JSONResponse(
            status_code=200,
            content={
                "access_token": token,
                "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_type": "DPoP",
                "expires_in": ttl,
                "jti": delegated_claims["jti"],
                "policy_version": "v1.2.0",
                "audit_id": f"evt_{uuid.uuid4().hex[:12]}",
            },
        )

    @app.post("/plan/validate")
    async def plan_validate(request: Request) -> dict:
        body = await request.json()
        plan = body.get("plan") or {}
        return {
            "status": "ok",
            "plan_id": plan.get("plan_id"),
            "task_count": len(plan.get("tasks") or []),
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    port = int(os.environ.get("PORT", "9100"))
    uvicorn.run(app, host="0.0.0.0", port=port)
