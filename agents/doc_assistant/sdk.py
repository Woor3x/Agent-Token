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


class HttpSdkClient:  # pragma: no cover — demo only, not tested
    def __init__(self, *, gateway_url: str, user_sub: str = "user:demo") -> None:
        self._gateway = gateway_url.rstrip("/")
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
        token = _mint_mock_token(
            target_agent=target_agent,
            action=intent["action"],
            resource=intent["resource"],
            trace_id=trace_id,
            plan_id=plan_id,
            task_id=task_id,
            user_sub=self._user_sub,
        )
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{self._gateway}/a2a/invoke",
                headers={
                    "Authorization": f"DPoP {token}",
                    "X-Target-Agent": target_agent,
                },
                json={"intent": intent, "context": {
                    "trace_id": trace_id, "plan_id": plan_id, "task_id": task_id,
                }},
            )
        r.raise_for_status()
        return r.json()["data"]
