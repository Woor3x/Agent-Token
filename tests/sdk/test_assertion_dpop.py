"""Unit tests for AssertionSigner + DPoPSigner + errors."""
from __future__ import annotations

import base64
import hashlib
import json

import jwt
import pytest

from agent_token_sdk import A2AError, AssertionSigner, DPoPSigner, is_retryable
from agent_token_sdk.errors import AssertionSignError


def test_assertion_mock_claims_roundtrip() -> None:
    signer = AssertionSigner(
        agent_id="doc_assistant", kid="doc_assistant-2025-q1", mock_secret="s"
    )
    tok = signer.sign(aud="https://idp.local/token/exchange", exp_delta=30)
    claims = jwt.decode(tok, "s", algorithms=["HS256"], audience="https://idp.local/token/exchange")
    assert claims["iss"] == "agent:doc_assistant"
    assert claims["sub"] == claims["iss"]
    assert claims["exp"] - claims["iat"] == 30
    hdr = jwt.get_unverified_header(tok)
    assert hdr["kid"] == "doc_assistant-2025-q1"


def test_assertion_rejects_exp_delta_out_of_range() -> None:
    signer = AssertionSigner(agent_id="x", kid="x-1", mock_secret="s")
    with pytest.raises(AssertionSignError):
        signer.sign(aud="https://idp/y", exp_delta=120)
    with pytest.raises(AssertionSignError):
        signer.sign(aud="https://idp/y", exp_delta=0)


def test_assertion_requires_key_material(monkeypatch) -> None:
    monkeypatch.delenv("MOCK_AUTH", raising=False)
    monkeypatch.delenv("MOCK_AUTH_SECRET", raising=False)
    with pytest.raises(AssertionSignError):
        AssertionSigner(agent_id="x", kid="x-1")


def test_dpop_jkt_matches_rfc7638() -> None:
    signer = DPoPSigner(kid="k-1")
    jwk = signer.public_jwk
    canonical = json.dumps(
        {"e": jwk["e"], "kty": "RSA", "n": jwk["n"]}, separators=(",", ":")
    )
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(canonical.encode()).digest()
    ).rstrip(b"=").decode()
    assert signer.jkt_b64u == expected


def test_dpop_proof_has_htu_htm_ath() -> None:
    signer = DPoPSigner(kid="k-1")
    proof = signer.sign(url="https://gateway/a2a/invoke", method="post", access_token="abc")
    hdr = jwt.get_unverified_header(proof)
    assert hdr["typ"] == "dpop+jwt"
    assert hdr["alg"] == "RS256"
    claims = jwt.decode(proof, options={"verify_signature": False})
    assert claims["htu"] == "https://gateway/a2a/invoke"
    assert claims["htm"] == "POST"
    expected_ath = base64.urlsafe_b64encode(
        hashlib.sha256(b"abc").digest()
    ).rstrip(b"=").decode()
    assert claims["ath"] == expected_ath


def test_retry_classifier() -> None:
    assert not is_retryable("AUTHN_TOKEN_INVALID")
    assert not is_retryable("AUTHZ_SCOPE_EXCEEDED")
    assert is_retryable("RATE_LIMITED")
    assert is_retryable("UPSTREAM_TIMEOUT")
    assert not is_retryable("UNKNOWN_FOO")


def test_a2a_error_repr() -> None:
    e = A2AError("AUTHZ_SCOPE_EXCEEDED", "nope", trace_id="t-1")
    assert "AUTHZ_SCOPE_EXCEEDED" in str(e)
    assert "t-1" in str(e)
