"""Unit tests for circuit breaker and registry."""
import asyncio
import pytest
from routing.circuit_breaker import CircuitBreaker, State


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker("test_upstream", failure_threshold=3, open_duration=1)
        assert cb.state == State.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        cb = CircuitBreaker("test_upstream", failure_threshold=3, open_duration=10)
        for _ in range(3):
            await cb.on_failure()
        assert cb.state == State.OPEN

    @pytest.mark.asyncio
    async def test_open_raises_circuit_open_error(self):
        from errors import CircuitOpenError
        cb = CircuitBreaker("test_upstream", failure_threshold=1, open_duration=60)
        await cb.on_failure()
        assert cb.state == State.OPEN
        with pytest.raises(CircuitOpenError):
            await cb.before_call()

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_duration(self):
        import time
        from unittest.mock import patch
        cb = CircuitBreaker("test_upstream", failure_threshold=1, open_duration=0)
        await cb.on_failure()
        # open_duration=0 → immediately transitions
        await cb.before_call()
        assert cb.state == State.HALF_OPEN

    @pytest.mark.asyncio
    async def test_success_closes_from_half_open(self):
        cb = CircuitBreaker("test_upstream", failure_threshold=1, open_duration=0)
        await cb.on_failure()
        await cb.before_call()  # → half_open
        await cb.on_success()
        assert cb.state == State.CLOSED


class TestRegistry:
    @pytest.mark.asyncio
    async def test_load_from_yaml(self, tmp_path):
        import yaml
        from routing.registry import Registry
        yaml_path = tmp_path / "registry.yaml"
        yaml_path.write_text(yaml.dump({
            "agents": {
                "my_agent": {
                    "upstream": "http://my-agent:8001",
                    "timeout_ms": 5000,
                }
            }
        }))
        reg = Registry(str(yaml_path))
        await reg.load()
        cfg = reg.get("my_agent")
        assert cfg.upstream == "http://my-agent:8001"
        assert cfg.timeout_ms == 5000

    @pytest.mark.asyncio
    async def test_unknown_agent_raises(self, tmp_path):
        import yaml
        from routing.registry import Registry
        from errors import UpstreamError
        yaml_path = tmp_path / "registry.yaml"
        yaml_path.write_text(yaml.dump({"agents": {}}))
        reg = Registry(str(yaml_path))
        await reg.load()
        with pytest.raises(UpstreamError):
            reg.get("nonexistent")
