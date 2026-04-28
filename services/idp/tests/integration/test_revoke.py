"""
集成测试：6 粒度撤销系统
"""
import pytest
from storage.redis import sadd_member, sismember
from revoke.pubsub import broadcast_revoke


class TestRevokeRedisOps:
    async def test_sadd_and_check(self, fake_redis):
        await sadd_member("revoked:jtis", "tok-abc-123", ttl_sec=3600)
        assert await sismember("revoked:jtis", "tok-abc-123")
        assert not await sismember("revoked:jtis", "tok-other")

    async def test_revoke_agent(self, fake_redis):
        await sadd_member("revoked:agents", "data_agent")
        assert await sismember("revoked:agents", "data_agent")
        assert not await sismember("revoked:agents", "web_agent")

    async def test_all_six_granularities(self, fake_redis):
        granularities = [
            ("revoked:jtis",   "jti-001"),
            ("revoked:subs",   "alice"),
            ("revoked:agents", "bad_agent"),
            ("revoked:traces", "trace-xyz"),
            ("revoked:plans",  "plan-001"),
            ("revoked:chains", "chain-abc"),
        ]
        for set_key, value in granularities:
            await sadd_member(set_key, value)
            assert await sismember(set_key, value), f"Failed for {set_key}"

    async def test_pubsub_broadcast(self, fake_redis, monkeypatch):
        """broadcast_revoke 应以正确参数调用 publish。
        fakeredis 的 get_message(timeout=...) 不真正阻塞，
        所以改用 monkeypatch 直接拦截 publish 调用来断言。
        """
        import json
        import revoke.pubsub as pubsub_mod

        published: list[tuple[str, str]] = []

        async def mock_publish(channel: str, message: str) -> int:
            published.append((channel, message))
            return 1

        # patch 打在使用处（pubsub_mod.publish），不是定义处（storage.redis.publish）
        monkeypatch.setattr(pubsub_mod, "publish", mock_publish)

        await broadcast_revoke("agent", "malicious_agent", reason="security incident")

        assert len(published) == 1
        channel, raw = published[0]
        assert channel == "revoke"
        data = json.loads(raw)
        assert data["type"] == "agent"
        assert data["value"] == "malicious_agent"
        assert data["reason"] == "security incident"

    async def test_revoke_blocks_assertion(self, mem_db, fake_redis, tmp_kms):
        """撤销 agent 后，client_assertion 验证应该失败"""
        import json
        from datetime import datetime, timezone
        from tests.conftest import make_rsa_keypair, make_agent_assertion
        import storage.sqlite as db_mod
        from token_exchange.assertion import verify_client_assertion
        from errors import AgentRevoked

        kid = "block-test-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await db_mod.insert_agent({
            "agent_id": "block_agent", "role": "executor", "kid": kid,
            "public_jwk": json.dumps(public_jwk), "alg": "RS256", "status": "active",
            "registered_at": datetime.now(timezone.utc).isoformat(),
        })

        # 撤销前可以正常验证
        assertion1 = make_agent_assertion("block_agent", private_pem, kid)
        identity = await verify_client_assertion(assertion1)
        assert identity.agent_id == "block_agent"

        # 执行撤销
        await fake_redis.sadd("revoked:agents", "block_agent")

        # 撤销后应该被阻止
        assertion2 = make_agent_assertion("block_agent", private_pem, kid)  # 新 jti
        with pytest.raises(AgentRevoked):
            await verify_client_assertion(assertion2)
