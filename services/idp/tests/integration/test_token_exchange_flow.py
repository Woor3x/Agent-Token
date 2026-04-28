"""
集成测试：token_exchange 10-phase 完整流程
使用内存 SQLite + fakeredis + 真实 KMS + 真实 RSA JWT
"""
import base64
import hashlib
import json
import time
import uuid

import pytest
from jose import jwt as jose_jwt

from tests.conftest import make_rsa_keypair, make_agent_assertion, make_user_token
from errors import (
    DelegationNotAllowed, EmptyEffectiveScope,
    ExecutorMismatch, InvalidRequest,
)
import storage.sqlite as db_mod
import storage.redis as redis_mod
from datetime import datetime, timezone

# ── 能力定义（复用 YAML 里的配置，测试时直接 mock loader）──────────────────
from agents.loader import AgentCapability, CapabilityEntry, DelegationConfig
import agents.loader as loader_mod


def _setup_capabilities():
    """模拟 load_capabilities 的结果"""
    doc_assistant = AgentCapability(
        agent_id="doc_assistant", role="orchestrator",
        capabilities=[
            CapabilityEntry(action="feishu.doc.write", resource_pattern="doc_token:*"),
            CapabilityEntry(action="a2a.invoke", resource_pattern="agent:data_agent"),
            CapabilityEntry(action="a2a.invoke", resource_pattern="agent:web_agent"),
        ],
        delegation=DelegationConfig(accept_from=["user"]),
    )
    data_agent = AgentCapability(
        agent_id="data_agent", role="executor",
        capabilities=[
            CapabilityEntry(action="feishu.bitable.read", resource_pattern="app_token:*/table:*"),
            CapabilityEntry(action="feishu.contact.read", resource_pattern="department:*"),
        ],
        delegation=DelegationConfig(accept_from=["doc_assistant"]),
    )
    web_agent = AgentCapability(
        agent_id="web_agent", role="executor",
        capabilities=[
            CapabilityEntry(action="web.search", resource_pattern="*"),
            CapabilityEntry(action="web.fetch", resource_pattern="https://*"),
        ],
        delegation=DelegationConfig(accept_from=["doc_assistant"]),
    )
    loader_mod._capabilities = {
        "doc_assistant": doc_assistant,
        "data_agent": data_agent,
        "web_agent": web_agent,
    }


async def _setup_orchestrator(kid: str) -> tuple[bytes, dict]:
    """向 DB 注册 doc_assistant，返回 (private_pem, public_jwk)"""
    private_pem, public_jwk = make_rsa_keypair(kid)
    await db_mod.insert_agent({
        "agent_id": "doc_assistant", "role": "orchestrator", "kid": kid,
        "public_jwk": json.dumps(public_jwk), "alg": "RS256", "status": "active",
        "registered_at": datetime.now(timezone.utc).isoformat(),
    })
    return private_pem, public_jwk


