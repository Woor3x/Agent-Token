"""Unit tests for AuthN middleware — JWT, DPoP, revocation."""
import json
import time
from unittest.mock import AsyncMock, patch

import pytest


class TestJwtVerification:
    def test_missing_auth_header_returns_401(self, client):
        r = client.post("/a2a/invoke", json={"intent": {"action": "feishu.bitable.read", "resource": "*"}})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_TOKEN_INVALID"

    def test_wrong_scheme_returns_401(self, client):
        r = client.post(
            "/a2a/invoke",
            headers={"Authorization": "Bearer sometoken"},
            json={},
        )
        assert r.status_code == 401

    def test_expired_token_returns_401(self, client, rsa_private_key, kid, dpop_public_key_jwk, dpop_private_key):
        from tests.conftest import _make_token
        token = _make_token(rsa_private_key, kid, exp_offset=-10, dpop_jwk=dpop_public_key_jwk)
        dpop_proof = _make_dpop_proof(dpop_private_key, "POST", "http://testserver/a2a/invoke", dpop_public_key_jwk)
        r = client.post(
            "/a2a/invoke",
            headers={
                "Authorization": f"DPoP {token}",
                "DPoP": dpop_proof,
                "X-Target-Agent": "data_agent",
            },
            json={"intent": {"action": "feishu.bitable.read", "resource": "*"}},
        )
        assert r.status_code == 401

    def test_revoked_jti_returns_401(self, client, rsa_private_key, kid, dpop_public_key_jwk, dpop_private_key, mock_redis):
        from tests.conftest import _make_token
        from revoke.bloom import revoke_bloom
        token = _make_token(rsa_private_key, kid, jti="revoked-jti", dpop_jwk=dpop_public_key_jwk)
        revoke_bloom.add("revoked-jti")
        mock_redis.sismember = AsyncMock(return_value=True)

        dpop_proof = _make_dpop_proof(dpop_private_key, "POST", "http://testserver/a2a/invoke", dpop_public_key_jwk)
        r = client.post(
            "/a2a/invoke",
            headers={
                "Authorization": f"DPoP {token}",
                "DPoP": dpop_proof,
                "X-Target-Agent": "data_agent",
            },
            json={},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "AUTHN_REVOKED"


class TestDPoP:
    def test_missing_dpop_header_returns_401(self, client, rsa_private_key, kid, dpop_public_key_jwk):
        from tests.conftest import _make_token
        token = _make_token(rsa_private_key, kid, dpop_jwk=dpop_public_key_jwk)
        r = client.post(
            "/a2a/invoke",
            headers={"Authorization": f"DPoP {token}", "X-Target-Agent": "data_agent"},
            json={},
        )
        assert r.status_code == 401
        assert "dpop" in r.json()["error"]["code"].lower() or "AUTHN_DPOP" in r.json()["error"]["code"]

    def test_dpop_htm_mismatch_returns_401(self, client, rsa_private_key, kid, dpop_public_key_jwk, dpop_private_key):
        from tests.conftest import _make_token
        token = _make_token(rsa_private_key, kid, dpop_jwk=dpop_public_key_jwk)
        # proof says GET but we send POST
        dpop_proof = _make_dpop_proof(dpop_private_key, "GET", "http://testserver/a2a/invoke", dpop_public_key_jwk)
        r = client.post(
            "/a2a/invoke",
            headers={
                "Authorization": f"DPoP {token}",
                "DPoP": dpop_proof,
                "X-Target-Agent": "data_agent",
            },
            json={},
        )
        assert r.status_code == 401


# ── DPoP proof helper ─────────────────────────────────────────────────────────

def _make_dpop_proof(private_key, method: str, url: str, jwk_dict: dict, jti: str | None = None) -> str:
    import uuid
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization

    jti = jti or str(uuid.uuid4())
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    payload = {
        "htm": method,
        "htu": url,
        "iat": int(time.time()),
        "jti": jti,
    }
    return pyjwt.encode(
        payload,
        pem,
        algorithm="RS256",
        headers={"typ": "dpop+jwt", "jwk": jwk_dict},
    )
