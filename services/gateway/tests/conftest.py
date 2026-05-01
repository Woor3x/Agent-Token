"""Shared pytest fixtures for Gateway tests."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

# ── Key generation ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rsa_public_key(rsa_private_key):
    return rsa_private_key.public_key()


@pytest.fixture(scope="session")
def kid():
    return "test-kid-001"


@pytest.fixture(scope="session")
def dpop_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def dpop_public_key(dpop_private_key):
    return dpop_private_key.public_key()


# ── JWT helpers ────────────────────────────────────────────────────────────────

def _make_token(
    private_key,
    kid: str,
    sub: str = "agent:doc_assistant",
    aud: str = "agent:data_agent",
    jti: str = "jti-test-001",
    exp_offset: int = 300,
    extra: dict | None = None,
    dpop_jwk: dict | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://idp.local",
        "sub": sub,
        "aud": aud,
        "iat": now,
        "nbf": now,
        "exp": now + exp_offset,
        "jti": jti,
        "one_time": True,
        "trace_id": "trace-abc",
        "plan_id": "plan-xyz",
        "scope": "feishu.bitable.read",
    }
    if dpop_jwk:
        import hashlib, base64
        required = {k: dpop_jwk[k] for k in sorted(dpop_jwk) if k in ("e", "kty", "n")}
        canonical = json.dumps(required, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).digest()
        jkt = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        payload["cnf"] = {"jkt": jkt}
    if extra:
        payload.update(extra)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def valid_token(rsa_private_key, kid, dpop_public_key_jwk):
    return _make_token(rsa_private_key, kid, dpop_jwk=dpop_public_key_jwk)


@pytest.fixture(scope="session")
def dpop_public_key_jwk(dpop_private_key):
    from jwt.algorithms import RSAAlgorithm
    import json
    pub = dpop_private_key.public_key()
    return json.loads(RSAAlgorithm.to_jwk(pub))


# ── Mock Redis ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.ping = AsyncMock(return_value=True)
    r.sismember = AsyncMock(return_value=False)
    r.set = AsyncMock(return_value=True)
    r.eval = AsyncMock(return_value=1)
    return r


# ── App fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def app_with_mocks(rsa_public_key, kid, mock_redis):
    """Return a FastAPI test app with JWKS cache and Redis mocked."""
    from main import app
    from jwt_token.jwks_cache import jwks_cache

    jwks_cache._keys = {kid: rsa_public_key}
    jwks_cache._fetched_at = time.time()

    app.state.redis = mock_redis
    return app


@pytest.fixture
def client(app_with_mocks):
    with TestClient(app_with_mocks, raise_server_exceptions=False) as c:
        yield c
