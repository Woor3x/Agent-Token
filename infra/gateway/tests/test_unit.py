"""
tests/test_unit.py — Gateway 纯逻辑单元测试

不依赖 HTTP 栈，覆盖四个纯函数/状态机模块：
  * authz.delegation  — delegation chain 解析
  * routing.circuit_breaker — CB 状态转换
  * intent.schema / intent.parser_structured — intent 校验与解析
  * authz.one_shot    — one-shot token 消费语义
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock


# ─────────────────────────────────────────────────────────────────────────────
# Delegation chain
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegationVerifier:
    def test_no_delegation_returns_empty(self):
        """无 act 链的 token 应返回空 chain。"""
        from authz.delegation import verify_delegation
        claims = {"sub": "agent:doc"}
        chain = verify_delegation(claims, max_depth=4)
        assert chain == []

    def test_single_hop_ok(self):
        """单跳委托返回长度为 1 的 chain，不抛异常。"""
        from authz.delegation import verify_delegation
        claims = {"sub": "agent:doc", "act": {"sub": "agent:data"}}
        chain = verify_delegation(claims, max_depth=4)
        assert chain == ["agent:data"]


# ─────────────────────────────────────────────────────────────────────────────
# Circuit Breaker 状态机
# ─────────────────────────────────────────────────────────────────────────────

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        from routing.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker("t", failure_threshold=3, open_duration=1)
        assert cb.state == State.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        from routing.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker("t", failure_threshold=3, open_duration=10)
        for _ in range(3):
            await cb.on_failure()
        assert cb.state == State.OPEN

    @pytest.mark.asyncio
    async def test_open_raises_circuit_open_error(self):
        from routing.circuit_breaker import CircuitBreaker, State
        from errors import CircuitOpenError
        cb = CircuitBreaker("t", failure_threshold=1, open_duration=60)
        await cb.on_failure()
        with pytest.raises(CircuitOpenError):
            await cb.before_call()

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_duration(self):
        from routing.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker("t", failure_threshold=1, open_duration=0)
        await cb.on_failure()
        await cb.before_call()   # open_duration=0 → 立即进入 half_open
        assert cb.state == State.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_closes_from_half_open(self):
        from routing.circuit_breaker import CircuitBreaker, State
        cb = CircuitBreaker("t", failure_threshold=1, open_duration=0)
        await cb.on_failure()
        await cb.before_call()   # → half_open
        await cb.on_success()
        assert cb.state == State.CLOSED


# ─────────────────────────────────────────────────────────────────────────────
# Intent schema 校验
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentSchema:
    def test_valid_intent_passes(self):
        from intent.schema import validate_intent
        validate_intent({"action": "feishu.bitable.read", "resource": "app:abc/table:xyz"})

    def test_unknown_action_raises(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError) as exc_info:
            validate_intent({"action": "evil.action", "resource": "*"})
        assert "INTENT_INVALID" in str(exc_info.value.code)

    def test_missing_action_raises(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError):
            validate_intent({"resource": "*"})

    def test_missing_resource_raises(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search"})

    def test_extra_field_raises(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "*", "evil": "injection"})

    def test_resource_too_long_raises(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "a" * 513})

    def test_resource_pattern_invalid(self):
        from intent.schema import validate_intent
        from errors import IntentError
        with pytest.raises(IntentError):
            validate_intent({"action": "web.search", "resource": "ha ha spaces"})

    def test_all_valid_actions(self):
        from intent.schema import validate_intent
        actions = [
            "feishu.bitable.read", "feishu.contact.read", "feishu.calendar.read",
            "feishu.doc.write", "web.search", "web.fetch", "a2a.invoke", "orchestrate",
        ]
        for action in actions:
            validate_intent({"action": action, "resource": "*"})

    def test_params_optional(self):
        from intent.schema import validate_intent
        validate_intent({"action": "web.search", "resource": "*", "params": {"q": "hello"}})


# ─────────────────────────────────────────────────────────────────────────────
# Intent structured parser
# ─────────────────────────────────────────────────────────────────────────────

class TestIntentParser:
    def test_parse_valid_body(self):
        from intent.parser_structured import parse_structured
        intent = parse_structured({"intent": {"action": "web.search", "resource": "*"}})
        assert intent["action"] == "web.search"

    def test_missing_intent_key_raises(self):
        from intent.parser_structured import parse_structured
        from errors import IntentError
        with pytest.raises(IntentError):
            parse_structured({"not_intent": {}})

    def test_non_dict_intent_raises(self):
        from intent.parser_structured import parse_structured
        from errors import IntentError
        with pytest.raises(IntentError):
            parse_structured({"intent": "string_not_dict"})


# ─────────────────────────────────────────────────────────────────────────────
# One-shot token 消费
# ─────────────────────────────────────────────────────────────────────────────

class TestOneShot:
    @pytest.mark.asyncio
    async def test_first_consume_succeeds_second_raises(self):
        """首次消费成功，第二次抛 TOKEN_REPLAYED。"""
        from authz.one_shot import consume_one_shot
        from errors import AuthnError

        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        claims = {"jti": "jti-oneshot", "exp": int(time.time()) + 60}
        await consume_one_shot(redis, claims)

        redis.set = AsyncMock(return_value=False)
        with pytest.raises(AuthnError) as exc_info:
            await consume_one_shot(redis, claims)
        assert exc_info.value.code == "TOKEN_REPLAYED"

    @pytest.mark.asyncio
    async def test_concurrent_same_jti_only_one_wins(self):
        """并发同 jti：只有一个请求成功，其余抛 TOKEN_REPLAYED。"""
        from authz.one_shot import consume_one_shot
        from errors import AuthnError

        call_count = 0

        async def setnx_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return call_count == 1

        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=setnx_once)
        claims = {"jti": "jti-concurrent", "exp": int(time.time()) + 60}

        results = await asyncio.gather(
            consume_one_shot(redis, claims),
            consume_one_shot(redis, claims),
            return_exceptions=True,
        )
        successes = sum(1 for r in results if not isinstance(r, Exception))
        failures = sum(1 for r in results if isinstance(r, AuthnError))
        assert successes == 1
        assert failures == 1
