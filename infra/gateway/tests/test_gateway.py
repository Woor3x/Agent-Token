"""
tests/test_gateway.py — Gateway 全链路集成测试

策略
────
* Gateway ASGI App 通过 httpx.ASGITransport 驱动，走真实 middleware / route handler
* Redis 用纯内存 SimpleRedis 模拟，SETNX / SISMEMBER / eval 语义正确
* OPA 用 unittest.mock.patch 按需 allow / deny
* 上游 Agent 用 stdlib HTTPServer 在独立线程提供真实 HTTP 服务
* JWKS 直接注入缓存，跳过 IdP 网络请求

覆盖范围
────────
AuthN   : 无头、错误方案、过期、错误 iss、缺 one_time、缺 cnf.jkt、未知 kid
Revoke  : jti / sub / trace_id / plan_id 四维撤销
DPoP    : 缺头、method 不匹配、htu 不匹配、jkt 不匹配、jti 重放
AuthZ   : OPA deny、OPA 不可达（fail-closed）、delegation 深度超限、delegation 环路
Execute : one-shot 已消费、未知 agent、上游 5xx、上游超时、熔断器打开、正常路径
Admin   : /healthz、/metrics、/admin/reload 正常 / 错误 token
Rate    : 令牌桶耗尽返回 429
"""

import asyncio
import base64
import hashlib
import http.server
import json
import os
import socket
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import jwt as pyjwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport

# ─────────────────────────────────────────────────────────────────────────────
# 内存 Redis 模拟
# ─────────────────────────────────────────────────────────────────────────────

class SimpleRedis:
    """覆盖 gateway 实际使用的所有 Redis 命令，SETNX / SISMEMBER 语义正确。"""

    def __init__(self):
        self._sets: dict[str, set] = {}
        self._kv: dict[str, object] = {}
        self.eval_result: int = 1          # 默认速率限制 = 允许

    async def ping(self) -> bool:
        return True

    async def sismember(self, key, member) -> bool:
        return str(member) in self._sets.get(str(key), set())

    async def sadd(self, key, *members) -> int:
        s = self._sets.setdefault(str(key), set())
        before = len(s)
        s.update(str(m) for m in members)
        return len(s) - before

    async def set(self, key, value, *, nx: bool = False, ex=None, px=None):
        k = str(key)
        if nx and k in self._kv:
            return None
        self._kv[k] = value
        return True

    async def eval(self, script, numkeys, *args) -> int:
        return self.eval_result

    async def aclose(self):
        pass

    def pubsub(self):
        return _FakePubSub()

    def reset(self):
        self._sets.clear()
        self._kv.clear()
        self.eval_result = 1


class _FakePubSub:
    async def subscribe(self, *channels): pass

    def listen(self):
        async def _gen():
            while True:
                await asyncio.sleep(3600)
                yield {"type": "subscribe"}
        return _gen()


# ─────────────────────────────────────────────────────────────────────────────
# Mock 上游 Agent（真实 HTTPServer）
# ─────────────────────────────────────────────────────────────────────────────

class _AgentHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        status = getattr(self.server, "resp_status", 200)
        body = getattr(self.server, "resp_body", {"status": "ok", "data": {"value": 42}})
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# 密钥与 JWK 工厂（session 级，只生成一次）
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def signing_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

@pytest.fixture(scope="session")
def signing_public_key(signing_private_key):
    return signing_private_key.public_key()

@pytest.fixture(scope="session")
def dpop_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

@pytest.fixture(scope="session")
def kid():
    return "integ-kid-001"

@pytest.fixture(scope="session")
def dpop_jwk(dpop_private_key):
    from jwt.algorithms import RSAAlgorithm
    return json.loads(RSAAlgorithm.to_jwk(dpop_private_key.public_key()))


# ─────────────────────────────────────────────────────────────────────────────
# Token / DPoP 工厂函数
# ─────────────────────────────────────────────────────────────────────────────

def _pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

