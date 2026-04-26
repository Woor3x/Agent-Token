"""SDK ``AgentServer`` wrapper test — ensures wrapper exposes a working /invoke
backed by the canonical core implementation in ``agents.common.server``.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agent_token_sdk import AgentServer
from agents.common.server import sign_mock_token


_CAP = Path(__file__).resolve().parents[2] / "agents" / "data_agent" / "capability.yaml"


async def _echo_handler(body, claims, cap):
    return {
        "echo": body.get("intent"),
        "actor": (claims.act or {}).get("sub"),
        "scope": claims.scope,
    }


@pytest.mark.asyncio
async def test_sdk_agent_server_exposes_invoke() -> None:
    server = AgentServer(
        agent_id="data_agent",
        capability_path=_CAP,
        handler=_echo_handler,
    )
    app = server.create_app()

    tok = sign_mock_token(
        sub="user:alice",
        actor_sub="agent:doc_assistant",
        aud="agent:data_agent",
        scope=["feishu.bitable.read:app_token:bascn_alice/table:tbl_q1"],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={"intent": {
                "action": "feishu.bitable.read",
                "resource": "app_token:bascn_alice/table:tbl_q1",
            }},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["echo"]["action"] == "feishu.bitable.read"
    assert body["data"]["actor"] == "agent:doc_assistant"


@pytest.mark.asyncio
async def test_sdk_agent_server_propagates_capability_miss() -> None:
    server = AgentServer(
        agent_id="data_agent",
        capability_path=_CAP,
        handler=_echo_handler,
    )
    app = server.create_app()
    tok = sign_mock_token(
        sub="user:alice",
        actor_sub="agent:doc_assistant",
        aud="agent:data_agent",
        scope=["web.search:*"],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={"intent": {"action": "web.search", "resource": "*"}},
        )
    assert r.status_code == 403
    assert r.json()["error"]["code"].startswith("AUTHZ_")


def test_sdk_agent_server_capability_loaded() -> None:
    server = AgentServer(
        agent_id="data_agent",
        capability_path=_CAP,
        handler=_echo_handler,
    )
    actions = {c.action for c in server.capability.capabilities}
    assert "feishu.bitable.read" in actions
    assert server.config.agent_id == "data_agent"
