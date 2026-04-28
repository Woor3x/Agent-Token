"""
集成测试：token_exchange/assertion.py
使用真实 JWT + 内存 SQLite + fakeredis
"""
import json
import time
import uuid

import pytest

from tests.conftest import make_rsa_keypair, make_agent_assertion
from token_exchange.assertion import verify_client_assertion
from errors import (
    AssertionReplay, AssertionTooLong, AgentRevoked,
    InvalidClient, SubIssMismatch,
)
import storage.sqlite as db_mod
import storage.redis as redis_mod


# ── 辅助：把 agent 写入内存 DB ─────────────────────────────────────────────
async def _insert_test_agent(agent_id: str, kid: str, public_jwk: dict,
                              status: str = "active") -> None:
    from datetime import datetime, timezone
    await db_mod.insert_agent({
        "agent_id": agent_id, "role": "executor", "kid": kid,
        "public_jwk": json.dumps(public_jwk),
        "alg": "RS256", "status": status,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    })


class TestVerifyClientAssertion:
    async def test_happy_path(self, mem_db, fake_redis):
        kid = "agent-test-v1"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("test_agent", kid, public_jwk)

        assertion = make_agent_assertion("test_agent", private_pem, kid)
        identity = await verify_client_assertion(assertion)

        assert identity.agent_id == "test_agent"
        assert identity.kid == kid

    async def test_unknown_kid_raises(self, mem_db, fake_redis):
        kid = "nonexistent-kid"
        private_pem, _ = make_rsa_keypair(kid)
        assertion = make_agent_assertion("nobody", private_pem, kid)

        with pytest.raises(InvalidClient, match="No agent found"):
            await verify_client_assertion(assertion)

    async def test_inactive_agent_raises(self, mem_db, fake_redis):
        kid = "revoked-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("dead_agent", kid, public_jwk, status="revoked")

        assertion = make_agent_assertion("dead_agent", private_pem, kid)
        with pytest.raises(InvalidClient, match="not active"):
            await verify_client_assertion(assertion)

    async def test_assertion_replay_raises(self, mem_db, fake_redis):
        kid = "replay-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("replay_agent", kid, public_jwk)

        shared_jti = str(uuid.uuid4())
        assertion = make_agent_assertion("replay_agent", private_pem, kid, jti=shared_jti)

        # 第一次：成功
        await verify_client_assertion(assertion)

        # 第二次：相同 jti → 重放攻击
        with pytest.raises(AssertionReplay):
            await verify_client_assertion(assertion)

    async def test_assertion_too_long_raises(self, mem_db, fake_redis):
        kid = "long-lived-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("long_agent", kid, public_jwk)

        # exp - iat = 700s > 600s → 超长
        assertion = make_agent_assertion("long_agent", private_pem, kid, exp_delta=700)
        with pytest.raises(AssertionTooLong):
            await verify_client_assertion(assertion)

    async def test_wrong_audience_raises(self, mem_db, fake_redis):
        kid = "wrong-aud-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("aud_agent", kid, public_jwk)

        assertion = make_agent_assertion(
            "aud_agent", private_pem, kid,
            audience="https://wrong-service/token"  # 错误的 audience
        )
        with pytest.raises(InvalidClient, match="verification failed"):
            await verify_client_assertion(assertion)

    async def test_revoked_agent_raises(self, mem_db, fake_redis):
        kid = "ok-kid"
        private_pem, public_jwk = make_rsa_keypair(kid)
        await _insert_test_agent("revoked_in_redis", kid, public_jwk)

        # 在 Redis 撤销集合中标记该 agent
        await fake_redis.sadd("revoked:agents", "revoked_in_redis")

        assertion = make_agent_assertion("revoked_in_redis", private_pem, kid)
        with pytest.raises(AgentRevoked):
            await verify_client_assertion(assertion)
