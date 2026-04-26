"""DataAgent unit tests (Feishu Mock backend)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.server import AgentServer, sign_mock_token
from agents.data_agent.feishu.oauth import FeishuOAuth
from agents.data_agent.handler import DataAgentHandler
from services.feishu_mock.main import app as feishu_mock_app


def _feishu_client_factory():
    transport = httpx.ASGITransport(app=feishu_mock_app)
    return lambda: httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def data_agent_app():
    cap_path = Path(__file__).resolve().parents[2] / "agents" / "data_agent" / "capability.yaml"
    cfg = AgentConfig.load("data_agent", cap_path)
    cap = load_capability(cap_path)
    handler = DataAgentHandler(
        feishu_base="http://testserver",
        oauth=FeishuOAuth(base="http://testserver"),
        client_factory=_feishu_client_factory(),
    )
    return AgentServer(config=cfg, capability=cap, handler=handler).create_app()


async def _call_invoke(app, *, intent: dict, scope: list[str]) -> httpx.Response:
    tok = sign_mock_token(
        sub="user:alice",
        actor_sub="doc_assistant",
        aud="agent:data_agent",
        scope=scope,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={"intent": intent},
        )


@pytest.mark.asyncio
async def test_healthz(data_agent_app) -> None:
    transport = httpx.ASGITransport(app=data_agent_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json()["agent"] == "data_agent"


@pytest.mark.asyncio
async def test_bitable_read_happy(data_agent_app) -> None:
    r = await _call_invoke(
        data_agent_app,
        intent={
            "action": "feishu.bitable.read",
            "resource": "app_token:bascn_alice/table:tbl_q1",
            "params": {"page_size": 50},
        },
        scope=["feishu.bitable.read:app_token:bascn_alice/table:tbl_q1"],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["count"] == 4
    regions = {rec["fields"]["region"] for rec in data["records"]}
    assert {"North", "South", "East", "West"} <= regions


@pytest.mark.asyncio
async def test_contact_read_happy(data_agent_app) -> None:
    r = await _call_invoke(
        data_agent_app,
        intent={
            "action": "feishu.contact.read",
            "resource": "department:sales",
        },
        scope=["feishu.contact.read:department:sales"],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_capability_denied_for_wrong_action(data_agent_app) -> None:
    r = await _call_invoke(
        data_agent_app,
        intent={
            "action": "web.search",
            "resource": "*",
        },
        scope=["web.search:*"],
    )
    # Capability miss at data_agent → 403
    assert r.status_code == 403
    assert r.json()["error"]["code"].startswith("AUTHZ_")


@pytest.mark.asyncio
async def test_scope_exceeded(data_agent_app) -> None:
    r = await _call_invoke(
        data_agent_app,
        intent={
            "action": "feishu.contact.read",
            "resource": "department:engineering",  # capability allows, but scope doesn't
        },
        scope=["feishu.contact.read:department:sales"],
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "AUTHZ_SCOPE_EXCEEDED"


@pytest.mark.asyncio
async def test_bad_resource_format(data_agent_app) -> None:
    r = await _call_invoke(
        data_agent_app,
        intent={
            "action": "feishu.bitable.read",
            "resource": "app_token:bascn_alice/table:tbl_q1",
            "params": {},
        },
        scope=["feishu.bitable.read:*"],
    )
    assert r.status_code == 200

    # Malformed resource reaches handler → ValueError → 400.
    r2 = await _call_invoke(
        data_agent_app,
        intent={
            "action": "feishu.bitable.read",
            "resource": "not-a-valid-format",
            "params": {},
        },
        scope=["feishu.bitable.read:*"],
    )
    # capability.find uses fnmatch "app_token:*/table:*" — "not-a-valid-format" won't match → 403
    assert r2.status_code == 403
