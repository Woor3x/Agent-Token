"""Unit tests for authz components — delegation, one-shot, OPA client."""
import pytest
from unittest.mock import AsyncMock

from authz.delegation import verify_delegation
from errors import AuthzError, AuthnError


class TestDelegationVerifier:
    def test_no_delegation_returns_empty(self):
        claims = {"sub": "agent:doc"}
        chain = verify_delegation(claims, max_depth=4)
        assert chain == []

    def test_single_hop_ok(self):
        claims = {"sub": "agent:doc", "act": {"sub": "agent:data"}}
        chain = verify_delegation(claims, max_depth=4)
        assert chain == ["agent:data"]

    def test_exceeds_max_depth_raises(self):
        # build chain of 5
        act = {"sub": "a5"}
        for i in range(4, 0, -1):
            act = {"sub": f"a{i}", "act": act}
        claims = {"sub": "a0", "act": act}
        with pytest.raises(AuthzError) as exc_info:
            verify_delegation(claims, max_depth=4)
        assert exc_info.value.code == "AUTHZ_DEPTH_EXCEEDED"

    def test_cycle_detection_raises(self):
        # a → b → a
        claims = {
            "sub": "agent:a",
            "act": {"sub": "agent:b", "act": {"sub": "agent:a"}},
        }
        with pytest.raises(AuthzError) as exc_info:
            verify_delegation(claims, max_depth=10)
        assert exc_info.value.code == "AUTHZ_DELEGATION_REJECTED"


class TestOneShot:
    @pytest.mark.asyncio
    async def test_consume_success(self):
        from authz.one_shot import consume_one_shot
        import time
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        claims = {"jti": "jti-123", "exp": int(time.time()) + 300}
        await consume_one_shot(redis, claims)
        redis.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consume_replay_raises(self):
        from authz.one_shot import consume_one_shot
        import time
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=False)  # already consumed
        claims = {"jti": "jti-used", "exp": int(time.time()) + 300}
        with pytest.raises(AuthnError) as exc_info:
            await consume_one_shot(redis, claims)
        assert exc_info.value.code == "TOKEN_REPLAYED"


class TestOpaClient:
    @pytest.mark.asyncio
    async def test_allow_response(self):
        import httpx
        from unittest.mock import patch, AsyncMock
        from authz.opa_client import check_authz

        mock_response = AsyncMock()
        mock_response.raise_for_status = AsyncMock()
        mock_response.json = lambda: {"result": {"allow": True, "reasons": []}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            allow, reasons = await check_authz({}, {"action": "web.search", "resource": "*"}, "web_agent", {})
            assert allow is True
            assert reasons == []

    @pytest.mark.asyncio
    async def test_opa_unreachable_fail_closed(self):
        from unittest.mock import patch
        from authz.opa_client import check_authz
        import httpx

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            allow, reasons = await check_authz({}, {}, "any", {})
            assert allow is False
            assert "opa_unavailable" in reasons
