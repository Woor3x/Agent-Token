"""LLM provider unit tests: factory, mock behavior, Volc Ark request shape."""
from __future__ import annotations

import json

import httpx
import pytest

from agents.common.llm import (
    ChatMessage,
    LLMError,
    MockLLMProvider,
    make_llm,
)


# ---- factory & mock ----------------------------------------------------------


def test_factory_default_is_mock(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    llm = make_llm()
    assert llm.name == "mock"


def test_factory_unknown_raises() -> None:
    with pytest.raises(ValueError):
        make_llm(provider="banana")


def test_factory_volc_requires_keys(monkeypatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("ARK_MODEL", raising=False)
    with pytest.raises(LLMError) as ei:
        make_llm(provider="volc")
    assert ei.value.code == "LLM_CONFIG"


@pytest.mark.asyncio
async def test_mock_canned_responses() -> None:
    llm = MockLLMProvider(responses=["one", "two"])
    a = await llm.chat([ChatMessage(role="user", content="hi")])
    b = await llm.chat([ChatMessage(role="user", content="hi")])
    c = await llm.chat([ChatMessage(role="user", content="hi")])
    assert a.content == "one"
    assert b.content == "two"
    assert c.content == "one"  # cycles


@pytest.mark.asyncio
async def test_mock_rule_callable() -> None:
    llm = MockLLMProvider(rule=lambda msgs: f"echoing {msgs[-1].content}")
    res = await llm.chat([ChatMessage(role="user", content="ping")])
    assert res.content == "echoing ping"


@pytest.mark.asyncio
async def test_mock_default_echo() -> None:
    llm = MockLLMProvider()
    res = await llm.chat(
        [
            ChatMessage(role="system", content="ignored"),
            ChatMessage(role="user", content="hello world"),
        ]
    )
    assert res.content.startswith("[mock] hello world")


# ---- Volc Ark provider via httpx mock ---------------------------------------


def _ark_app_factory(captured: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "model": captured["body"]["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "你好，世界。"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_volc_provider_builds_correct_request(monkeypatch) -> None:
    from agents.common.llm.volc import VolcArkProvider

    captured: dict = {}
    transport = _ark_app_factory(captured)
    http = httpx.AsyncClient(transport=transport)
    monkeypatch.setenv("ARK_API_KEY", "secret-test")
    monkeypatch.setenv("ARK_MODEL", "ep-test-001")
    provider = VolcArkProvider(http=http)

    res = await provider.chat(
        messages=[
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="hi"),
        ],
        temperature=0.4,
        max_tokens=128,
        json_mode=True,
    )
    await http.aclose()

    assert res.content == "你好，世界。"
    assert res.usage["total_tokens"] == 16
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer secret-test"
    assert captured["body"]["model"] == "ep-test-001"
    assert captured["body"]["temperature"] == 0.4
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_volc_provider_propagates_upstream_error(monkeypatch) -> None:
    from agents.common.llm.volc import VolcArkProvider

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "rate_limit", "message": "slow down"}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-1")
    provider = VolcArkProvider(http=http)
    with pytest.raises(LLMError) as ei:
        await provider.chat([ChatMessage(role="user", content="hi")])
    assert ei.value.code == "LLM_UPSTREAM"
    assert ei.value.status_code == 429
    await http.aclose()
