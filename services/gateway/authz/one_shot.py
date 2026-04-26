"""One-shot token consumer — atomic SETNX jti:used:<jti>."""
import time

import redis.asyncio as aioredis

from errors import token_replayed


async def consume_one_shot(redis: aioredis.Redis, claims: dict) -> None:
    """Mark token jti as used. Raises AuthnError if already consumed."""
    jti = claims["jti"]
    exp = claims.get("exp", 0)
    ttl = max(1, exp - int(time.time()))
    ok = await redis.set(f"jti:used:{jti}", 1, nx=True, ex=ttl)
    if not ok:
        raise token_replayed()
