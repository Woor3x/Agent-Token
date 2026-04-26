"""Mini in-process SDK for DocAssistant → data/web agents.

Two backends:
  * ``AsgiSdkClient`` — directly invokes the peer agent's ASGI app via httpx
    transport. Used in tests / single-process demo (no Gateway).
  * ``HttpSdkClient`` — real HTTP client hitting the Gateway ``/a2a/invoke``.

Both sign a mock one-time token (via ``agents.common.server.sign_mock_token``) —
mirrors how a real orchestrator would fetch a fresh delegated token from the
IdP ``/token/exchange`` endpoint before each fan-out call.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from agents.common.server import sign_mock_token


def _mint_mock_token(
    *,
    target_agent: str,
    action: str,
    resource: str,
    trace_id: str,
    plan_id: str,
    task_id: str,
    user_sub: str,
) -> str:
    issuer = os.environ.get("IDP_ISSUER", "https://idp.local")
    secret = os.environ.get("MOCK_AUTH_SECRET", "mock-secret")
    return sign_mock_token(
        sub=user_sub,
        actor_sub="doc_assistant",
        aud=f"agent:{target_agent}",
        scope=[f"{action}:{resource}"],
        trace_id=trace_id,
        plan_id=plan_id,
        task_id=task_id,
        purpose="orchestrate",
        ttl=60,
        issuer=issuer,
        secret=secret,
        one_time=True,
    )


class AsgiSdkClient:
    """In-process transport over peer ASGI apps (no network)."""

    def __init__(self, *, apps: dict[str, Any], user_sub: str = "user:demo") -> None:
        self._apps = apps  # agent_id -> FastAPI app
        self._user_sub = user_sub

    async def invoke(
        self,
        *,
        target_agent: str,
        intent: dict,
        trace_id: str,
        plan_id: str,
        task_id: str,
    ) -> dict:
        app = self._apps.get(target_agent)
        if app is None:
            raise RuntimeError(f"no app registered for {target_agent}")
        token = _mint_mock_token(
            target_agent=target_agent,
            action=intent["action"],
            resource=intent["resource"],
            trace_id=trace_id,
            plan_id=plan_id,
            task_id=task_id,
            user_sub=self._user_sub,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            r = await c.post(
                "/invoke",
                headers={"Authorization": f"DPoP {token}"},
                json={"intent": intent, "context": {
                    "trace_id": trace_id, "plan_id": plan_id, "task_id": task_id,
                }},
            )
        if r.status_code != 200:
            raise RuntimeError(f"peer {target_agent} {r.status_code}: {r.text}")
        return r.json()["data"]


class HttpSdkClient:
    """Production SDK path through M1 IdP + Gateway.

    Wraps :class:`agent_token_sdk.AgentClient` (which performs RFC 7521 client
    assertion → RFC 8693 token exchange → RFC 9449 DPoP → Gateway POST). Each
    call uses the orchestrator's RS256 private key (registered with the IdP via
    ``services/idp/agents/register``) to sign client assertions and DPoP proofs.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        idp_url: str,
        gateway_url: str,
        private_key_pem: bytes | str,
        kid: str,
        user_token: str,
        user_sub: str | None = None,
    ) -> None:
        from agent_token_sdk import AgentClient

        self._user_token = user_token
        self._user_sub = user_sub or "user:unknown"
        self._client = AgentClient(
            agent_id=agent_id,
            idp_url=idp_url,
            gateway_url=gateway_url,
            kid=kid,
            private_key_pem=private_key_pem,
        )

    async def invoke(
        self,
        *,
        target_agent: str,
        intent: dict,
        trace_id: str,
        plan_id: str,
        task_id: str,
    ) -> dict:
        resp = await self._client.invoke(
            target=target_agent,
            intent=intent,
            on_behalf_of=self._user_token,
            purpose="orchestrate",
            plan_id=plan_id,
            task_id=task_id,
            trace_id=trace_id,
        )
        # Gateway returns the full agent response envelope; pull out data.
        return resp.get("data", resp)

    async def aclose(self) -> None:
        await self._client.close()
