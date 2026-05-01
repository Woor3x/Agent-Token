"""Test helpers: mock IdP + mock Gateway apps, shared transport wiring.

Mock IdP issues HS256 tokens (MOCK_AUTH path on the Agent side accepts them).
Mock Gateway forwards ``/a2a/invoke`` to the right in-process agent ASGI app
identified by the ``X-Target-Agent`` header.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, Form, HTTPException, Header, Request
from fastapi.responses import JSONResponse

MOCK_SECRET = os.environ.get("MOCK_AUTH_SECRET", "mock-secret")
MOCK_ISSUER = os.environ.get("IDP_ISSUER", "https://idp.local")


# ---- Mock IdP ----------------------------------------------------------------


def build_mock_idp(*, issuer: str = MOCK_ISSUER) -> FastAPI:
    app = FastAPI(title="mock-idp")

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
        # In the real IdP we would verify client_assertion against JWKS. Here we
        # just decode without signature check so tests can exercise the flow.
        try:
            assertion_claims = jwt.decode(
                form["client_assertion"], options={"verify_signature": False}
            )
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": "invalid_client", "detail": str(e)})
        if assertion_claims.get("iss") != assertion_claims.get("sub"):
            return JSONResponse(status_code=400, content={"error": "invalid_client"})

        scope = form["scope"]
        audience = form["audience"]
        dpop_jkt = form.get("dpop_jkt", "mock-jkt")
        now = int(time.time())
        delegated_claims: dict[str, Any] = {
            "iss": issuer,
            "sub": _subject_sub(form["subject_token"]),
            "aud": audience,
            "scope": [scope],
            "iat": now,
            "nbf": now,
            "exp": now + 120,
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
        token = jwt.encode(delegated_claims, MOCK_SECRET, algorithm="HS256")
        return JSONResponse(
            status_code=200,
            content={
                "access_token": token,
                "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "token_type": "DPoP",
                "expires_in": 120,
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


def _subject_sub(subject_token: str) -> str:
    try:
        claims = jwt.decode(subject_token, options={"verify_signature": False})
        return claims.get("sub", "user:unknown")
    except Exception:
        return "user:unknown"


# ---- Mock Gateway ------------------------------------------------------------


def build_mock_gateway(agent_apps: dict[str, FastAPI]) -> FastAPI:
    app = FastAPI(title="mock-gateway")
    transports = {name: httpx.ASGITransport(app=a) for name, a in agent_apps.items()}

    @app.post("/a2a/invoke")
    async def a2a_invoke(
        request: Request,
        x_target_agent: str = Header(...),
        authorization: str = Header(...),
        dpop: str = Header(...),
    ) -> JSONResponse:
        transport = transports.get(x_target_agent)
        if transport is None:
            raise HTTPException(status_code=404, detail=f"no such agent {x_target_agent}")
        body = await request.json()
        headers = {
            "Authorization": authorization,
            "DPoP": dpop,
            "Content-Type": "application/json",
        }
        for h in ("Traceparent", "X-Plan-Id", "X-Task-Id", "X-Idempotency-Key"):
            v = request.headers.get(h)
            if v:
                headers[h] = v
        async with httpx.AsyncClient(
            transport=transport, base_url="http://agent.internal"
        ) as c:
            resp = await c.post("/invoke", headers=headers, json=body)
        return JSONResponse(status_code=resp.status_code, content=resp.json())

    return app


# ---- Single shared httpx client over mounted mounts --------------------------


def build_sdk_http(idp_app: FastAPI, gateway_app: FastAPI) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` that routes:
      * ``https://idp.mock``     → the mock IdP ASGI app
      * ``https://gateway.mock`` → the mock Gateway ASGI app
    """
    mounts = {
        "all://idp.mock": httpx.ASGITransport(app=idp_app),
        "all://gateway.mock": httpx.ASGITransport(app=gateway_app),
    }
    return httpx.AsyncClient(mounts=mounts, timeout=10.0)


def mint_user_token(*, sub: str = "user:alice", aud: str = "agent:doc_assistant") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": MOCK_ISSUER,
            "sub": sub,
            "aud": aud,
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "jti": uuid.uuid4().hex,
            "scope": ["orchestrate:plan:*"],
            "one_time": True,
            "cnf": {"jkt": "user-mock-jkt"},
            "purpose": "user-origin",
        },
        MOCK_SECRET,
        algorithm="HS256",
    )
