import secrets
import time
import uuid

from fastapi import APIRouter, Form
from jose import jwt as jose_jwt

from config import settings, REFRESH_TOKEN_TTL_SEC
from errors import InvalidGrant, InvalidRequest
from kms.store import get_kms
from oidc.session import consume_auth_code, store_refresh_token
from oidc.authorize import _verify_pkce

router = APIRouter()

USER_TOKEN_TTL_SEC = 3600
USER_SCOPES = ["openid", "profile", "agent:invoke"]


def _sign_user_token(sub: str, scopes: list[str], extra: dict = None) -> tuple[str, str]:
    kms = get_kms()
    sk = kms.get_active_signing_key()
    jti = str(uuid.uuid4())
    now = int(time.time())

    claims = {
        "iss": settings.idp_issuer,
        "sub": sub,
        "aud": "web-ui",
        "iat": now,
        "nbf": now,
        "exp": now + USER_TOKEN_TTL_SEC,
        "jti": jti,
        "scope": " ".join(scopes),
    }
    if extra:
        claims.update(extra)

    token = jose_jwt.encode(claims, sk.private_pem, algorithm="RS256", headers={"kid": sk.kid})
    return token, jti


def _sign_id_token(sub: str, client_id: str, nonce: str = None) -> str:
    kms = get_kms()
    sk = kms.get_active_signing_key()
    now = int(time.time())

    claims = {
        "iss": settings.idp_issuer,
        "sub": sub,
        "aud": client_id,
        "iat": now,
        "exp": now + USER_TOKEN_TTL_SEC,
        "auth_time": now,
    }
    if nonce:
        claims["nonce"] = nonce

    return jose_jwt.encode(claims, sk.private_pem, algorithm="RS256", headers={"kid": sk.kid})


@router.post("/oidc/token")
async def oidc_token(
    grant_type: str = Form(...),
    code: str = Form(default=None),
    redirect_uri: str = Form(default=None),
    code_verifier: str = Form(default=None),
    client_id: str = Form(default=None),
    refresh_token: str = Form(default=None),
):
    if grant_type == "authorization_code":
        return await _handle_code_exchange(code, redirect_uri, code_verifier, client_id)
    elif grant_type == "refresh_token":
        return await _handle_refresh(refresh_token)
    else:
        raise InvalidRequest(f"Unsupported grant_type: {grant_type}")


async def _handle_code_exchange(
    code: str, redirect_uri: str, code_verifier: str, client_id: str
) -> dict:
    if not code or not code_verifier:
        raise InvalidRequest("code and code_verifier are required")

    session = await consume_auth_code(code)
    if not session:
        raise InvalidGrant("Authorization code not found or expired")

    if redirect_uri and session.get("redirect_uri") != redirect_uri:
        raise InvalidGrant("redirect_uri mismatch")

    if not _verify_pkce(code_verifier, session["code_challenge"], session.get("code_challenge_method", "S256")):
        raise InvalidGrant("PKCE code_verifier does not match code_challenge")

    user_id = session["user_id"]
    scope_list = session.get("scope", "openid profile agent:invoke").split()
    effective_scopes = list(set(USER_SCOPES) & set(scope_list)) or USER_SCOPES

    access_token, _ = _sign_user_token(user_id, effective_scopes)
    id_token = _sign_id_token(user_id, session.get("client_id", "web-ui"), session.get("nonce"))

    rt = secrets.token_urlsafe(48)
    await store_refresh_token(rt, {"user_id": user_id, "scope": effective_scopes}, REFRESH_TOKEN_TTL_SEC)

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": USER_TOKEN_TTL_SEC,
        "scope": " ".join(effective_scopes),
        "id_token": id_token,
        "refresh_token": rt,
    }


async def _handle_refresh(refresh_token: str) -> dict:
    if not refresh_token:
        raise InvalidRequest("refresh_token is required")

    from oidc.session import get_refresh_token
    session = await get_refresh_token(refresh_token)
    if not session:
        raise InvalidGrant("refresh_token not found or expired")

    user_id = session["user_id"]
    effective_scopes = session.get("scope", USER_SCOPES)

    access_token, _ = _sign_user_token(user_id, effective_scopes)

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": USER_TOKEN_TTL_SEC,
        "scope": " ".join(effective_scopes),
        "refresh_token": refresh_token,
    }
