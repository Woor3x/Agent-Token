from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from agents.register import _check_admin
from audit.writer import get_audit_writer
from errors import InvalidRequest
from revoke.pubsub import broadcast_revoke
from storage.redis import sadd_member, sismember

router = APIRouter()

KEY_MAP: dict[str, str] = {
    "jti":   "revoked:jtis",
    "sub":   "revoked:subs",
    "agent": "revoked:agents",
    "trace": "revoked:traces",
    "plan":  "revoked:plans",
    "chain": "revoked:chains",
}

DEFAULT_TTL_SEC = 86400 * 7


class RevokeRequest(BaseModel):
    type: str
    value: str
    reason: str = ""
    ttl_sec: int = DEFAULT_TTL_SEC


class RevokeStatusRequest(BaseModel):
    type: str
    value: str


@router.post("/revoke")
async def revoke(request: Request, body: RevokeRequest):
    _check_admin(request)

    if body.type not in KEY_MAP:
        raise InvalidRequest(f"Unknown revoke type: {body.type!r}. Allowed: {sorted(KEY_MAP)}")

    set_key = KEY_MAP[body.type]
    await sadd_member(set_key, body.value, ttl_sec=body.ttl_sec)
    await broadcast_revoke(body.type, body.value, body.reason)

    writer = get_audit_writer()
    await writer.write({
        "event_type": "token.revoke",
        "decision": "allow",
        "payload": {
            "type": body.type,
            "value": body.value,
            "reason": body.reason,
            "ttl_sec": body.ttl_sec,
        },
    })

    return {
        "revoked": True,
        "type": body.type,
        "value": body.value,
    }


@router.get("/revoke/status")
async def revoke_status(request: Request, type: str, value: str):
    _check_admin(request)

    if type not in KEY_MAP:
        raise InvalidRequest(f"Unknown revoke type: {type!r}")

    set_key = KEY_MAP[type]
    is_revoked = await sismember(set_key, value)

    return {
        "type": type,
        "value": value,
        "revoked": is_revoked,
    }
