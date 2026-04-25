import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional

from jose import jwt as jose_jwt, JWTError
from cryptography.hazmat.primitives.asymmetric import rsa, ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from errors import DpopInvalid
from storage.redis import setnx_with_ttl
from config import DPOP_JTI_TTL_SEC

MAX_SKEW_SEC = 60


@dataclass
class DpopClaims:
    jkt: str
    jti: str
    iat: int


def jwk_thumbprint(jwk: dict) -> str:
    kty = jwk.get("kty", "")
    if kty == "RSA":
        required_keys = ["e", "kty", "n"]
    elif kty == "EC":
        required_keys = ["crv", "kty", "x", "y"]
    else:
        raise DpopInvalid(f"Unsupported key type: {kty}")

    filtered = {k: jwk[k] for k in required_keys if k in jwk}
    if len(filtered) != len(required_keys):
        raise DpopInvalid("JWK missing required fields for thumbprint")

    canonical = json.dumps(filtered, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(canonical).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _jwk_to_public_key(jwk: dict):
    kty = jwk.get("kty")
    if kty == "RSA":
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

        def b64url_to_int(s: str) -> int:
            padding = "=" * (4 - len(s) % 4) if len(s) % 4 else ""
            return int.from_bytes(base64.urlsafe_b64decode(s + padding), "big")

        n = b64url_to_int(jwk["n"])
        e = b64url_to_int(jwk["e"])
        return RSAPublicNumbers(e, n).public_key()
    elif kty == "EC":
        from cryptography.hazmat.primitives.asymmetric.ec import (
            EllipticCurvePublicNumbers, SECP256R1, SECP384R1, SECP521R1
        )
        curve_map = {"P-256": SECP256R1(), "P-384": SECP384R1(), "P-521": SECP521R1()}
        crv = jwk.get("crv", "P-256")
        curve = curve_map.get(crv)
        if not curve:
            raise DpopInvalid(f"Unsupported EC curve: {crv}")

        def b64url_to_int(s: str) -> int:
            padding = "=" * (4 - len(s) % 4) if len(s) % 4 else ""
            return int.from_bytes(base64.urlsafe_b64decode(s + padding), "big")

        x = b64url_to_int(jwk["x"])
        y = b64url_to_int(jwk["y"])
        return EllipticCurvePublicNumbers(x, y, curve).public_key()
    else:
        raise DpopInvalid(f"Unsupported JWK kty: {kty}")


async def verify_dpop_proof(dpop_header: str, expected_htm: str, expected_htu: str) -> DpopClaims:
    try:
        header = jose_jwt.get_unverified_header(dpop_header)
    except JWTError as exc:
        raise DpopInvalid(f"Cannot parse DPoP header: {exc}")

    if header.get("typ") != "dpop+jwt":
        raise DpopInvalid("DPoP proof must have typ=dpop+jwt")

    jwk_claim = header.get("jwk")
    if not jwk_claim:
        raise DpopInvalid("DPoP proof missing jwk in header")

    alg = header.get("alg", "RS256")

    try:
        public_key = _jwk_to_public_key(jwk_claim)
    except Exception as exc:
        raise DpopInvalid(f"Cannot construct public key from DPoP jwk: {exc}")

    try:
        claims = jose_jwt.decode(
            dpop_header,
            public_key,
            algorithms=[alg],
            options={"verify_aud": False, "verify_exp": False, "verify_nbf": False},
        )
    except JWTError as exc:
        raise DpopInvalid(f"DPoP proof signature invalid: {exc}")

    htm = claims.get("htm")
    htu = claims.get("htu")
    iat = claims.get("iat")
    jti = claims.get("jti")

    if not htm or not htu or iat is None or not jti:
        raise DpopInvalid("DPoP proof missing required claims (htm, htu, iat, jti)")

    if htm.upper() != expected_htm.upper():
        raise DpopInvalid(f"DPoP htm mismatch: got {htm}, expected {expected_htm}")

    if htu.rstrip("/") != expected_htu.rstrip("/"):
        raise DpopInvalid(f"DPoP htu mismatch: got {htu}, expected {expected_htu}")

    now = int(time.time())
    if abs(now - int(iat)) > MAX_SKEW_SEC:
        raise DpopInvalid(f"DPoP iat out of window: iat={iat}, now={now}")

    redis_key = f"dpop:jti:{jti}"
    ok = await setnx_with_ttl(redis_key, "1", DPOP_JTI_TTL_SEC)
    if not ok:
        raise DpopInvalid(f"DPoP jti replay detected: {jti}")

    jkt = jwk_thumbprint(jwk_claim)
    return DpopClaims(jkt=jkt, jti=jti, iat=int(iat))
