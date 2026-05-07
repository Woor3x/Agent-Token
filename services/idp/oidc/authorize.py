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
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in — Agent Token</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
      background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
      display: flex; align-items: center; justify-content: center; padding: 1rem;
    }}
    .card {{
      width: 100%; max-width: 360px;
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 16px;
      box-shadow: 0 4px 24px rgba(0,0,0,.07);
      overflow: hidden;
    }}
    .card-header {{
      background: linear-gradient(90deg, #2563eb, #3b82f6);
      padding: 28px 32px;
    }}
    .card-header .logo {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }}
    .card-header .logo-icon {{
      width: 32px; height: 32px; border-radius: 8px;
      background: rgba(255,255,255,.2);
      display: flex; align-items: center; justify-content: center;
    }}
    .card-header .logo-icon svg {{ width: 16px; height: 16px; color: #fff; }}
    .card-header .logo-text {{ color: #fff; font-weight: 600; font-size: 15px; }}
    .card-header .subtitle {{ color: #bfdbfe; font-size: 12px; }}
    .card-body {{ padding: 28px 32px; }}
    .field {{ margin-bottom: 16px; }}
    .field label {{ display: block; font-size: 12px; font-weight: 500; color: #475569; margin-bottom: 6px; }}
    .field input {{
      width: 100%; padding: 9px 12px;
      border: 1px solid #e2e8f0; border-radius: 8px;
      font-size: 14px; color: #1e293b; outline: none;
      transition: border-color .15s, box-shadow .15s;
    }}
    .field input:focus {{ border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,.15); }}
    .btn {{
      width: 100%; padding: 10px;
      background: #2563eb; color: #fff;
      border: none; border-radius: 10px;
      font-size: 14px; font-weight: 500; cursor: pointer;
      transition: background .15s;
      margin-top: 4px;
    }}
    .btn:hover {{ background: #1d4ed8; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <div class="logo">
        <div class="logo-icon">
          <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
          </svg>
        </div>
        <span class="logo-text">Agent Token</span>
      </div>
      <p class="subtitle">A2A 鉴权系统 · 身份验证</p>
    </div>
    <div class="card-body">
      <form method="post" action="/oidc/login">
        <input type="hidden" name="state_token" value="{state_token}">
        <div class="field">
          <label for="user_id">用户 ID</label>
          <input id="user_id" type="text" name="user_id" required autocomplete="username" placeholder="请输入用户 ID">
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input id="password" type="password" name="password" required autocomplete="current-password" placeholder="请输入密码">
        </div>
        <button class="btn" type="submit">登录</button>
      </form>
    </div>
  </div>
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
