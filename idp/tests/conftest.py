"""
全局 pytest fixtures。

测试分三层：
  unit/        — 纯函数，无 I/O，无 fixture
  integration/ — 真实 SQLite(:memory:) + fakeredis + 真实 KMS
  api/         — FastAPI TestClient，依赖全部 override
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import fakeredis.aioredis

# ── 保证 idp/ 在 sys.path ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 覆盖环境变量（必须在 import config 之前）────────────────────────────────
os.environ.setdefault("IDP_ISSUER", "https://idp.test")
os.environ.setdefault("IDP_KMS_PASSPHRASE", "test-passphrase-1234")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("POLICY_VERSION", "v1.2.0")
os.environ.setdefault("ALLOWED_SOURCE_NETS", "0.0.0.0/0")


# ── 辅助函数：生成 RSA 密钥对 ──────────────────────────────────────────────
def make_rsa_keypair(kid: str) -> tuple[bytes, dict]:
    """返回 (private_pem, public_jwk)"""
    import base64
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    pub = private_key.public_key().public_numbers()

    def b64u(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    public_jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
                  "n": b64u(pub.n), "e": b64u(pub.e)}
    return private_pem, public_jwk


def make_agent_assertion(agent_id: str, private_pem: bytes, kid: str,
                         audience: str = "https://idp.test/token/exchange",
                         jti: str = None, exp_delta: int = 300) -> str:
    """构造合法的 client_assertion JWT"""
    from jose import jwt as jose_jwt
    now = int(time.time())
    claims = {
        "iss": agent_id, "sub": agent_id,
        "aud": audience,
        "iat": now, "exp": now + exp_delta,
        "jti": jti or str(uuid.uuid4()),
    }
    return jose_jwt.encode(claims, private_pem, algorithm="RS256",
                           headers={"kid": kid})


def make_user_token(sub: str, private_pem: bytes, kid: str,
                    scope: str = "openid profile agent:invoke",
                    audience: str = "web-ui") -> str:
    """构造合法的用户 access_token"""
    from jose import jwt as jose_jwt
    from config import settings
    now = int(time.time())
    claims = {
        "iss": settings.idp_issuer, "sub": sub, "aud": audience,
        "scope": scope, "iat": now, "exp": now + 3600,
        "jti": str(uuid.uuid4()),
    }
    return jose_jwt.encode(claims, private_pem, algorithm="RS256",
                           headers={"kid": kid})


# ── fakeredis fixture ──────────────────────────────────────────────────────
@pytest.fixture
async def fake_redis():
    """返回 fakeredis 实例并注入到 storage.redis 全局"""
    import storage.redis as redis_mod
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_mod._redis = r
    yield r
    await r.aclose()
    redis_mod._redis = None


# ── 内存 SQLite fixture ────────────────────────────────────────────────────
@pytest.fixture
async def mem_db():
    """初始化内存 SQLite，执行 schema，注入全局"""
    import storage.sqlite as sqlite_mod
    import aiosqlite
    from pathlib import Path

    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")

    schema = (Path(__file__).parent.parent / "audit" / "schema.sql").read_text()
    await db.executescript(schema)
    await db.commit()

    sqlite_mod._db = db
    yield db
    await db.close()
    sqlite_mod._db = None


# ── KMS fixture ────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_kms(tmp_path):
    """在临时目录初始化 KMS"""
    import kms.store as kms_mod
    kms_mod.init_kms("test-passphrase-1234", str(tmp_path))
    yield kms_mod.get_kms()
    kms_mod._kms = None


# ── AuditWriter fixture ────────────────────────────────────────────────────
@pytest.fixture
async def audit_writer(mem_db):
    from audit.writer import init_audit_writer
    writer = init_audit_writer()
    writer.start()
    yield writer
    await writer.stop()
