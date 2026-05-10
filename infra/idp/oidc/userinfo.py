from fastapi import APIRouter, Header
from jose import jwt as jose_jwt, JWTError

from config import settings
from errors import InvalidClient, Unauthorized
from kms.store import get_kms
from storage import sqlite as db

router = APIRouter()


@router.get("/oidc/userinfo")
async def userinfo(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized("Bearer token required")

    token = authorization[7:]
    kms = get_kms()
    public_keys = kms.get_all_public_keys()

    claims = None
    for jwk in public_keys:
        try:
            claims = jose_jwt.decode(
                token, jwk, algorithms=["RS256"],
                audience="web-ui",
            )
            break
        except JWTError:
            continue

    if claims is None:
        raise InvalidClient("Invalid access token")

    sub = claims.get("sub", "")
    user = await db.get_user(sub)

    result = {"sub": sub}
    if user:
        result["preferred_username"] = sub
    return result