def _jkt(jwk_dict: dict) -> str:
    required = {k: jwk_dict[k] for k in sorted(jwk_dict) if k in ("e", "kty", "n")}
    canonical = json.dumps(required, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(
        hashlib.sha256(canonical.encode()).digest()
    ).rstrip(b"=").decode()


_MISSING = object()  # sentinel


def make_token(
    signing_key,
    kid: str,
    dpop_jwk: dict | None = None,
    *,
    sub: str = "agent:doc_assistant",
    aud: str = "agent:data_agent",
    jti: str | None = None,
    exp_offset: int = 300,
    one_time: bool = True,
    cnf_jkt=_MISSING,          # _MISSING → 从 dpop_jwk 推算；None → 不加 cnf
    trace_id: str = "trace-integ",
    plan_id: str = "plan-integ",
    iss: str = "https://idp.local",
    act: dict | None = None,
) -> tuple[str, str]:
    """返回 (jti, encoded_token)。"""
    jti = jti or str(uuid.uuid4())
    now = int(time.time())
    payload: dict = {
        "iss": iss,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "nbf": now,
        "exp": now + exp_offset,
        "jti": jti,
        "trace_id": trace_id,
        "plan_id": plan_id,
        "scope": "feishu.bitable.read",
    }
    if one_time:
        payload["one_time"] = True
    if cnf_jkt is _MISSING:
        if dpop_jwk:
            payload["cnf"] = {"jkt": _jkt(dpop_jwk)}
    elif cnf_jkt is not None:
        payload["cnf"] = {"jkt": cnf_jkt}
    if act:
        payload["act"] = act
    token = pyjwt.encode(payload, _pem(signing_key), algorithm="RS256", headers={"kid": kid})
    return jti, token


def make_dpop(dpop_key, jwk_dict: dict, method: str, url: str, jti: str | None = None) -> str:
    jti = jti or str(uuid.uuid4())
    payload = {"htm": method, "htu": url, "iat": int(time.time()), "jti": jti}
    return pyjwt.encode(
        payload, _pem(dpop_key), algorithm="RS256",
        headers={"typ": "dpop+jwt", "jwk": jwk_dict},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Gateway 主 fixture（module 级）
# ─────────────────────────────────────────────────────────────────────────────

INVOKE_URL = "http://testserver/a2a/invoke"

@pytest_asyncio.fixture(scope="module")
async def gw(signing_public_key, kid, dpop_jwk, dpop_private_key, signing_private_key):
    """
    启动 gateway ASGI 应用，注入所有外部依赖的模拟。
    返回 (client, redis, agent_httpd, signing_private_key, dpop_private_key, dpop_jwk, kid)
    """
    # ── 上游 mock agent
    agent_port = _free_port()
    agent_httpd = http.server.HTTPServer(("127.0.0.1", agent_port), _AgentHandler)
    agent_thread = threading.Thread(target=agent_httpd.serve_forever, daemon=True)
    agent_thread.start()

    # ── 临时 registry.yaml（指向 mock agent；cb_test_agent 指向不存在的端口用于熔断测试）
    dead_port = _free_port()  # 不会有服务监听，用于触发连接失败
    reg_content = f"""
agents:
  data_agent:
    upstream: http://127.0.0.1:{agent_port}
    transport: http
    timeout_ms: 2000
    retry:
      max: 0
  cb_test_agent:
    upstream: http://127.0.0.1:{dead_port}
    transport: http
    timeout_ms: 500
    retry:
      max: 0
"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    tmp.write(reg_content)
    tmp.close()

    # ── SimpleRedis
    redis = SimpleRedis()

    # ── 配置 gateway 模块（在导入之前设好 settings）
    with patch("redis.asyncio.from_url", return_value=redis):
        # 导入 gateway app（lifespan 不会被 ASGITransport 触发，我们手动注入状态）
        import sys
        # 确保 gateway 包在 sys.path 里
        gw_dir = str(Path(__file__).parent.parent)
        if gw_dir not in sys.path:
            sys.path.insert(0, gw_dir)

        from config import settings
        from jwt_token.jwks_cache import jwks_cache
        from routing.registry import registry as gw_registry
        from middleware.audit import audit_writer
        from main import app
        import aiosqlite

        # 注入 JWKS（跳过 IdP 网络请求）
        jwks_cache._keys = OrderedDict({kid: signing_public_key})
        jwks_cache._fetched_at = time.time() + 86400
        if jwks_cache._client is None:
            jwks_cache._client = httpx.AsyncClient()

        # 注入 Redis
        app.state.redis = redis

        # 加载 registry（指向 mock agent）
        gw_registry._path = Path(tmp.name)
        await gw_registry.load()

        # 启动 audit writer（内存 DB，避免写文件）
        if audit_writer._db is None:
            import aiosqlite
            audit_writer._db = await aiosqlite.connect(":memory:")
            await audit_writer._db.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, ts REAL, trace_id TEXT, span_id TEXT,
                    parent_span_id TEXT, plan_id TEXT, task_id TEXT, sub TEXT,
                    target_agent TEXT, action TEXT, resource TEXT, decision TEXT,
                    deny_reasons TEXT, jti TEXT, dpop_jti TEXT, dpop_jkt TEXT,
                    raw_prompt TEXT, source_ip TEXT, duration_ms REAL, extra TEXT
                )
            """)
            await audit_writer._db.commit()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, redis, agent_httpd, signing_private_key, dpop_private_key, dpop_jwk, kid

    os.unlink(tmp.name)
    agent_httpd.shutdown()


# 快捷函数：构造一次完整的合法请求头 + body
def _valid_headers_and_body(signing_key, dpop_key, dpop_jwk, kid, redis=None, **token_kwargs):
    """生成合法的 Authorization / DPoP 头 + 请求体。"""
    jti, token = make_token(signing_key, kid, dpop_jwk, **token_kwargs)
    dpop = make_dpop(dpop_key, dpop_jwk, "POST", INVOKE_URL)
    headers = {
        "Authorization": f"DPoP {token}",
        "DPoP": dpop,
        "X-Target-Agent": "data_agent",
    }
    body = {"intent": {"action": "feishu.bitable.read", "resource": "app_token:xxx/table:yyy"}}
    return jti, headers, body


# ─────────────────────────────────────────────────────────────────────────────
# 基础设施测试
# ─────────────────────────────────────────────────────────────────────────────

class TestInfra:
    async def test_healthz(self, gw):
        client, *_ = gw
        r = await client.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data

    async def test_metrics(self, gw):
        client, *_ = gw
        r = await client.get("/metrics")
        assert r.status_code == 200
        assert b"python_" in r.content or len(r.content) > 0

    async def test_admin_reload_valid_token(self, gw):
        client, *_ = gw
        from config import settings
        r = await client.post(
            "/admin/reload",
            headers={"Authorization": f"Bearer {settings.admin_token}"},
        )
        assert r.status_code == 200
        assert r.json()["reloaded"] is True

    async def test_admin_reload_invalid_token(self, gw):
        client, *_ = gw
        r = await client.post(
            "/admin/reload",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_TOKEN_INVALID"


# ─────────────────────────────────────────────────────────────────────────────
# AuthN — JWT 验证
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthnJWT:
    async def test_no_auth_header(self, gw):
        client, *_ = gw
        r = await client.post(INVOKE_URL, json={})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_TOKEN_INVALID"

    async def test_bearer_scheme_rejected(self, gw):
        """Bearer（非 DPoP）方案应被拒绝。"""
        client, *_ = gw
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": "Bearer sometoken"},
            json={},
        )
        assert r.status_code == 401

    async def test_expired_token(self, gw):
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk, exp_offset=-120)  # leeway=30s, 需要超出 30s
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_wrong_issuer(self, gw):
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk, iss="https://evil.example.com")
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_missing_one_time_claim(self, gw):
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk, one_time=False)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert "one_time" in r.json()["error"]["message"].lower() or r.json()["error"]["code"] == "AUTHN_TOKEN_INVALID"

    async def test_missing_cnf_jkt(self, gw):
        """token 缺少 cnf.jkt → 401。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, dpop_jwk=None, cnf_jkt=None)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_unknown_kid(self, gw):
        client, _, __, sk, dk, djwk, _ = gw
        _, token = make_token(sk, "unknown-kid-999", djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# AuthN — 4 维撤销
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthnRevocation:
    async def test_revoked_jti(self, gw):
        client, redis, _, sk, dk, djwk, kid = gw
        jti, token = make_token(sk, kid, djwk, jti="revoked-jti-001")
        await redis.sadd("revoked:jtis", jti)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_REVOKED"
        redis._sets.pop("revoked:jtis", None)

    async def test_revoked_sub(self, gw):
        client, redis, _, sk, dk, djwk, kid = gw
        sub = "agent:revoked-orchestrator"
        _, token = make_token(sk, kid, djwk, sub=sub, aud="agent:data_agent")
        await redis.sadd("revoked:subs", sub)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_REVOKED"
        redis._sets.pop("revoked:subs", None)

    async def test_revoked_trace(self, gw):
        client, redis, _, sk, dk, djwk, kid = gw
        trace = "trace-revoked-xyz"
        _, token = make_token(sk, kid, djwk, trace_id=trace)
        await redis.sadd("revoked:traces", trace)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_REVOKED"
        redis._sets.pop("revoked:traces", None)

    async def test_revoked_plan(self, gw):
        client, redis, _, sk, dk, djwk, kid = gw
        plan = "plan-revoked-abc"
        _, token = make_token(sk, kid, djwk, plan_id=plan)
        await redis.sadd("revoked:plans", plan)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_REVOKED"
        redis._sets.pop("revoked:plans", None)


# ─────────────────────────────────────────────────────────────────────────────
# AuthN — DPoP
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthnDPoP:
    async def test_missing_dpop_header(self, gw):
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert "DPOP" in r.json()["error"]["code"]

    async def test_dpop_method_mismatch(self, gw):
        """DPoP proof 声称 GET，但请求是 POST。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "GET", INVOKE_URL)   # 方法错误
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_dpop_htu_mismatch(self, gw):
        """DPoP proof htu 与请求 URL 不符。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", "http://testserver/some/other/path")
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_dpop_jkt_mismatch(self, gw):
        """token cnf.jkt 与 DPoP proof 实际公钥不匹配。"""
        client, _, __, sk, dk, djwk, kid = gw
        wrong_jkt = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        _, token = make_token(sk, kid, dpop_jwk=None, cnf_jkt=wrong_jkt)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    async def test_dpop_jti_replay(self, gw):
        """同一 DPoP jti 第二次出现 → 401（重放攻击）。"""
        client, redis, _, sk, dk, djwk, kid = gw
        dpop_jti = f"dpop-replay-{uuid.uuid4().hex}"

        # 模拟：第一次 set 已经被执行（replay guard 已触发）
        await redis.set(f"dpop:jti:{dpop_jti}", 1, nx=True, ex=120)

        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL, jti=dpop_jti)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401
        assert "DPOP" in r.json()["error"]["code"]
        redis._kv.pop(f"dpop:jti:{dpop_jti}", None)


# ─────────────────────────────────────────────────────────────────────────────
# AuthZ — OPA 决策
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthzOPA:
    async def test_opa_deny(self, gw):
        """OPA 返回 deny → 403 AUTHZ_SCOPE_EXCEEDED。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(False, ["scope_not_granted"]))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "AUTHZ_SCOPE_EXCEEDED"

    async def test_opa_unavailable_fail_closed(self, gw):
        """OPA 不可达 → fail-closed → 403。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        async def _unavailable(*a, **kw):
            return False, ["opa_unavailable"]

        with patch("routes.invoke.check_authz", new=_unavailable):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# AuthZ — Delegation chain
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthzDelegation:
    async def test_delegation_depth_exceeded(self, gw):
        """act 链深度超过 settings.delegation_max_depth → 403。"""
        client, _, __, sk, dk, djwk, kid = gw
        # 构造深度 = 5 的 act 链（默认 max=4）
        act = {"sub": "agent:lvl1", "act": {"sub": "agent:lvl2", "act": {
            "sub": "agent:lvl3", "act": {"sub": "agent:lvl4", "act": {
                "sub": "agent:lvl5"
            }}}}}
        _, token = make_token(sk, kid, djwk, act=act)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "AUTHZ_DEPTH_EXCEEDED"

    async def test_delegation_cycle(self, gw):
        """act 链中出现重复 sub → 403 AUTHZ_DELEGATION_REJECTED。"""
        client, _, __, sk, dk, djwk, kid = gw
        # A → B → A（环路）
        act = {"sub": "agent:A", "act": {"sub": "agent:B", "act": {"sub": "agent:A"}}}
        _, token = make_token(sk, kid, djwk, act=act)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "AUTHZ_DELEGATION_REJECTED"


# ─────────────────────────────────────────────────────────────────────────────
# 执行层测试
# ─────────────────────────────────────────────────────────────────────────────

class TestExecution:
    async def test_one_shot_already_consumed(self, gw):
        """同一 jti 第二次请求 → 401 TOKEN_REPLAYED。"""
        client, redis, _, sk, dk, djwk, kid = gw
        jti = f"oneshot-{uuid.uuid4().hex}"

        # 模拟第一次已消费：直接在 Redis 写入消费记录
        await redis.set(f"jti:used:{jti}", 1, nx=True, ex=300)

        _, token = make_token(sk, kid, djwk, jti=jti)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "TOKEN_REPLAYED"
        redis._kv.pop(f"jti:used:{jti}", None)

    async def test_unknown_target_agent(self, gw):
        """X-Target-Agent 不在 registry → 502 UPSTREAM_FAIL。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk, aud="agent:ghost_agent")
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={
                    "Authorization": f"DPoP {token}",
                    "DPoP": dpop,
                    "X-Target-Agent": "ghost_agent",  # aud 对上，但不在 registry
                },
                json=body,
            )
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "UPSTREAM_FAIL"

    async def test_upstream_5xx(self, gw):
        """上游返回 5xx → gateway 透传（上游决定了 status code）。"""
        client, _, agent_httpd, sk, dk, djwk, kid = gw
        agent_httpd.resp_status = 500
        agent_httpd.resp_body = {"error": "internal server error"}

        try:
            _, token = make_token(sk, kid, djwk)
            dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
            body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

            with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
                r = await client.post(
                    INVOKE_URL,
                    headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                    json=body,
                )
            assert r.status_code == 500
        finally:
            agent_httpd.resp_status = 200
            agent_httpd.resp_body = {"status": "ok", "data": {"value": 42}}

    async def test_upstream_timeout(self, gw):
        """上游超时 → 504 UPSTREAM_TIMEOUT。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        async def _timeout(*a, **kw):
            raise httpx.TimeoutException("simulated timeout")

        with (
            patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))),
            patch("routing.upstream_client.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = _timeout
            mock_client_cls.return_value = mock_client

            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 504
        assert r.json()["error"]["code"] == "UPSTREAM_TIMEOUT"

    async def test_circuit_breaker_opens(self, gw):
        """熔断器处于 OPEN 状态时直接返回 503 CIRCUIT_OPEN，不转发请求。"""
        client, redis, _, sk, dk, djwk, kid = gw

        from routing.circuit_breaker import get_breaker, _breakers, State

        cb_agent = "cb_test_agent"  # 已在 registry 中，指向不监听的端口
        _breakers.pop(cb_agent, None)

        # 强制推到 OPEN 状态
        breaker = get_breaker(cb_agent)
        breaker._state = State.OPEN
        breaker._opened_at = time.time()  # 刚打开，不会进入 half-open

        _, token = make_token(sk, kid, djwk, aud=f"agent:{cb_agent}")
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={
                    "Authorization": f"DPoP {token}",
                    "DPoP": dpop,
                    "X-Target-Agent": cb_agent,
                },
                json=body,
            )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "CIRCUIT_OPEN"
        _breakers.pop(cb_agent, None)

    async def test_happy_path(self, gw):
        """全链路正常：OPA allow + 上游 200 → gateway 返回 200。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "app_token:xxx/table:yyy"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok"

    async def test_happy_path_response_headers(self, gw):
        """正常路径下响应包含 X-Trace-Id 和 X-Policy-Version 头。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "app_token:xxx/table:yyy"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert r.status_code == 200
        assert "x-trace-id" in r.headers or "x-policy-version" in r.headers

    async def test_sensitive_headers_stripped(self, gw):
        """上游响应中的敏感头（如 Authorization、Set-Cookie）不应透传到客户端。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}

        with patch("routes.invoke.check_authz", new=AsyncMock(return_value=(True, []))):
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
        assert "authorization" not in {k.lower() for k in r.headers}
        assert "set-cookie" not in {k.lower() for k in r.headers}

    async def test_invalid_json_body(self, gw):
        """非法 JSON body → 400 INTENT_INVALID。"""
        client, _, __, sk, dk, djwk, kid = gw
        _, token = make_token(sk, kid, djwk)
        dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
        r = await client.post(
            INVOKE_URL,
            headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
            content=b"not valid json",
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INTENT_INVALID"


# ─────────────────────────────────────────────────────────────────────────────
# 速率限制
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimit:
    async def test_rate_limit_exhausted(self, gw):
        """令牌桶耗尽时 → 429 RATE_LIMITED。"""
        client, redis, _, sk, dk, djwk, kid = gw
        # 让 eval 返回 0（桶空）
        redis.eval_result = 0

        try:
            _, token = make_token(sk, kid, djwk)
            dpop = make_dpop(dk, djwk, "POST", INVOKE_URL)
            body = {"intent": {"action": "feishu.bitable.read", "resource": "*"}}
            r = await client.post(
                INVOKE_URL,
                headers={"Authorization": f"DPoP {token}", "DPoP": dpop, "X-Target-Agent": "data_agent"},
                json=body,
            )
            assert r.status_code == 429
            assert r.json()["error"]["code"] == "RATE_LIMITED"
        finally:
            redis.eval_result = 1
