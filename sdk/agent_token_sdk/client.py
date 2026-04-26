"""AgentClient: caller-side flow per 方案-SDK §4.

One ``invoke`` call =
  1. Self-sign RFC 7523 Client Assertion (``AssertionSigner``).
  2. POST IdP ``/token/exchange`` with ``subject_token`` + assertion, carrying
     DPoP proof bound to that endpoint URL. Receives one-time delegated token.
  3. Sign DPoP proof for ``<gateway>/a2a/invoke`` with the access token hash.
  4. POST Gateway ``/a2a/invoke`` with the delegated token + DPoP header.

All HTTP calls share a single ``httpx.AsyncClient`` for connection reuse; tests
inject a preconfigured client via ``http=...`` to route to ASGI transports.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx

from .assertion import AssertionSigner
from .dpop import DPoPSigner
from .errors import A2AError, TokenExchangeError, is_retryable

_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


class AgentClient:
    """Caller-side SDK entry."""

    def __init__(
        self,
        *,
        agent_id: str,
        idp_url: str,
        gateway_url: str,
        kid: str | None = None,
        private_key_path: str | None = None,
        private_key_pem: bytes | str | None = None,
        mock_secret: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        assertion_exp_delta: int = 60,
    ) -> None:
        self.agent_id = agent_id
        self.idp_url = idp_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self._assertion_exp_delta = assertion_exp_delta
        resolved_kid = kid or f"{agent_id}-default"
        self._assertion = AssertionSigner(
            agent_id=agent_id,
            kid=resolved_kid,
            private_key_path=private_key_path,
            private_key_pem=private_key_pem,
            mock_secret=mock_secret or (
                os.environ.get("MOCK_AUTH_SECRET")
                if os.environ.get("MOCK_AUTH", "false").lower() == "true"
                else None
            ),
        )
        self._dpop = DPoPSigner(
            kid=resolved_kid,
            private_key_path=private_key_path,
            private_key_pem=private_key_pem,
        )
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    # ---- public API --------------------------------------------------------

    async def invoke(
        self,
        *,
        target: str,
        intent: dict[str, Any],
        on_behalf_of: str,
        purpose: str = "",
        plan_id: str | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        delegated = await self._token_exchange(
            target=target,
            intent=intent,
            on_behalf_of=on_behalf_of,
            purpose=purpose,
            plan_id=plan_id,
            task_id=task_id,
            trace_id=trace_id,
        )

        gw_url = f"{self.gateway_url}/a2a/invoke"
        dpop = self._dpop.sign(url=gw_url, method="POST", access_token=delegated)

        headers = {
            "Authorization": f"DPoP {delegated}",
            "DPoP": dpop,
            "X-Target-Agent": target,
            "Content-Type": "application/json",
        }
        if trace_id:
            headers["Traceparent"] = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
        if plan_id:
            headers["X-Plan-Id"] = plan_id
        if task_id:
            headers["X-Task-Id"] = task_id
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        body: dict[str, Any] = {"intent": intent}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key

        resp = await self._http.post(gw_url, json=body, headers=headers)
        if resp.status_code != 200:
            try:
                payload = resp.json()
                err = payload.get("error", {})
            except Exception:
                err = {"code": "UPSTREAM_ERROR", "message": resp.text}
            raise A2AError(
                err.get("code", "UNKNOWN"),
                err.get("message", ""),
                trace_id=err.get("trace_id"),
            )
        return resp.json()

    async def plan_validate(
        self,
        *,
        plan: dict[str, Any],
        user_token: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.idp_url}/plan/validate"
        assertion = self._assertion.sign(aud=url, exp_delta=self._assertion_exp_delta)
        dpop = self._dpop.sign(url=url, method="POST")
        headers = {
            "DPoP": dpop,
            "Content-Type": "application/json",
        }
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        body = {
            "client_assertion": assertion,
            "subject_token": user_token,
            "plan": plan,
        }
        r = await self._http.post(url, headers=headers, json=body)
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AgentClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ---- internals ---------------------------------------------------------

    async def _token_exchange(
        self,
        *,
        target: str,
        intent: dict[str, Any],
        on_behalf_of: str,
        purpose: str,
        plan_id: str | None,
        task_id: str | None,
        trace_id: str | None,
    ) -> str:
        te_url = f"{self.idp_url}/token/exchange"
        assertion = self._assertion.sign(aud=te_url, exp_delta=self._assertion_exp_delta)
        dpop = self._dpop.sign(url=te_url, method="POST")

        form = {
            "grant_type": _GRANT_TYPE,
            "client_assertion_type": _CLIENT_ASSERTION_TYPE,
            "client_assertion": assertion,
            "subject_token": on_behalf_of,
            "subject_token_type": _SUBJECT_TOKEN_TYPE,
            "requested_token_type": _REQUESTED_TOKEN_TYPE,
            "audience": f"agent:{target}",
            "scope": f"{intent['action']}:{intent['resource']}",
            "resource": f"{self.gateway_url}/a2a/invoke",
            "purpose": purpose,
            "plan_id": plan_id or "",
            "task_id": task_id or "",
            "trace_id": trace_id or "",
            "dpop_jkt": self._dpop.jkt_b64u,
        }
        r = await self._http.post(
            te_url,
            data=form,
            headers={
                "DPoP": dpop,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if r.status_code != 200:
            try:
                msg = r.json()
            except Exception:
                msg = {"error": r.text}
            raise TokenExchangeError(r.status_code, str(msg), body=r.text)
        body = r.json()
        if "access_token" not in body:
            raise TokenExchangeError(r.status_code, "no access_token in response", body=r.text)
        return body["access_token"]


# Convenience: retry wrapper the caller can opt into.
async def invoke_with_retry(
    client: AgentClient,
    *,
    attempts: int = 3,
    base_delay: float = 0.25,
    **kwargs: Any,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await client.invoke(**kwargs)
        except A2AError as e:
            if not is_retryable(e.code):
                raise
            last_exc = e
            await asyncio.sleep(base_delay * (2 ** i))
    assert last_exc is not None
    raise last_exc
