"""DocAssistant FastAPI entry.

In production the peer ASGI apps are not wired in — the orchestrator uses
``HttpSdkClient`` to go through the Gateway. For the single-process demo / tests
we expose ``build_app(peer_apps)`` so the harness can inject data_agent and
web_agent ASGI apps directly (no Gateway in the loop).

``POST /chat`` is the user-facing entry point. It accepts the user's OIDC
Bearer token (aud=web-ui), verifies it against the IdP JWKS, then runs the
LangGraph graph using the agent's own registered private key for downstream
token-exchange calls through the Gateway.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import jwt as _jwt
from cachetools import TTLCache
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.llm import LLMProvider, make_llm
from agents.common.logging import setup_logging
from agents.common.server import AgentServer
from agents.common.ulid import new_ulid

from .graph import run_graph
from .handler import DocAssistantHandler
from .sdk import AsgiSdkClient, HttpSdkClient

setup_logging()

_CAP_PATH = Path(__file__).with_name("capability.yaml")

# JWKS cache: kid → JWK dict, TTL 10 min
_jwks_cache: TTLCache = TTLCache(maxsize=32, ttl=600)


async def _verify_user_token(token: str, jwks_url: str, expected_issuer: str) -> str:
    """Verify a user OIDC access token (RS256, aud=web-ui). Returns ``sub``."""
    try:
        header = _jwt.get_unverified_header(token)
    except Exception as exc:
        raise ValueError(f"malformed token header: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise ValueError("token has no kid")

    if kid not in _jwks_cache:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
            for k in resp.json().get("keys", []):
                if "kid" in k:
                    _jwks_cache[k["kid"]] = k

    jwk = _jwks_cache.get(kid)
    if not jwk:
        raise ValueError(f"unknown kid: {kid!r}")

    key = _jwt.PyJWK(jwk).key
    claims = _jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience="web-ui",
        issuer=expected_issuer,
        leeway=30,
    )
    return claims["sub"]


def build_app(
    peer_apps: dict[str, Any] | None = None,
    *,
    llm: LLMProvider | None = None,
):
    config = AgentConfig.load("doc_assistant", _CAP_PATH)
    cap = load_capability(_CAP_PATH)
    handler = DocAssistantHandler(
        feishu_base=config.feishu_base,
        peer_apps=peer_apps or {},
        llm=llm if llm is not None else make_llm(),
    )
    app = AgentServer(config=config, capability=cap, handler=handler).create_app()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/chat")
    async def chat(request: Request) -> JSONResponse:
        """User-facing chat entry. Accepts OIDC Bearer token; runs the orchestration graph."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "AUTHN_REQUIRED", "message": "Bearer token required"}},
            )
        token_str = auth[7:].strip()

        try:
            user_sub = await _verify_user_token(
                token_str,
                jwks_url=config.idp_jwks_url,
                expected_issuer=config.idp_issuer,
            )
        except Exception as exc:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "AUTHN_TOKEN_INVALID", "message": str(exc)}},
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "INVALID_REQUEST", "message": "invalid JSON body"}},
            )

        prompt = body.get("prompt", "").strip()
        if not prompt:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "INVALID_REQUEST", "message": "prompt is required"}},
            )

        trace_id = body.get("trace_id") or new_ulid()
        plan_id = new_ulid()

        # Build SDK: use the agent's registered private key when available (production),
        # fall back to in-process AsgiSdkClient for tests / no-key environments.
        key_dir = Path(os.environ.get("AGENT_KEY_DIR", "/app/keys/doc_assistant"))
        priv = key_dir / "private.pem"
        kid_file = key_dir / "kid.txt"

        if priv.exists() and kid_file.exists() and not (peer_apps or {}):
            sdk = HttpSdkClient(
                agent_id="doc_assistant",
                idp_url=os.environ.get("IDP_URL", "http://idp:8000"),
                gateway_url=config.gateway_url,
                private_key_pem=priv.read_bytes(),
                kid=kid_file.read_text().strip(),
                user_token=token_str,
                user_sub=user_sub,
            )
        else:
            sdk = AsgiSdkClient(apps=peer_apps or {}, user_sub=user_sub)

        state: dict[str, Any] = {
            "user_prompt": prompt,
            "user_token": token_str,
            "user_sub": user_sub,
            "trace_id": trace_id,
            "plan_id": plan_id,
            "sdk": sdk,
            "feishu_base": handler._feishu_base,
            "feishu_oauth": handler._oauth,
            "client_factory": handler._client_factory,
            "llm": handler._llm,
        }

        try:
            final = await run_graph(state)
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": {"code": "AGENT_ERROR", "message": str(exc)}},
            )

        return JSONResponse({
            "status": "ok",
            "trace_id": state["trace_id"],
            "plan_id": final.get("plan_id", plan_id),
            "dag": final.get("dag", []),
            "results": final.get("results", {}),
            "doc": final.get("doc"),
        })

    return app


app = build_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
