import json

from storage.redis import publish


async def broadcast_revoke(revoke_type: str, value: str, reason: str = "") -> None:
    await publish("revoke", json.dumps({
        "event": "revoke",
        "type": revoke_type,
        "value": value,
        "reason": reason,
    }))


async def broadcast_policy_reload() -> None:
    await publish("policy_reload", json.dumps({"event": "policy_reload"}))


async def broadcast_agent_event(event: str, agent_id: str) -> None:
    await publish(f"agent_{event}", json.dumps({
        "event": f"agent_{event}",
        "agent_id": agent_id,
    }))
