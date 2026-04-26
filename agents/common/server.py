"""Build FastAPI `/invoke` app for any agent."""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import AuthnError, VerifiedClaims, verify_delegated_token
from .capability import Capability
from .config import AgentConfig
from .logging import clear_trace_context, get_logger, set_trace_context
from .ulid import new_ulid

InvokeHandler = Callable[[dict, VerifiedClaims, Capability], Awaitable[dict]]

_log = get_logger("agents.server")


def _extract_deny_reasons(claims: VerifiedClaims, body: dict, cap: Capability) -> list[str]:
    reasons: list[str] = []
    intent = body.get("intent") or {}
    action = intent.get("action")
    resource = intent.get("resource")
    # capability coverage (zero-trust self-check; Gateway also verified)
    if not action or not resource:
        reasons.append("intent_missing")
        return reasons
    if not cap.find(action, resource):
        reasons.append("capability_missing")
    # scope covers
    needed = f"{action}:{resource}"
    if not any(_scope_matches(s, needed) for s in claims.scope):
        reasons.append("scope_exceeded")
    return reasons


def _scope_matches(granted: str, requested: str) -> bool:
    """Glob-ish match. `:` and `/` are separators and `*` wildcards a segment."""
    import fnmatch

    return fnmatch.fnmatchcase(requested, granted)


class AgentServer:
    def __init__(
        self,
        *,
        config: AgentConfig,
        capability: Capability,
        handler: InvokeHandler,
    ) -> None:
        self.config = config
        self.capability = capability
        self.handler = handler

    def create_app(self) -> FastAPI:
        app = FastAPI(title=f"agent:{self.config.agent_id}")
        expected_aud = f"agent:{self.config.agent_id}"

        @app.exception_handler(AuthnError)
        async def _authn_exc(_: Request, exc: AuthnError):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

        @app.get("/healthz")
        async def healthz() -> dict:
            return {
                "status": "ok",
                "agent": self.config.agent_id,
                "version": "1.0.0",
                "deps": {"feishu": "mock" if self.config.feishu_mock else "live"},
            }

        @app.post("/invoke")
        async def invoke(request: Request) -> JSONResponse:
            start = time.monotonic()
            body = await request.json()
            claims = await verify_delegated_token(
                request.headers.get("Authorization", ""),
                expected_issuer=self.config.idp_issuer,
                expected_audience=expected_aud,
                jwks_url=self.config.idp_jwks_url,
            )
            set_trace_context(
                trace_id=claims.trace_id or "",
                plan_id=claims.plan_id or "",
                task_id=claims.task_id or "",
                agent=self.config.agent_id,
            )
            try:
                deny = _extract_deny_reasons(claims, body, self.capability)
                if deny:
                    _log.warning(f"deny: {deny}")
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": {
                                "code": "AUTHZ_SCOPE_EXCEEDED"
                                if "scope_exceeded" in deny
                                else "AUTHZ_CAPABILITY_MISSING",
                                "message": ",".join(deny),
                                "trace_id": claims.trace_id,
                            }
                        },
                    )
                data = await self.handler(body, claims, self.capability)
                latency = int((time.monotonic() - start) * 1000)
                _log.info(f"invoke ok latency_ms={latency}")
                return JSONResponse(
                    status_code=200,
                    content={
                        "status": "ok",
                        "data": data,
                        "trace_id": claims.trace_id,
                        "event_id": new_ulid(),
                        "latency_ms": latency,
                    },
                )
            except PermissionError as e:
                return JSONResponse(
                    status_code=403,
                    content={"error": {"code": "AGENT_FORBIDDEN", "message": str(e)}},
                )
            except ValueError as e:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "INTENT_INVALID", "message": str(e)}},
                )
            except HTTPException:
                raise
            except Exception as e:  # pragma: no cover
                _log.exception("handler crashed")
                return JSONResponse(
                    status_code=500,
                    content={"error": {"code": "AGENT_INTERNAL_ERROR", "message": str(e)}},
                )
            finally:
                clear_trace_context()

        return app


def sign_mock_token(
    *,
    sub: str,
    actor_sub: str | None,
    aud: str,
    scope: list[str],
    trace_id: str = "trace-demo",
    plan_id: str = "plan-demo",
    task_id: str = "t1",
    purpose: str = "demo",
    ttl: int = 60,
    issuer: str = "https://idp.local",
    secret: str = "mock-secret",
    jti: str | None = None,
    one_time: bool = True,
) -> str:
    """Test helper: forge a delegated token locally (HS256, only valid under MOCK_AUTH)."""
    import time as _t
    import uuid as _uuid

    import jwt as _jwt

    now = int(_t.time())
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": jti or _uuid.uuid4().hex,
        "scope": scope,
        "trace_id": trace_id,
        "plan_id": plan_id,
        "task_id": task_id,
        "purpose": purpose,
        "one_time": one_time,
        "cnf": {"jkt": "mock-jkt"},
        "policy_version": "v1.2.0",
    }
    if actor_sub:
        payload["act"] = {"sub": actor_sub, "act": None}
    return _jwt.encode(payload, secret, algorithm="HS256")
