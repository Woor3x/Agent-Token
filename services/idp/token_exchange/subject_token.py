from dataclasses import dataclass
from typing import Optional

from jose import jwt as jose_jwt, JWTError

from config import settings
from errors import InvalidGrant, TokenRevoked
from kms.store import get_kms
from storage.redis import sismember


@dataclass
class UserIdentity:
    sub: str
    scope: list[str]
    jti: str
    exp: int


async def verify_subject_token(subject_token: str) -> UserIdentity:
    kms = get_kms()
    public_keys = kms.get_all_public_keys()

    if not public_keys:
        raise InvalidGrant("No IdP public keys available to verify subject_token")

    claims = None
    last_exc: Optional[Exception] = None

    for jwk in public_keys:
        try:
            claims = jose_jwt.decode(
                subject_token,
                jwk,
                algorithms=["RS256"],
                audience="web-ui",
                options={"verify_aud": True},
            )
            break
        except JWTError as exc:
            last_exc = exc

    if claims is None:
        raise InvalidGrant(f"subject_token verification failed: {last_exc}")

    scope_raw = claims.get("scope", "")
    scope_list = scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw)

    if "agent:invoke" not in scope_list:
        raise InvalidGrant("subject_token does not contain 'agent:invoke' scope")

    jti = claims.get("jti", "")
    sub = claims.get("sub", "")
    exp = claims.get("exp", 0)

    if jti:
        revoked_jti = await sismember("revoked:jtis", jti)
        if revoked_jti:
            raise TokenRevoked(f"subject_token jti is revoked: {jti}")

    if sub:
        revoked_sub = await sismember("revoked:subs", sub)
        if revoked_sub:
            raise TokenRevoked(f"user {sub} is revoked")

    return UserIdentity(sub=sub, scope=scope_list, jti=jti, exp=int(exp))
