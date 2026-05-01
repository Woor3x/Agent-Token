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
    """Permanent 429 (retries exhausted) → sanitised LLM_UPSTREAM."""
    from agents.common.llm.volc import VolcArkProvider

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "0", "X-Request-Id": "req-xyz"},
            json={"error": {"code": "rate_limit", "message": "slow down"}},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-1")
    # max_attempts=1 keeps the test fast and asserts the sanitised error path
    # in isolation; the retry path is covered by the dedicated test below.
    provider = VolcArkProvider(http=http, max_attempts=1)
    with pytest.raises(LLMError) as ei:
        await provider.chat([ChatMessage(role="user", content="hi")])
    err = ei.value
    assert err.code == "LLM_UPSTREAM"
    assert err.status_code == 429
    # Sanitised: only status + upstream error code, never the full message body.
    assert "rate_limit" in str(err)
    assert "slow down" not in str(err)
    # Upstream id captured on the exception for log correlation.
    assert err.request_id == "req-xyz"
    # Full payload retained on .upstream for opt-in DEBUG.
    assert err.upstream == {"error": {"code": "rate_limit", "message": "slow down"}}
    await http.aclose()


@pytest.mark.asyncio
async def test_volc_provider_retries_then_succeeds(monkeypatch) -> None:
    """503 → 200 succeeds within max_attempts; call count = 2."""
    from agents.common.llm.volc import VolcArkProvider

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": {"code": "busy"}})
        return httpx.Response(
            200,
            headers={"X-Request-Id": "req-ok"},
            json={
                "id": "chat-ok",
                "model": "ep-1",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-1")
    provider = VolcArkProvider(http=http, max_attempts=3)
    res = await provider.chat([ChatMessage(role="user", content="hi")])
    await http.aclose()
    assert res.content == "hello"
    assert res.request_id == "req-ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_volc_provider_propagates_trace_id_header(monkeypatch) -> None:
    """trace_id arg → X-Client-Request-Id header, also surfaces on result."""
    from agents.common.llm.volc import VolcArkProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "model": "ep-1",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "OK",
                        "reasoning_content": "thinking step…",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-1")
    provider = VolcArkProvider(http=http)
    res = await provider.chat(
        [ChatMessage(role="user", content="hi")],
        trace_id="trace-abc-123",
    )
    await http.aclose()
    assert captured["headers"]["x-client-request-id"] == "trace-abc-123"
    assert res.reasoning == "thinking step…"
    # No upstream X-Request-Id → response.id used as fallback.
    assert res.request_id == "chat-1"


@pytest.mark.asyncio
async def test_volc_provider_json_mode_unsupported_fallback(monkeypatch) -> None:
    """Doubao seed-pro rejects ``response_format=json_object``; provider must
    auto-retry without it and cache the negative capability."""
    from agents.common.llm.volc import VolcArkProvider

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(
                400,
                json={"error": {
                    "code": "InvalidParameter",
                    "param": "response_format.type",
                    "message": "json_object is not supported by this model",
                }},
            )
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": body["model"],
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "{\"ok\":1}"},
                    "finish_reason": "stop",
                }],
                "usage": {},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-cot")
    provider = VolcArkProvider(http=http)
    # First call: probe + fallback in one transparent retry.
    res1 = await provider.chat([ChatMessage("user", "hi")], json_mode=True)
    assert res1.content == "{\"ok\":1}"
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    # Second call: capability cached → no probe round-trip.
    res2 = await provider.chat([ChatMessage("user", "hi again")], json_mode=True)
    assert res2.content == "{\"ok\":1}"
    assert len(calls) == 3
    assert "response_format" not in calls[2]
    await http.aclose()


@pytest.mark.asyncio
async def test_volc_provider_per_call_model_override(monkeypatch) -> None:
    """``model=`` argument wins over the constructor default."""
    from agents.common.llm.volc import VolcArkProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "x",
                "model": captured["body"]["model"],
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("ARK_API_KEY", "k")
    monkeypatch.setenv("ARK_MODEL", "ep-default")
    provider = VolcArkProvider(http=http)
    await provider.chat(
        [ChatMessage(role="user", content="hi")],
        model="ep-override-001",
    )
    await http.aclose()
    assert captured["body"]["model"] == "ep-override-001"
