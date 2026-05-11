"""
Comprehensive IdP test suite — 29 tests across 7 classes.

Covers gaps not addressed by unit / integration / api tests:
  • OIDC authorize endpoint validation              (TestOidcAuthorize, 4)
  • Full PKCE login → token → refresh flow          (TestOidcLoginAndToken, 6)
  • DPoP full protocol validation                   (TestDpopProtocol, 8)
  • Token-exchange API-level error paths            (TestTokenExchangeErrors, 4)
  • Token-exchange 10-phase happy path              (TestTokenExchangeHappyPath, 1)
  • Error response schema contract                  (TestErrorResponseSchema, 3)
  • Agent lifecycle (re-register, rotate, list)     (TestAgentLifecycle, 3)
"""
import base64
import hashlib
import re
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from jose import jwt as jose_jwt

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import settings
from dpop.validator import verify_dpop_proof
from errors import DpopInvalid
from kms.store import get_kms

# ── Constants ─────────────────────────────────────────────────────────────────
EXCHANGE_URL = "https://idp.test/token/exchange"
ADMIN_HDR = {"Authorization": "Bearer test-admin-token"}
REDIRECT_URI = "http://localhost:3000/callback"
GRANT_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"

pytestmark = pytest.mark.asyncio


# ── Module-level helpers (no fixtures) ───────────────────────────────────────

def pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256_challenge)."""
    verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _make_dpop_proof(
    private_pem: bytes,
    public_jwk: dict,
    *,
    htm: str = "POST",
    htu: str = EXCHANGE_URL,
    iat: int = None,
    jti: str = None,
    typ: str = "dpop+jwt",
    alg: str = "RS256",
) -> str:
    """Build a DPoP proof JWT.  Pass explicit iat/jti for error-case tests."""
    payload = {
        "htm": htm,
        "htu": htu,
        "iat": iat if iat is not None else int(time.time()),
        "jti": jti or str(uuid.uuid4()),
    }
    headers = {"typ": typ, "alg": alg, "jwk": public_jwk}
    return jose_jwt.encode(payload, private_pem, algorithm=alg, headers=headers)


def _make_agent_assertion(
    agent_id: str,
    private_pem: bytes,
    kid: str,
    *,
    audience: str = EXCHANGE_URL,
    jti: str = None,
    exp_delta: int = 300,
) -> str:
    """Build a RFC 7523 client_assertion JWT for the given agent."""
    now = int(time.time())
    claims = {
        "iss": agent_id,
        "sub": agent_id,
        "aud": audience,
        "iat": now,
        "exp": now + exp_delta,
        "jti": jti or str(uuid.uuid4()),
    }
    return jose_jwt.encode(
        claims, private_pem, algorithm="RS256", headers={"kid": kid}
    )


def _make_user_token_from_kms(sub: str = "alice") -> str:
    """Sign a subject_token with the KMS active key so verify_subject_token accepts it."""
    sk = get_kms().get_active_signing_key()
    now = int(time.time())
    claims = {
        "iss": settings.idp_issuer,
        "sub": sub,
        "aud": "web-ui",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "jti": str(uuid.uuid4()),
        "scope": "openid profile agent:invoke",
    }
    return jose_jwt.encode(
        claims, sk.private_pem, algorithm="RS256", headers={"kid": sk.kid}
    )


def _exchange_form(
    assertion: str,
    subject_token: str,
    *,
    scope: str = "feishu.bitable.read:app_token:testapp/table:tbl001",
    audience: str = "agent:data_agent",
    **extra,
) -> dict:
    """Minimal form-data dict for POST /token/exchange."""
    return {
        "grant_type": GRANT_EXCHANGE,
        "client_assertion_type": ASSERTION_TYPE,
        "client_assertion": assertion,
        "subject_token": subject_token,
        "subject_token_type": SUBJECT_TOKEN_TYPE,
        "scope": scope,
        "audience": audience,
        **extra,
    }


def _check_error_schema(resp) -> dict:
    """Assert error envelope is present; return the inner 'error' dict."""
    assert resp.status_code >= 400, f"Expected error status, got {resp.status_code}"
    body = resp.json()
    assert "error" in body, f"Missing 'error' key in body: {body}"
    err = body["error"]
    assert "code" in err, f"error.code missing: {err}"
    assert "message" in err, f"error.message missing: {err}"
    assert "policy_version" in err, f"error.policy_version missing: {err}"
    return err


# ─────────────────────────────────────────────────────────────────────────────
# 1 · OIDC Authorize endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestOidcAuthorize:
    async def test_valid_authorize_returns_html(self, app_client):
        """GET /oidc/authorize 合法参数 → 200 登录表单 HTML，含 state_token 隐藏域"""
        client, _ = app_client
        verifier, challenge = pkce_pair()
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "scope": "openid profile agent:invoke",
                "state": "state-abc",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 200
        assert 'name="state_token"' in resp.text
        assert "<form" in resp.text

    async def test_bad_response_type(self, app_client):
        """response_type=token（非 code）→ 400"""
        client, _ = app_client
        _, challenge = pkce_pair()
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "token",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400

    async def test_unlisted_redirect_uri(self, app_client):
        """redirect_uri=https://evil.example.com/cb（不在白名单）→ 400"""
        client, _ = app_client
        _, challenge = pkce_pair()
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": "https://evil.example.com/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400

    async def test_non_s256_method(self, app_client):
        """code_challenge_method=plain（非 S256）→ 400"""
        client, _ = app_client
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": "plaintext-challenge",
                "code_challenge_method": "plain",
            },
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 2 · OIDC Login + Token endpoint
# ─────────────────────────────────────────────────────────────────────────────
class TestOidcLoginAndToken:
    async def test_stale_state_token(self, app_client):
        """POST /oidc/login 随机伪造 state_token → 400"""
        client, _ = app_client
        resp = await client.post(
            "/oidc/login",
            data={
                "state_token": secrets.token_urlsafe(32),
                "user_id": "alice",
                "password": "alice123",
            },
        )
        assert resp.status_code == 400

    async def test_wrong_password(self, app_client):
        """合法 state_token + 错误密码 hunter2 → 400"""
        client, _ = app_client
        _, challenge = pkce_pair()
        auth_resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert auth_resp.status_code == 200
        m = re.search(r'name="state_token"\s+value="([^"]+)"', auth_resp.text)
        assert m, "state_token not in login form HTML"
        state_token = m.group(1)

        resp = await client.post(
            "/oidc/login",
            data={"state_token": state_token, "user_id": "alice", "password": "hunter2"},
        )
        assert resp.status_code == 400

    async def test_unsupported_grant_type(self, app_client):
        """POST /oidc/token grant_type=client_credentials → 400"""
        client, _ = app_client
        resp = await client.post(
            "/oidc/token",
            data={"grant_type": "client_credentials", "client_id": "web-ui"},
        )
        assert resp.status_code == 400

    async def test_expired_code(self, app_client):
        """随机伪造 code（不存在于 Redis）换 token → 400"""
        client, _ = app_client
        verifier, _ = pkce_pair()
        resp = await client.post(
            "/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": secrets.token_urlsafe(32),
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "client_id": "web-ui",
            },
        )
        assert resp.status_code == 400

    async def test_pkce_mismatch(self, app_client):
        """合法 code + 另一对的 code_verifier（S256 不匹配）→ 400"""
        client, _ = app_client
        verifier, challenge = pkce_pair()
        wrong_verifier, _ = pkce_pair()

        auth_resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        m = re.search(r'name="state_token"\s+value="([^"]+)"', auth_resp.text)
        state_token = m.group(1)

        login_resp = await client.post(
            "/oidc/login",
            data={"state_token": state_token, "user_id": "alice", "password": "alice123"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 302
        code = parse_qs(urlparse(login_resp.headers["location"]).query)["code"][0]

        resp = await client.post(
            "/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": wrong_verifier,
                "client_id": "web-ui",
            },
        )
        assert resp.status_code == 400

    async def test_full_pkce_flow(self, app_client):
        """authorize → login → token (code exchange) → refresh — complete happy path."""
        client, _ = app_client
        verifier, challenge = pkce_pair()

        # Step 1: authorize
        auth_resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "code",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "scope": "openid profile agent:invoke",
                "state": "my-state-xyz",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert auth_resp.status_code == 200
        m = re.search(r'name="state_token"\s+value="([^"]+)"', auth_resp.text)
        assert m, "state_token not found in login form"
        state_token = m.group(1)

        # Step 2: login → redirect with code
        login_resp = await client.post(
            "/oidc/login",
            data={"state_token": state_token, "user_id": "alice", "password": "alice123"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 302
        location = login_resp.headers["location"]
        qs = parse_qs(urlparse(location).query)
        assert "code" in qs, f"No code in redirect: {location}"
        assert qs.get("state", [None])[0] == "my-state-xyz"
        code = qs["code"][0]

        # Step 3: exchange code → tokens
        tok_resp = await client.post(
            "/oidc/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "client_id": "web-ui",
            },
        )
        assert tok_resp.status_code == 200, tok_resp.text
        tok = tok_resp.json()
        assert tok["token_type"] == "Bearer"
        assert "access_token" in tok
        assert "id_token" in tok
        assert "refresh_token" in tok
        assert "agent:invoke" in tok["scope"]

        # Step 4: refresh
        ref_resp = await client.post(
            "/oidc/token",
            data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
        )
        assert ref_resp.status_code == 200, ref_resp.text
        ref = ref_resp.json()
        assert "access_token" in ref
        assert ref["token_type"] == "Bearer"


# ─────────────────────────────────────────────────────────────────────────────
# 3 · DPoP full protocol
# ─────────────────────────────────────────────────────────────────────────────
class TestDpopProtocol:
    """
    Calls verify_dpop_proof() directly.
    Uses registered_agents to get a real RSA keypair and to ensure
    fakeredis (required for JTI anti-replay) is injected into storage.redis.
    """

    async def test_valid_dpop_proof(self, registered_agents):
        """合法 DPoP proof → verify_dpop_proof() 返回含 jkt / jti 的 claims"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(info["private_key_pem"], info["public_jwk"])
        claims = await verify_dpop_proof(proof, "POST", EXCHANGE_URL)
        assert claims.jkt
        assert claims.jti

    async def test_wrong_typ(self, registered_agents):
        """header.typ=JWT（非 dpop+jwt）→ DpopInvalid: typ=dpop+jwt"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(
            info["private_key_pem"], info["public_jwk"], typ="JWT"
        )
        with pytest.raises(DpopInvalid, match="typ=dpop\\+jwt"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_missing_jwk(self, registered_agents):
        """JWT without 'jwk' in header → DpopInvalid."""
        info = registered_agents["doc_assistant"]
        payload = {
            "htm": "POST",
            "htu": EXCHANGE_URL,
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
        }
        proof = jose_jwt.encode(
            payload,
            info["private_key_pem"],
            algorithm="RS256",
            headers={"typ": "dpop+jwt", "alg": "RS256"},
        )
        with pytest.raises(DpopInvalid, match="missing jwk"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_htm_mismatch(self, registered_agents):
        """proof.htm=GET，验证 POST → DpopInvalid: htm mismatch"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(
            info["private_key_pem"], info["public_jwk"], htm="GET"
        )
        with pytest.raises(DpopInvalid, match="htm mismatch"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_htu_mismatch(self, registered_agents):
        """proof.htu 指向 other.example.com → DpopInvalid: htu mismatch"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(
            info["private_key_pem"],
            info["public_jwk"],
            htu="https://other.example.com/token",
        )
        with pytest.raises(DpopInvalid, match="htu mismatch"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_stale_iat(self, registered_agents):
        """proof.iat 早于当前 120s → DpopInvalid: iat out of window"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(
            info["private_key_pem"],
            info["public_jwk"],
            iat=int(time.time()) - 120,
        )
        with pytest.raises(DpopInvalid, match="iat out of window"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_future_iat(self, registered_agents):
        """proof.iat 超前当前 120s → DpopInvalid: iat out of window"""
        info = registered_agents["doc_assistant"]
        proof = _make_dpop_proof(
            info["private_key_pem"],
            info["public_jwk"],
            iat=int(time.time()) + 120,
        )
        with pytest.raises(DpopInvalid, match="iat out of window"):
            await verify_dpop_proof(proof, "POST", EXCHANGE_URL)

    async def test_jti_replay(self, registered_agents):
        """Same jti used twice → second call raises DpopInvalid (Redis setnx guard)."""
        info = registered_agents["doc_assistant"]
        shared_jti = str(uuid.uuid4())
        proof1 = _make_dpop_proof(
            info["private_key_pem"], info["public_jwk"], jti=shared_jti
        )
        proof2 = _make_dpop_proof(
            info["private_key_pem"], info["public_jwk"], jti=shared_jti
        )
        await verify_dpop_proof(proof1, "POST", EXCHANGE_URL)
        with pytest.raises(DpopInvalid, match="replay"):
            await verify_dpop_proof(proof2, "POST", EXCHANGE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# 4 · Token exchange — error paths
# ─────────────────────────────────────────────────────────────────────────────
class TestTokenExchangeErrors:
    async def test_wrong_grant_type(self, app_client):
        """grant_type=authorization_code → 400 invalid_request"""
        client, _ = app_client
        resp = await client.post(
            "/token/exchange",
            data={"grant_type": "authorization_code"},
        )
        assert resp.status_code == 400

    async def test_missing_dpop_header(self, app_client, registered_agents):
        """缺少 DPoP 头 → 400 invalid_request"""
        client, _ = app_client
        info = registered_agents["doc_assistant"]
        assertion = _make_agent_assertion(
            "doc_assistant", info["private_key_pem"], info["kid"]
        )
        subject_token = _make_user_token_from_kms()

        resp = await client.post(
            "/token/exchange",
            data=_exchange_form(assertion, subject_token),
        )
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert "dpop" in err["message"].lower() or err["code"] == "invalid_request"

    async def test_unknown_callee_agent(self, app_client, registered_agents):
        """audience=agent:totally_unknown_agent → 400 unknown callee"""
        client, _ = app_client
        info = registered_agents["doc_assistant"]
        assertion = _make_agent_assertion(
            "doc_assistant", info["private_key_pem"], info["kid"]
        )
        subject_token = _make_user_token_from_kms()
        dpop = _make_dpop_proof(info["private_key_pem"], info["public_jwk"])

        resp = await client.post(
            "/token/exchange",
            data=_exchange_form(
                assertion, subject_token,
                audience="agent:totally_unknown_agent",
            ),
            headers={"DPoP": dpop},
        )
        assert resp.status_code == 400
        assert "unknown" in resp.json()["error"]["message"].lower()

    async def test_empty_effective_scope(self, app_client, registered_agents):
        """Alice has no feishu.contact.read permission → empty intersect → error."""
        client, _ = app_client
        info = registered_agents["doc_assistant"]
        assertion = _make_agent_assertion(
            "doc_assistant", info["private_key_pem"], info["kid"]
        )
        subject_token = _make_user_token_from_kms()
        dpop = _make_dpop_proof(info["private_key_pem"], info["public_jwk"])

        resp = await client.post(
            "/token/exchange",
            data=_exchange_form(
                assertion, subject_token,
                scope="feishu.contact.read:department:all",
                audience="agent:data_agent",
            ),
            headers={"DPoP": dpop},
        )
        assert resp.status_code in (400, 403)
        err_code = resp.json()["error"]["code"]
        assert err_code in (
            "empty_effective_scope", "invalid_request", "delegation_not_allowed"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5 · Token exchange — happy path (all 10 phases)
# ─────────────────────────────────────────────────────────────────────────────
class TestTokenExchangeHappyPath:
    async def test_happy_path(self, app_client, registered_agents):
        """
        All 10 phases pass:
          1 client_assertion  2 subject_token  3 DPoP  4 scope parse
          5 delegation        6 executor       7 intersect
          8 context           9 rate-limit    10 sign

        Validates delegated token claims: sub, act, aud, scope, cnf.
        """
        client, _ = app_client
        info = registered_agents["doc_assistant"]
        assertion = _make_agent_assertion(
            "doc_assistant", info["private_key_pem"], info["kid"]
        )
        subject_token = _make_user_token_from_kms("alice")
        dpop = _make_dpop_proof(info["private_key_pem"], info["public_jwk"])

        resp = await client.post(
            "/token/exchange",
            data=_exchange_form(
                assertion, subject_token,
                scope="feishu.bitable.read:app_token:testapp/table:tbl001",
                audience="agent:data_agent",
                plan_id="plan-comp-001",
                trace_id="trace-comp-001",
            ),
            headers={"DPoP": dpop},
        )
        assert resp.status_code == 200, f"Exchange failed: {resp.text}"
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert "access_token" in body
        assert body["expires_in"] == 120
        assert "feishu.bitable.read" in body["scope"]

        claims = jose_jwt.get_unverified_claims(body["access_token"])
        assert claims["sub"] == "alice"
        assert claims["act"]["sub"] == "doc_assistant"
        assert claims["aud"] == "agent:data_agent"
        assert "feishu.bitable.read" in claims["scope"]
        assert "cnf" in claims
        assert claims["cnf"]["jkt"]


# ─────────────────────────────────────────────────────────────────────────────
# 6 · Error response schema contract
# ─────────────────────────────────────────────────────────────────────────────
class TestErrorResponseSchema:
    """Every 4xx response must carry error.code, error.message, error.policy_version."""

    async def test_authorize_error_schema(self, app_client):
        """GET /oidc/authorize response_type=token → error.code + message + policy_version"""
        client, _ = app_client
        _, challenge = pkce_pair()
        resp = await client.get(
            "/oidc/authorize",
            params={
                "response_type": "token",
                "client_id": "web-ui",
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        err = _check_error_schema(resp)
        assert err["code"] == "invalid_request"
        assert err["policy_version"]

    async def test_token_error_schema(self, app_client):
        """POST /oidc/token grant_type=implicit → error.code + message + policy_version"""
        client, _ = app_client
        resp = await client.post(
            "/oidc/token",
            data={"grant_type": "implicit"},
        )
        err = _check_error_schema(resp)
        assert err["code"] == "invalid_request"
        assert err["policy_version"]

    async def test_exchange_error_schema(self, app_client):
        """POST /token/exchange grant_type=password → error.code + trace_id + policy_version"""
        client, _ = app_client
        resp = await client.post(
            "/token/exchange",
            data={"grant_type": "password"},
        )
        err = _check_error_schema(resp)
        assert err["code"] == "invalid_request"
        assert "trace_id" in err
        assert err["policy_version"]


# ─────────────────────────────────────────────────────────────────────────────
# 7 · Agent lifecycle
# ─────────────────────────────────────────────────────────────────────────────
class TestAgentLifecycle:
    """
    Runs last; re-registering doc_assistant bumps its DB version counter
    without affecting earlier tests that already consumed v1 keys.
    """

    async def test_reregister_increments_kid_version(self, app_client, registered_agents):
        """Re-registering an already-registered agent produces kid ending in -v2."""
        client, _ = app_client
        resp = await client.post(
            "/agents/register",
            json={
                "agent_id": "doc_assistant",
                "role": "orchestrator",
                "display_name": "Doc Assistant v2",
            },
            headers=ADMIN_HDR,
        )
        assert resp.status_code == 200, resp.text
        kid = resp.json()["kid"]
        assert kid.endswith("-v2"), f"Expected kid with -v2 suffix, got: {kid}"

    async def test_rotate_key_returns_new_kid(self, app_client, registered_agents):
        """POST /agents/{id}/rotate-key returns a distinct new kid."""
        client, _ = app_client
        resp = await client.post(
            "/agents/doc_assistant/rotate-key",
            headers=ADMIN_HDR,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "new_kid" in body
        assert "old_kid" in body
        assert body["new_kid"] != body["old_kid"]

    async def test_list_agents_requires_admin(self, app_client):
        """GET /agents without an Authorization header → 401."""
        client, _ = app_client
        resp = await client.get("/agents")
        assert resp.status_code in (401, 403)
