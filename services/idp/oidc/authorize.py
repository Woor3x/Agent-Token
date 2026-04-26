import base64
import hashlib
import os
import secrets
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import settings
from errors import InvalidRequest
from oidc.session import store_auth_code
from users.perms import verify_password

router = APIRouter()

ALLOWED_RESPONSE_TYPES = {"code"}
REQUIRED_SCOPES = {"openid"}


def _verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(computed, code_challenge)
    return False


LOGIN_FORM_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>IdP Login</title></head>
<body>
<h2>Sign in</h2>
<form method="post" action="/oidc/login">
  <input type="hidden" name="state_token" value="{state_token}">
  <label>User ID: <input type="text" name="user_id" required></label><br>
  <label>Password: <input type="password" name="password" required></label><br>
  <button type="submit">Login</button>
</form>
</body>
</html>"""


@router.get("/oidc/authorize")
async def oidc_authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(default="openid profile agent:invoke"),
    state: Optional[str] = Query(default=None),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(default="S256"),
    nonce: Optional[str] = Query(default=None),
):
    if response_type not in ALLOWED_RESPONSE_TYPES:
        raise InvalidRequest(f"Unsupported response_type: {response_type}")

    if redirect_uri not in settings.redirect_uris_list:
        raise InvalidRequest(f"redirect_uri not in whitelist: {redirect_uri}")

    if code_challenge_method != "S256":
        raise InvalidRequest("Only code_challenge_method=S256 is supported")

    state_token = secrets.token_urlsafe(32)
    session = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "nonce": nonce,
        "state_token": state_token,
    }

    from storage.redis import set_value
    import json
    await set_value(f"login_state:{state_token}", json.dumps(session), ttl_sec=300)

    html = LOGIN_FORM_HTML.format(state_token=state_token)
    return HTMLResponse(content=html)


@router.post("/oidc/login")
async def oidc_login(
    request: Request,
    state_token: str = Form(...),
    user_id: str = Form(...),
    password: str = Form(...),
):
    from storage.redis import get_value, delete_key
    import json

    raw = await get_value(f"login_state:{state_token}")
    if not raw:
        raise InvalidRequest("Login session expired or invalid")

    session = json.loads(raw)
    await delete_key(f"login_state:{state_token}")

    if not await verify_password(user_id, password):
        raise InvalidRequest("Invalid credentials")

    code = secrets.token_urlsafe(32)
    await store_auth_code(code, {**session, "user_id": user_id})

    redirect_uri = session["redirect_uri"]
    params = {"code": code}
    if session.get("state"):
        params["state"] = session["state"]

    redirect_url = f"{redirect_uri}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=302)
