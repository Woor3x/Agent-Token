"""WebAgent unit tests: search + fetch + SSRF."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.server import AgentServer, sign_mock_token
from agents.web_agent.handler import WebAgentHandler
from agents.web_agent.search.fetcher import url_allowed


@pytest.fixture
def web_agent_app():
    cap_path = Path(__file__).resolve().parents[2] / "agents" / "web_agent" / "capability.yaml"
    cfg = AgentConfig.load("web_agent", cap_path)
    cap = load_capability(cap_path)
    return AgentServer(
        config=cfg, capability=cap, handler=WebAgentHandler()
    ).create_app()


async def _invoke(app, *, intent: dict, scope: list[str]) -> httpx.Response:
    tok = sign_mock_token(
        sub="user:alice", actor_sub="doc_assistant",
        aud="agent:web_agent", scope=scope,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={"intent": intent},
        )


@pytest.mark.asyncio
async def test_search_happy(web_agent_app) -> None:
    r = await _invoke(
        web_agent_app,
        intent={
            "action": "web.search",
            "resource": "*",
            "params": {"query": "zero trust agent design", "max_results": 2},
        },
        scope=["web.search:*"],
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["count"] >= 1
    assert body["count"] <= 2


@pytest.mark.asyncio
async def test_search_respects_capability_cap(web_agent_app) -> None:
    r = await _invoke(
        web_agent_app,
        intent={
            "action": "web.search", "resource": "*",
            "params": {"query": "token exchange", "max_results": 999},
        },
        scope=["web.search:*"],
    )
    assert r.status_code == 200
    # capability.max_results == 10
    assert r.json()["data"]["count"] <= 10


def test_url_allowed_basic() -> None:
    ok, reason = url_allowed("http://example.com/x")
    assert not ok and reason == "scheme_not_https"
    ok, reason = url_allowed("https://evil.example.net/x")
    assert not ok and reason == "domain_not_allowed"


@pytest.mark.asyncio
async def test_fetch_denied_scheme(web_agent_app) -> None:
    r = await _invoke(
        web_agent_app,
        intent={"action": "web.fetch", "resource": "http://example.com"},
        scope=["web.fetch:*"],
    )
    # http:// fails resource_pattern "https://*" → capability miss → 403 authz
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_fetch_denied_domain(web_agent_app) -> None:
    r = await _invoke(
        web_agent_app,
        intent={"action": "web.fetch", "resource": "https://evil.net/x"},
        scope=["web.fetch:*"],
    )
    # capability pattern "https://*" matches → handler path runs, fetcher blocks → 403 forbidden
    assert r.status_code == 403
    body = r.json()
    assert "fetch_blocked" in body["error"]["message"] or body["error"]["code"] == "AGENT_FORBIDDEN"


@pytest.mark.asyncio
async def test_fetch_ok_via_mock(web_agent_app, monkeypatch) -> None:
    # Stub http_fetch to avoid DNS/network.
    async def _fake_fetch(url, **kw):
        return "<html><body><p>hello world of agents</p></body></html>"

    monkeypatch.setattr("agents.web_agent.handler.http_fetch", _fake_fetch)

    r = await _invoke(
        web_agent_app,
        intent={"action": "web.fetch", "resource": "https://example.com/x"},
        scope=["web.fetch:*"],
    )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert "hello world" in body["summary"]
