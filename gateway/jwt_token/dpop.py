"""DPoP proof JWT verification (RFC 9449)."""
import base64
import hashlib
import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from errors import authn_dpop_invalid


def _thumbprint(jwk_dict: dict) -> str:
    """SHA-256 JWK thumbprint (RFC 7638), base64url, no padding."""
    required = {k: jwk_dict[k] for k in sorted(jwk_dict) if k in ("e", "kty", "n")}
    canonical = json.dumps(required, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify_dpop(
    dpop_token: str,
    *,
    expected_jkt: str,
    expected_htu: str,
    expected_htm: str,
    max_iat_skew: int = 60,
) -> dict:
    """Verify DPoP proof and return its claims.

    Raises AuthnError on any failure.
    """
    try:
        header = jwt.get_unverified_header(dpop_token)
    except Exception:
        raise authn_dpop_invalid("malformed header")

    if header.get("typ") != "dpop+jwt":
        raise authn_dpop_invalid("wrong typ")
    if header.get("alg", "").upper() not in ("RS256", "ES256", "ES384", "PS256"):
        raise authn_dpop_invalid("unsupported alg")

    jwk_dict = header.get("jwk")
    if not jwk_dict:
        raise authn_dpop_invalid("missing jwk in header")

    # Verify thumbprint matches cnf.jkt
    computed_jkt = _thumbprint(jwk_dict)
    if computed_jkt != expected_jkt:
        raise authn_dpop_invalid("jkt mismatch")

    # Decode & verify signature with the embedded public key
    try:
        public_key: RSAPublicKey = RSAAlgorithm.from_jwk(jwk_dict)
        claims = jwt.decode(
            dpop_token,
            key=public_key,
            algorithms=[header["alg"]],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise authn_dpop_invalid("expired")
    except Exception as exc:
        raise authn_dpop_invalid(f"sig: {exc}")

    # htu / htm
    if claims.get("htu", "").rstrip("/") != expected_htu.rstrip("/"):
        raise authn_dpop_invalid(f"htu mismatch: {claims.get('htu')} != {expected_htu}")
    if claims.get("htm", "").upper() != expected_htm.upper():
        raise authn_dpop_invalid("htm mismatch")

    # iat skew
    iat = claims.get("iat")
    if iat is None:
        raise authn_dpop_invalid("missing iat")
    now = int(time.time())
    if abs(now - iat) > max_iat_skew:
        raise authn_dpop_invalid(f"iat skew {abs(now - iat)}s")

    # jti must be present (replay guard done externally via Redis SETNX)
    if not claims.get("jti"):
        raise authn_dpop_invalid("missing jti")

    return claims