async def _setup_user_perms():
    """向 DB 写入 alice 的权限"""
    await db_mod.upsert_user({
        "user_id": "alice",
        "password_hash": None,
        "permissions": [
            {"action": "feishu.bitable.read", "resource_pattern": "app_token:*/table:*"},
            {"action": "feishu.doc.write",     "resource_pattern": "doc_token:*"},
            {"action": "web.search",            "resource_pattern": "*"},
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


class TestTokenExchangeCore:
    """直接调用 token_exchange handler 的各 phase，不经过 HTTP"""

    @pytest.fixture(autouse=True)
    async def setup(self, mem_db, fake_redis, tmp_kms, audit_writer):
        _setup_capabilities()
        self.kid = "orch-key-v1"
        self.private_pem, self.public_jwk = await _setup_orchestrator(self.kid)
        self.idp_kms = tmp_kms
        await _setup_user_perms()

    def _make_subject_token(self) -> str:
        """用 IdP KMS 签发合法的 user access_token"""
        sk = self.idp_kms.get_active_signing_key()
        return make_user_token("alice", sk.private_pem, sk.kid)

    def _make_assertion(self, agent_id: str = "doc_assistant",
                        jti: str = None, exp_delta: int = 300) -> str:
        return make_agent_assertion(agent_id, self.private_pem, self.kid,
                                    jti=jti, exp_delta=exp_delta)

    # ── Phase 1+2: assertion + subject_token ─────────────────────────────
    async def test_verify_assertion_and_subject_token(self):
        from token_exchange.assertion import verify_client_assertion
        from token_exchange.subject_token import verify_subject_token

        assertion = self._make_assertion()
        identity = await verify_client_assertion(assertion)
        assert identity.agent_id == "doc_assistant"

        subject_token = self._make_subject_token()
        user = await verify_subject_token(subject_token)
        assert user.sub == "alice"
        assert "agent:invoke" in user.scope

    # ── Phase 4: scope 解析 ───────────────────────────────────────────────
    async def test_parse_valid_scope(self):
        from token_exchange.intent import parse_scope
        action, resource = parse_scope("feishu.bitable.read:app_token:x/table:y")
        assert action == "feishu.bitable.read"

    # ── Phase 5: delegation check ─────────────────────────────────────────
    async def test_delegation_allowed(self):
        from token_exchange.delegation import check_delegation, check_orchestrator_can_invoke
        from agents.loader import get_agent_capability
        callee_cap = get_agent_capability("data_agent")
        orch_cap   = get_agent_capability("doc_assistant")
        check_orchestrator_can_invoke(orch_cap, "data_agent")   # 不抛异常
        check_delegation("doc_assistant", "data_agent", callee_cap)  # 不抛异常

    async def test_delegation_denied_wrong_invoker(self):
        from token_exchange.delegation import check_delegation
        from agents.loader import get_agent_capability
        callee_cap = get_agent_capability("data_agent")
        with pytest.raises(DelegationNotAllowed):
            check_delegation("web_agent", "data_agent", callee_cap)  # web_agent 无权调 data_agent

    # ── Phase 6: executor check ───────────────────────────────────────────
    async def test_executor_correct(self):
        from token_exchange.executor import check_executor
        check_executor("data_agent", "feishu.bitable.read")  # 不抛异常

    async def test_executor_wrong_raises(self):
        from token_exchange.executor import check_executor
        with pytest.raises(ExecutorMismatch):
            check_executor("web_agent", "feishu.bitable.read")  # web_agent 不是 bitable 的执行者

    # ── Phase 7: intersect ────────────────────────────────────────────────
    async def test_intersect_produces_scope(self):
        from token_exchange.intersect import intersect
        from users.perms import load_permissions
        from agents.loader import get_agent_capability

        callee_cap = get_agent_capability("data_agent")
        callee_caps_raw = [{"action": c.action, "resource_pattern": c.resource_pattern}
                           for c in callee_cap.capabilities]
        user_perms = await load_permissions("alice")
        result = intersect(callee_caps_raw, user_perms,
                           [("feishu.bitable.read", "app_token:x/table:y")])
        assert result == ["feishu.bitable.read:app_token:x/table:y"]

    async def test_intersect_empty_when_user_has_no_perm(self):
        from token_exchange.intersect import intersect
        from agents.loader import get_agent_capability

        callee_cap = get_agent_capability("data_agent")
        callee_caps_raw = [{"action": c.action, "resource_pattern": c.resource_pattern}
                           for c in callee_cap.capabilities]
        user_perms = []  # 用户没有任何权限
        result = intersect(callee_caps_raw, user_perms,
                           [("feishu.bitable.read", "app_token:x/table:y")])
        assert result == []

    # ── Phase 10: 签发 token ──────────────────────────────────────────────
    async def test_sign_delegated_token(self):
        from token_exchange.signer import sign_delegated_token
        claims = {
            "sub": "alice", "aud": "data_agent",
            "act": {"sub": "doc_assistant"},
            "scope": "feishu.bitable.read:app_token:x/table:y",
        }
        token, jti = sign_delegated_token(claims)
        assert token
        assert jti

        # 用 JWKS 解码验证
        sk = self.idp_kms.get_active_signing_key()
        decoded = jose_jwt.decode(
            token, sk.public_jwk, algorithms=["RS256"],
            options={"verify_aud": False}
        )
        assert decoded["sub"] == "alice"
        assert decoded["one_time"] is True
        assert decoded["exp"] - decoded["iat"] == 120  # TTL=120s
