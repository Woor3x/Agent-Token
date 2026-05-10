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
    # LLM provider defaults to mock → summarize_with_llm falls back to char-truncate
    assert "hello world" in body["summary"]


@pytest.mark.asyncio
async def test_search_tavily_backend(monkeypatch) -> None:
    from agents.web_agent.search import client as search_client

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {
                "results": [
                    {"title": "T1", "url": "https://x.example.com/1", "content": "snip1"},
                    {"title": "T2", "url": "https://y.example.com/2", "content": "snip2"},
                ]
            }

    class _FakeClient:
        def __init__(self, *a, **kw):
            captured["init"] = (a, kw)

        async def post(self, url, json=None):
            captured["url"] = url
            captured["payload"] = json
            return _FakeResp()

        async def aclose(self):
            return None

    monkeypatch.setattr(search_client.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")

    hits = await search_client.search("agent token", max_results=2)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["payload"]["api_key"] == "tvly-test-key"
    assert captured["payload"]["query"] == "agent token"
    assert captured["payload"]["max_results"] == 2
    assert hits == [
        {"title": "T1", "url": "https://x.example.com/1", "snippet": "snip1"},
        {"title": "T2", "url": "https://y.example.com/2", "snippet": "snip2"},
    ]


@pytest.mark.asyncio
async def test_search_tavily_requires_key(monkeypatch) -> None:
    from agents.web_agent.search import client as search_client

    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await search_client.search("x", max_results=1)


def test_url_allowed_domain_open_switch(monkeypatch) -> None:
    # Default: arbitrary domain blocked.
    ok, reason = url_allowed("https://news.ycombinator.com/")
    assert not ok and reason == "domain_not_allowed"
    # With domain-open switch, scheme + DNS + CIDR still enforced.
    monkeypatch.setenv("WEB_FETCH_DOMAIN_OPEN", "true")
    ok, reason = url_allowed("https://news.ycombinator.com/")
    # domain check passes; remaining checks may pass or block on DNS env.
    # We accept either ok or non-domain block reason.
    assert ok or reason != "domain_not_allowed"
    # Loopback IP literal still blocked by CIDR even with domain open.
    ok, reason = url_allowed("https://127.0.0.1/")
    assert not ok and reason.startswith("ip_blocked")
