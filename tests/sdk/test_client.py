"""AgentClient unit tests against mock IdP + mock Gateway."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agent_token_sdk import A2AError, AgentClient
from agent_token_sdk.errors import TokenExchangeError

from .helpers import build_mock_gateway, build_mock_idp, build_sdk_http, mint_user_token


def _echo_agent() -> FastAPI:
    """Tiny stand-in agent: returns whatever intent it receives."""
    app = FastAPI()

    @app.post("/invoke")
    async def invoke(body: dict) -> dict:
        return {
            "status": "ok",
            "data": {"echo": body.get("intent")},
            "trace_id": "t-echo",
        }

    return app


@pytest.mark.asyncio
async def test_invoke_happy_path() -> None:
    idp = build_mock_idp()
    gw = build_mock_gateway({"data_agent": _echo_agent()})
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="doc_assistant-1",
        mock_secret="mock-secret",
        http=http,
    ) as c:
        out = await c.invoke(
            target="data_agent",
            intent={
                "action": "feishu.bitable.read",
                "resource": "app_token:bascn_alice/table:tbl_q1",
                "params": {"page_size": 50},
            },
            on_behalf_of=mint_user_token(),
            purpose="weekly-report",
            plan_id="plan-1", task_id="t1", trace_id="trace-1",
        )
    assert out["status"] == "ok"
    assert out["data"]["echo"]["action"] == "feishu.bitable.read"
    await http.aclose()


@pytest.mark.asyncio
async def test_invoke_propagates_error_envelope() -> None:
    idp = build_mock_idp()
    deny_app = FastAPI()

    @deny_app.post("/invoke")
    async def deny(_: dict) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"error": {"code": "AUTHZ_SCOPE_EXCEEDED", "message": "nope", "trace_id": "t-x"}},
        )

    gw = build_mock_gateway({"data_agent": deny_app})
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="k-1",
        mock_secret="mock-secret",
        http=http,
    ) as c:
        with pytest.raises(A2AError) as ei:
            await c.invoke(
                target="data_agent",
                intent={"action": "feishu.bitable.read", "resource": "app_token:x/table:y"},
                on_behalf_of=mint_user_token(),
            )
    assert ei.value.code == "AUTHZ_SCOPE_EXCEEDED"
    assert ei.value.trace_id == "t-x"
    await http.aclose()


@pytest.mark.asyncio
async def test_token_exchange_400_raises() -> None:
    idp = FastAPI()

    @idp.post("/token/exchange")
    async def te() -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "invalid_grant"})

    http = httpx.AsyncClient(
        mounts={
            "all://idp.mock": httpx.ASGITransport(app=idp),
            "all://gateway.mock": httpx.ASGITransport(app=FastAPI()),
        }
    )
    async with AgentClient(
        agent_id="x", idp_url="https://idp.mock", gateway_url="https://gateway.mock",
        kid="k-1", mock_secret="s", http=http,
    ) as c:
        with pytest.raises(TokenExchangeError) as ei:
            await c.invoke(
                target="data_agent",
                intent={"action": "feishu.bitable.read", "resource": "x"},
                on_behalf_of=mint_user_token(),
            )
    assert ei.value.status_code == 400
    await http.aclose()


@pytest.mark.asyncio
async def test_plan_validate() -> None:
    idp = build_mock_idp()
    gw = build_mock_gateway({})
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="k-1", mock_secret="mock-secret", http=http,
    ) as c:
        out = await c.plan_validate(
            plan={
                "plan_id": "plan-1",
                "tasks": [
                    {"id": "t1", "agent": "data_agent",
                     "action": "feishu.bitable.read",
                     "resource": "app_token:x/table:y", "deps": []},
                ],
            },
            user_token=mint_user_token(),
            trace_id="trace-1",
        )
    assert out["status"] == "ok"
    assert out["task_count"] == 1
    await http.aclose()
