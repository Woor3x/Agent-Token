"""RFC 7523 Client Assertion signer.

Two key-material modes:

* ``private_key_path`` — load PEM private key from disk, sign with RS256.
* ``mock_secret`` — HS256 shared secret for offline tests / single-process demo.

The token is opaque to the SDK once signed; the IdP is responsible for verifying
``iss == sub == agent:<id>`` and the signature against its registered JWKS.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import jwt

from .errors import AssertionSignError


class AssertionSigner:
    """Sign ``iss=sub=agent:<id>`` JWTs for the IdP ``/token/exchange`` endpoint."""

    def __init__(
        self,
        *,
        agent_id: str,
        kid: str,
        private_key_path: str | Path | None = None,
        private_key_pem: bytes | str | None = None,
        mock_secret: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.kid = kid
        self._pk: Any | None = None
        self._mock_secret = mock_secret

        if mock_secret is not None:
            self._alg = "HS256"
            return

        pem: bytes | None = None
        if private_key_pem is not None:
            pem = private_key_pem if isinstance(private_key_pem, bytes) else private_key_pem.encode()
        elif private_key_path is not None:
            pem = Path(private_key_path).read_bytes()
        else:
            env = os.environ.get("MOCK_AUTH_SECRET")
            if os.environ.get("MOCK_AUTH", "false").lower() == "true" and env:
                self._mock_secret = env
                self._alg = "HS256"
                return
            raise AssertionSignError("no key material supplied")

        try:
            from cryptography.hazmat.primitives import serialization

            self._pk = serialization.load_pem_private_key(pem, password=None)
            self._alg = "RS256"
        except Exception as e:  # pragma: no cover
            raise AssertionSignError(f"invalid private key: {e}") from e

    def sign(self, *, aud: str, exp_delta: int = 60, jti: str | None = None) -> str:
        if exp_delta <= 0 or exp_delta > 60:
            raise AssertionSignError(f"exp_delta must be in (0, 60], got {exp_delta}")
        now = int(time.time())
        payload = {
            "iss": f"agent:{self.agent_id}" if not self.agent_id.startswith("agent:") else self.agent_id,
            "sub": f"agent:{self.agent_id}" if not self.agent_id.startswith("agent:") else self.agent_id,
            "aud": aud,
            "iat": now,
            "nbf": now,
            "exp": now + exp_delta,
            "jti": jti or uuid.uuid4().hex,
        }
        headers = {"kid": self.kid, "typ": "JWT"}
        key: Any = self._mock_secret if self._mock_secret is not None else self._pk
        try:
            return jwt.encode(payload, key, algorithm=self._alg, headers=headers)
        except Exception as e:  # pragma: no cover
            raise AssertionSignError(str(e)) from e
