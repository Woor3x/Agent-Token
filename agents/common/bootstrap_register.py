"""Agent bootstrap: register with M1 IdP `/agents/register` and persist the key.

Reads env:
    AGENT_ID            agent identifier (matches capabilities/<id>.yaml)
    AGENT_ROLE          orchestrator | executor
    IDP_URL             base URL of M1 IdP
    ADMIN_TOKEN         IdP admin bearer token (provisioning)

Loads the matching ``/app/capabilities/<AGENT_ID>.yaml`` (if mounted),
base64-encodes it, POSTs to ``/agents/register``, and writes
``/app/keys/<AGENT_ID>/{private.pem,kid.txt,public.jwk.json}``.

This script is idempotent — if the agent already exists, IdP rotates the key
(per ``/agents/register`` semantics) and we overwrite the local copy.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx


def main() -> int:
    agent_id = os.environ["AGENT_ID"]
    role = os.environ.get("AGENT_ROLE", "executor")
    idp_url = os.environ["IDP_URL"].rstrip("/")
    admin_token = os.environ.get("ADMIN_TOKEN", "admin-secret-token")

    cap_path = Path(f"/app/capabilities/{agent_id}.yaml")
    capabilities_yaml_b64: str | None = None
    if cap_path.exists():
        capabilities_yaml_b64 = base64.b64encode(cap_path.read_bytes()).decode()

    body = {
        "agent_id": agent_id,
        "role": role,
        "display_name": agent_id,
        "desired_key_alg": "RS256",
    }
    if capabilities_yaml_b64:
        body["capabilities_yaml"] = capabilities_yaml_b64

    headers = {"Authorization": f"Bearer {admin_token}"}

    with httpx.Client(timeout=30.0) as c:
        r = c.post(f"{idp_url}/agents/register", json=body, headers=headers)
    if r.status_code >= 400:
        print(f"[bootstrap] register failed {r.status_code}: {r.text}", file=sys.stderr)
        return 2
    data = r.json()

    key_dir = Path(f"/app/keys/{agent_id}")
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / "private.pem").write_text(data["private_key_pem"])
    (key_dir / "kid.txt").write_text(data["kid"])
    (key_dir / "public.jwk.json").write_text(json.dumps(data["public_jwk"]))
    print(f"[bootstrap] saved {key_dir}/{{private.pem,kid.txt,public.jwk.json}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
