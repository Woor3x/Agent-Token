"""End-to-end M1+M4+M5 integration demo.

Flow:
  1. OIDC PKCE — alice authorizes web-ui, exchanges code -> user_token
  2. Admin registers ``web_ui`` orchestrator with the IdP (one-time)
  3. AgentClient(web_ui) calls Gateway /a2a/invoke with X-Target-Agent: doc_assistant
       - Inside IdP: token-exchange user_token -> delegated_doc_token
       - DPoP-bound POST to gateway, gateway proxies to doc_assistant
  4. doc_assistant orchestrator (running in container) plans + fans out to
     data_agent / web_agent / feishu-mock and returns the final doc

Run after ``docker compose up`` from repo root::

    python scripts/e2e_demo.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import uuid
from pathlib import Path

import httpx

# Allow running without `pip install -e ./sdk`.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))           # for `agents.*`
sys.path.insert(0, str(_REPO / "sdk"))   # for `agent_token_sdk`
from agent_token_sdk.client import AgentClient  # noqa: E402  (avoid pulling server.py)

IDP_URL = os.environ.get("IDP_URL", "http://localhost:8000")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9200")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "admin-secret-token")
USER_ID = os.environ.get("DEMO_USER", "alice")
USER_PASSWORD = os.environ.get("DEMO_PASS", "alice123")
REDIRECT_URI = os.environ.get("DEMO_REDIRECT", "http://localhost:3000/callback")
KEY_DIR = Path(os.environ.get("DEMO_KEY_DIR", "./.demo_keys/web_ui"))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def _oidc_login(client: httpx.AsyncClient) -> str:
    """Run authorize → login → token, returning user access_token."""
    code_verifier = _b64u(secrets.token_bytes(48))
    code_challenge = _b64u(hashlib.sha256(code_verifier.encode()).digest())

    r = await client.get(
        f"{IDP_URL}/oidc/authorize",
        params={
            "response_type": "code",
            "client_id": "web-ui",
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile agent:invoke",
            "state": "demo-state",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
    )
    r.raise_for_status()
    m = re.search(r'name="state_token" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("login form missing state_token")
    state_token = m.group(1)

    r = await client.post(
        f"{IDP_URL}/oidc/login",
        data={"state_token": state_token, "user_id": USER_ID, "password": USER_PASSWORD},
        follow_redirects=False,
    )
    if r.status_code != 302:
        raise RuntimeError(f"login expected 302, got {r.status_code}: {r.text}")
    loc = r.headers["location"]
    code = re.search(r"[?&]code=([^&]+)", loc).group(1)

    r = await client.post(
        f"{IDP_URL}/oidc/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
            "client_id": "web-ui",
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


async def _ensure_web_ui_agent(client: httpx.AsyncClient) -> tuple[str, bytes]:
    """Register the synthetic 'web_ui' orchestrator if no local key cached."""
    pem_path = KEY_DIR / "private.pem"
    kid_path = KEY_DIR / "kid.txt"
    if pem_path.exists() and kid_path.exists():
        return kid_path.read_text().strip(), pem_path.read_bytes()

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    cap_yaml = (Path(__file__).resolve().parents[1] / "capabilities" / "web_ui.yaml").read_text()
    body = {
        "agent_id": "web_ui",
        "role": "orchestrator",
        "display_name": "Web UI orchestrator (demo)",
        "capabilities_yaml": base64.b64encode(cap_yaml.encode()).decode(),
    }
    r = await client.post(
        f"{IDP_URL}/agents/register",
        json=body,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    if r.status_code != 200:
        # Re-registering an existing agent fails; if the IdP reports duplicate
        # we cannot recover the private key — manual action required.
        raise RuntimeError(f"register failed {r.status_code}: {r.text}")
    out = r.json()
    pem_path.write_text(out["private_key_pem"])
    kid_path.write_text(out["kid"])
    (KEY_DIR / "public.jwk.json").write_text(json.dumps(out["public_jwk"], indent=2))
    print(f"[demo] registered web_ui kid={out['kid']}")
    return out["kid"], out["private_key_pem"].encode()


async def main() -> None:
    print(f"[demo] IdP={IDP_URL}  Gateway={GATEWAY_URL}  user={USER_ID}")
    async with httpx.AsyncClient(timeout=30.0) as http:
        user_token = await _oidc_login(http)
        print(f"[demo] user_token (truncated): {user_token[:40]}...")

        kid, pem = await _ensure_web_ui_agent(http)

        async with AgentClient(
            agent_id="web_ui",
            idp_url=IDP_URL,
            gateway_url=GATEWAY_URL,
            kid=kid,
            private_key_pem=pem,
        ) as agent:
            trace_id = uuid.uuid4().hex
            result = await agent.invoke(
                target="doc_assistant",
                intent={
                    "action": "a2a.invoke",
                    "resource": "agent:doc_assistant",
                    "params": {"prompt": "Summarize Q1 sales from the sales team"},
                },
                on_behalf_of=user_token,
                purpose="demo",
                trace_id=trace_id,
            )
    print("[demo] result:")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
