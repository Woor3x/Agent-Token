"""End-to-end SDK ↔ agents integration.

Wires the SDK ``AgentClient`` (caller-side) against real ``data_agent`` and
``web_agent`` ASGI apps (built from ``agents/*``), routed through a mock
Gateway, with delegated tokens minted by a mock IdP. Feishu calls hit the
in-process Feishu Mock service. This is the closest thing to a real cross-
process flow we can run from a single ``pytest`` invocation.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_token_sdk import AgentClient, AgentServer
from agents.data_agent.feishu.oauth import FeishuOAuth
from agents.data_agent.handler import DataAgentHandler
from agents.web_agent.handler import WebAgentHandler
from services.feishu_mock.main import app as feishu_mock_app

from .helpers import build_mock_gateway, build_mock_idp, build_sdk_http, mint_user_token


_REPO = Path(__file__).resolve().parents[2]
_DATA_CAP = _REPO / "agents" / "data_agent" / "capability.yaml"
_WEB_CAP = _REPO / "agents" / "web_agent" / "capability.yaml"


def _feishu_factory():
    transport = httpx.ASGITransport(app=feishu_mock_app)
    return lambda: httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _build_data_agent_app():
    handler = DataAgentHandler(
        feishu_base="http://testserver",
        oauth=FeishuOAuth(base="http://testserver"),
        client_factory=_feishu_factory(),
    )
    return AgentServer(
        agent_id="data_agent", capability_path=_DATA_CAP, handler=handler
    ).create_app()


def _build_web_agent_app():
    handler = WebAgentHandler()
    return AgentServer(
        agent_id="web_agent", capability_path=_WEB_CAP, handler=handler
    ).create_app()


@pytest.mark.asyncio
async def test_sdk_to_data_agent_through_mock_gateway() -> None:
    idp = build_mock_idp()
    gw = build_mock_gateway({
        "data_agent": _build_data_agent_app(),
        "web_agent": _build_web_agent_app(),
    })
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="doc_assistant-2025-q1",
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
            plan_id="plan-1",
            task_id="t1",
            trace_id="trace-1",
        )
    assert out["status"] == "ok"
    data = out["data"]
    assert data["count"] == 4
    regions = {r["fields"]["region"] for r in data["records"]}
    assert {"North", "South", "East", "West"} <= regions
    await http.aclose()


@pytest.mark.asyncio
async def test_sdk_scope_mismatch_yields_authz_error() -> None:
    """SDK requests action:resource X but the agent only sees scope X — happy
    path. Now flip: ask Gateway to forward to data_agent but with an intent
    whose action isn't in data_agent's capability list → 403 AUTHZ_*.
    """
    from agent_token_sdk.errors import A2AError

    idp = build_mock_idp()
    gw = build_mock_gateway({"data_agent": _build_data_agent_app()})
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="doc_assistant-2025-q1",
        mock_secret="mock-secret",
        http=http,
    ) as c:
        with pytest.raises(A2AError) as ei:
            await c.invoke(
                target="data_agent",
                intent={"action": "web.search", "resource": "*"},
                on_behalf_of=mint_user_token(),
            )
    assert ei.value.code.startswith("AUTHZ_")
    await http.aclose()


@pytest.mark.asyncio
async def test_sdk_to_web_agent_search() -> None:
    idp = build_mock_idp()
    gw = build_mock_gateway({"web_agent": _build_web_agent_app()})
    http = build_sdk_http(idp, gw)
    async with AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="doc_assistant-2025-q1",
        mock_secret="mock-secret",
        http=http,
    ) as c:
        out = await c.invoke(
            target="web_agent",
            intent={
                "action": "web.search",
                "resource": "*",
                "params": {"query": "agent token", "max_results": 3},
            },
            on_behalf_of=mint_user_token(aud="agent:web_agent"),
            purpose="research",
        )
    assert out["status"] == "ok"
    assert out["data"]["query"] == "agent token"
    assert out["data"]["count"] >= 1
    await http.aclose()
