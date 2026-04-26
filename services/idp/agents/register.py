import base64
import json
from datetime import datetime, timezone
from typing import Optional

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from agents.loader import AgentCapability, CapabilityEntry, DelegationConfig, get_capabilities
from agents.sod_check import check_agent_sod
from audit.writer import get_audit_writer
from errors import InvalidRequest, Unauthorized
from storage import sqlite as db
from config import settings

router = APIRouter()


class RegisterAgentRequest(BaseModel):
    agent_id: str
    role: str
    display_name: str = ""
    contact: str = ""
    desired_key_alg: str = "RS256"
    capabilities_yaml: Optional[str] = None


class RegisterAgentResponse(BaseModel):
    agent_id: str
    kid: str
    private_key_pem: str
    public_jwk: dict
    message: str


def _check_admin(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != settings.admin_token:
        raise Unauthorized("Admin token required")


def _generate_agent_keypair(agent_id: str, version: int = 1) -> tuple[str, bytes, dict]:
    from datetime import date
    from kms.store import _rsa_pub_to_jwk

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = f"agent-{agent_id}-{date.today().strftime('%Y%m%d')}-v{version}"

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_jwk = _rsa_pub_to_jwk(private_key.public_key(), kid)
    return kid, private_pem, public_jwk


@router.post("/agents/register", response_model=RegisterAgentResponse)
async def register_agent(request: Request, body: RegisterAgentRequest):
    _check_admin(request)

    if body.role not in ("orchestrator", "executor"):
        raise InvalidRequest("role must be 'orchestrator' or 'executor'")

    caps_data = []
    delegation_data = {}

    if body.capabilities_yaml:
        try:
            raw = base64.b64decode(body.capabilities_yaml).decode("utf-8")
            parsed = yaml.safe_load(raw)
        except Exception as exc:
            raise InvalidRequest(f"Invalid capabilities_yaml: {exc}")

        caps_data = parsed.get("capabilities", [])
        delegation_data = parsed.get("delegation", {})

    cap_entries = [CapabilityEntry(**c) for c in caps_data]
    delegation = DelegationConfig(**delegation_data) if delegation_data else DelegationConfig()

    agent_cap = AgentCapability(
        agent_id=body.agent_id,
        role=body.role,
        display_name=body.display_name,
        capabilities=cap_entries,
        delegation=delegation,
    )
    existing = get_capabilities()
    check_agent_sod(agent_cap, existing)

    existing_agent = await db.get_agent(body.agent_id)
    version = 1
    if existing_agent:
        import re
        m = re.search(r"-v(\d+)$", existing_agent.get("kid", ""))
        if m:
            version = int(m.group(1)) + 1

    kid, private_pem, public_jwk = _generate_agent_keypair(body.agent_id, version)

    now = datetime.now(timezone.utc).isoformat()
    await db.insert_agent({
        "agent_id": body.agent_id,
        "role": body.role,
        "kid": kid,
        "public_jwk": json.dumps(public_jwk),
        "alg": "RS256",
        "status": "active",
        "display_name": body.display_name,
        "contact": body.contact,
        "registered_at": now,
        "registered_by": "admin",
    })

    writer = get_audit_writer()
    await writer.write({
        "event_type": "agent.register",
        "sub": body.agent_id,
        "act": "register",
        "decision": "allow",
        "payload": {
            "agent_id": body.agent_id,
            "role": body.role,
            "kid": kid,
            "display_name": body.display_name,
        },
    })

    return RegisterAgentResponse(
        agent_id=body.agent_id,
        kid=kid,
        private_key_pem=private_pem.decode("utf-8"),
        public_jwk=public_jwk,
        message="Agent registered. Store the private key securely — it will not be shown again.",
    )


@router.get("/agents")
async def list_agents(request: Request, status: Optional[str] = None):
    _check_admin(request)
    agents = await db.list_agents(status=status)
    for agent in agents:
        agent.pop("public_jwk", None)
    return {"agents": agents}
