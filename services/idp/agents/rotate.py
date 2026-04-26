import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.register import _check_admin, _generate_agent_keypair
from audit.writer import get_audit_writer
from errors import InvalidRequest, NotFound
from storage import sqlite as db

router = APIRouter()


class RotateKeyResponse(BaseModel):
    agent_id: str
    old_kid: str
    new_kid: str
    private_key_pem: str
    public_jwk: dict
    message: str


@router.post("/agents/{agent_id}/rotate-key", response_model=RotateKeyResponse)
async def rotate_agent_key(agent_id: str, request: Request):
    _check_admin(request)

    agent = await db.get_agent(agent_id)
    if not agent:
        raise NotFound(f"Agent {agent_id} not found")

    old_kid = agent["kid"]

    import re
    version = 1
    m = re.search(r"-v(\d+)$", old_kid)
    if m:
        version = int(m.group(1)) + 1

    new_kid, private_pem, public_jwk = _generate_agent_keypair(agent_id, version)
    await db.update_agent_kid(agent_id, new_kid, json.dumps(public_jwk))

    writer = get_audit_writer()
    await writer.write({
        "event_type": "agent.rotate_key",
        "sub": agent_id,
        "act": "rotate_key",
        "decision": "allow",
        "payload": {
            "agent_id": agent_id,
            "old_kid": old_kid,
            "new_kid": new_kid,
        },
    })

    return RotateKeyResponse(
        agent_id=agent_id,
        old_kid=old_kid,
        new_kid=new_kid,
        private_key_pem=private_pem.decode("utf-8"),
        public_jwk=public_jwk,
        message="Key rotated. Store the new private key securely — it will not be shown again.",
    )
