import time
import uuid

from jose import jwt as jose_jwt

from config import settings, TOKEN_EXCHANGE_TTL_SEC
from kms.store import get_kms


def sign_delegated_token(claims_input: dict) -> tuple[str, str]:
    kms = get_kms()
    sk = kms.get_active_signing_key()

    jti = str(uuid.uuid4())
    now_s = int(time.time())

    claims = {
        **claims_input,
        "iss": settings.idp_issuer,
        "iat": now_s,
        "nbf": now_s,
        "exp": now_s + TOKEN_EXCHANGE_TTL_SEC,
        "jti": jti,
        "one_time": True,
        "policy_version": settings.policy_version,
    }

    token = jose_jwt.encode(
        claims,
        sk.private_pem,
        algorithm="RS256",
        headers={"kid": sk.kid},
    )
    return token, jti
