"""
API 层集成测试：HTTP 端点验证
"""
import base64
import json
import time
import uuid

import pytest
from jose import jwt as jose_jwt

from tests.conftest import make_rsa_keypair


class TestHealthAndJWKS:
    async def test_healthz_ok(self, app_client):
        client, _ = app_client
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "sqlite" in data["checks"]
        assert "redis" in data["checks"]

    async def test_jwks_returns_keys(self, app_client):
        client, _ = app_client
        resp = await client.get("/jwks")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert len(keys) >= 1
        assert keys[0]["kty"] == "RSA"
        assert "n" in keys[0]
        assert "e" in keys[0]
        assert "Cache-Control" in resp.headers

    async def test_openid_configuration(self, app_client):
        client, _ = app_client
        resp = await client.get("/.well-known/openid-configuration")
        assert resp.status_code == 200
        config = resp.json()
        assert "jwks_uri" in config
        assert "token_endpoint" in config
        assert "authorization_endpoint" in config


class TestAgentRegistration:
    async def test_register_agent_success(self, app_client):
        client, _ = app_client
        caps_yaml = base64.b64encode(b"""
capabilities:
  - action: feishu.contact.read
    resource_pattern: "department:*"
delegation:
  accept_from: [doc_assistant]
""").decode()

        resp = await client.post(
            "/agents/register",
            json={
                "agent_id": "test_executor_api",
                "role": "executor",
                "display_name": "Test Executor",
                "capabilities_yaml": caps_yaml,
            },
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "test_executor_api"
        assert "private_key_pem" in data
        assert "-----BEGIN RSA PRIVATE KEY-----" in data["private_key_pem"]
        assert "kid" in data

    async def test_register_agent_requires_admin(self, app_client):
        client, _ = app_client
        resp = await client.post(
            "/agents/register",
            json={"agent_id": "hacker", "role": "executor"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    async def test_register_agent_invalid_role(self, app_client):
        client, _ = app_client
        resp = await client.post(
            "/agents/register",
            json={"agent_id": "bad_role_agent", "role": "superadmin"},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code in (400, 422)

    async def test_list_agents(self, app_client):
        client, _ = app_client
        resp = await client.get(
            "/agents",
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        assert "agents" in resp.json()


class TestRevoke:
    async def test_revoke_jti(self, app_client):
        client, fake_redis = app_client
        resp = await client.post(
            "/revoke",
            json={"type": "jti", "value": "test-jti-001", "reason": "test", "ttl_sec": 3600},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

        # 验证 Redis 中存在
        assert await fake_redis.sismember("revoked:jtis", "test-jti-001")

    async def test_revoke_status_check(self, app_client):
        client, fake_redis = app_client
        await fake_redis.sadd("revoked:subs", "eve")

        resp = await client.get(
            "/revoke/status",
            params={"type": "sub", "value": "eve"},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    async def test_revoke_unknown_type(self, app_client):
        client, _ = app_client
        resp = await client.post(
            "/revoke",
            json={"type": "unknown_type", "value": "x"},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 400

    async def test_revoke_requires_admin(self, app_client):
        client, _ = app_client
        resp = await client.post(
            "/revoke",
            json={"type": "jti", "value": "x"},
        )
        assert resp.status_code == 401


class TestOidcFlow:
    async def test_authorize_renders_form(self, app_client):
        client, _ = app_client
        import hashlib, secrets
        verifier = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()

        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": "http://localhost:3000/callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 200
        assert "form" in resp.text.lower()

    async def test_authorize_invalid_redirect_uri(self, app_client):
        client, _ = app_client
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": "http://evil.com/steal",
                "code_challenge": "abc",
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400


class TestMetrics:
    async def test_metrics_endpoint(self, app_client):
        client, _ = app_client
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert b"idp_requests_total" in resp.content
