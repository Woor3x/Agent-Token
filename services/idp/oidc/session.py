import json
from typing import Optional

from storage.redis import setnx_with_ttl, get_value, delete_key, set_value
from config import AUTH_CODE_TTL_SEC


async def store_auth_code(code: str, session: dict) -> None:
    key = f"auth_code:{code}"
    await set_value(key, json.dumps(session), ttl_sec=AUTH_CODE_TTL_SEC)


async def consume_auth_code(code: str) -> Optional[dict]:
    key = f"auth_code:{code}"
    raw = await get_value(key)
    if raw is None:
        return None
    await delete_key(key)
    return json.loads(raw)


async def store_refresh_token(token: str, session: dict, ttl_sec: int) -> None:
    key = f"refresh:{token}"
    await set_value(key, json.dumps(session), ttl_sec=ttl_sec)


async def get_refresh_token(token: str) -> Optional[dict]:
    key = f"refresh:{token}"
    raw = await get_value(key)
    if raw is None:
        return None
    return json.loads(raw)


async def revoke_refresh_token(token: str) -> None:
    await delete_key(f"refresh:{token}")
