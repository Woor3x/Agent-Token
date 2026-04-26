"""Redis Pub/Sub subscriber — listens for revocation broadcasts from IdP and updates Bloom filter."""
import asyncio
import json
import logging

import redis.asyncio as aioredis

from config import settings
from revoke.bloom import revoke_bloom

logger = logging.getLogger(__name__)

_CHANNELS = ("revoke:jti", "revoke:sub", "revoke:trace", "revoke:plan")

# Key prefix → Redis set name
_DIM_MAP = {
    "jti":   "revoked:jtis",
    "sub":   "revoked:subs",
    "trace": "revoked:traces",
    "plan":  "revoked:plans",
}


async def run_subscriber(redis_client: aioredis.Redis) -> None:
    """Long-running coroutine; call via asyncio.create_task."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(*_CHANNELS)
    logger.info("revoke subscriber started on channels %s", _CHANNELS)

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            dim = channel.split(":")[-1]          # "jti" / "sub" / ...
            payload = message["data"]
            if isinstance(payload, bytes):
                payload = payload.decode()
            value = json.loads(payload).get("value", "")
            if value and dim in _DIM_MAP:
                revoke_bloom.add(value)
                logger.debug("bloom: added %s in dim %s", value, dim)
        except Exception as exc:
            logger.warning("revoke subscriber error: %s", exc)
