"""RFC 9449 DPoP proof signer + RFC 7638 JWK thumbprint.

Generates ephemeral ``dpop+jwt`` proofs bound to (method, URL) and optionally
the hash of the access token (``ath``). In mock mode (no PEM supplied) we use
an in-process RSA key pair generated on construction — still produces a real
``jkt`` thumbprint so the IdP ``cnf.jkt`` binding round-trip is testable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import jwt

from .errors import DPoPSignError


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _int_to_b64url(n: int) -> str:
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8 or 1, "big"))


class DPoPSigner:
    """Signs short-lived DPoP proofs; exposes ``jkt_b64u`` for ``cnf.jkt`` binding."""

    def __init__(
        self,
        *,
        kid: str,
        private_key_path: str | Path | None = None,
        private_key_pem: bytes | str | None = None,
    ) -> None:
        self.kid = kid
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        pem: bytes | None = None
        if private_key_pem is not None:
            pem = private_key_pem if isinstance(private_key_pem, bytes) else private_key_pem.encode()
        elif private_key_path is not None:
            pem = Path(private_key_path).read_bytes()

        try:
            if pem is None:
                # Ephemeral keypair for tests / mock mode.
                self._pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            else:
                self._pk = serialization.load_pem_private_key(pem, password=None)
        except Exception as e:  # pragma: no cover
            raise DPoPSignError(f"invalid private key: {e}") from e

        self._jwk = self._public_jwk()
        # RFC 7638 canonical order: e, kty, n (lexicographic).
        canonical = json.dumps(
            {"e": self._jwk["e"], "kty": "RSA", "n": self._jwk["n"]},
            separators=(",", ":"),
            sort_keys=False,
        )
        self.jkt_b64u = _b64url(hashlib.sha256(canonical.encode()).digest())

    def _public_jwk(self) -> dict[str, Any]:
        pub = self._pk.public_key().public_numbers()
        return {
            "kty": "RSA",
            "alg": "RS256",
            "n": _int_to_b64url(pub.n),
            "e": _int_to_b64url(pub.e),
            "kid": self.kid,
        }

    @property
    def public_jwk(self) -> dict[str, Any]:
        return dict(self._jwk)

    def sign(self, *, url: str, method: str, access_token: str | None = None) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "htu": url,
            "htm": method.upper(),
            "iat": now,
            "jti": uuid.uuid4().hex,
        }
        if access_token:
            payload["ath"] = _b64url(hashlib.sha256(access_token.encode()).digest())
        headers = {"typ": "dpop+jwt", "alg": "RS256", "jwk": self._jwk}
        try:
            return jwt.encode(payload, self._pk, algorithm="RS256", headers=headers)
        except Exception as e:  # pragma: no cover
            raise DPoPSignError(str(e)) from e
