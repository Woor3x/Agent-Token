import json
from typing import Any, Optional

import redis.asyncio as aioredis


_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        raise RuntimeError("Redis not initialized; call init_redis() first")
    return _redis


async def init_redis(redis_url: str) -> None:
    global _redis
    _redis = aioredis.from_url(redis_url, decode_responses=True, max_connections=20)
    await _redis.ping()


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


async def setnx_with_ttl(key: str, value: str, ttl_sec: int) -> bool:
    r = await get_redis()
    result = await r.set(key, value, nx=True, ex=ttl_sec)
    return result is not None


async def get_value(key: str) -> Optional[str]:
    r = await get_redis()
    return await r.get(key)


async def set_value(key: str, value: str, ttl_sec: Optional[int] = None) -> None:
    r = await get_redis()
    if ttl_sec:
        await r.setex(key, ttl_sec, value)
    else:
        await r.set(key, value)


async def delete_key(key: str) -> None:
    r = await get_redis()
    await r.delete(key)


async def sadd_member(set_key: str, member: str, ttl_sec: Optional[int] = None) -> None:
    r = await get_redis()
    await r.sadd(set_key, member)
    if ttl_sec:
        existing_ttl = await r.ttl(set_key)
        if existing_ttl < 0:
            await r.expire(set_key, ttl_sec)
        elif existing_ttl < ttl_sec:
            await r.expire(set_key, ttl_sec)


async def sismember(set_key: str, member: str) -> bool:
    r = await get_redis()
    return bool(await r.sismember(set_key, member))


async def smembers(set_key: str) -> set[str]:
    r = await get_redis()
    return await r.smembers(set_key)


async def publish(channel: str, message: str) -> int:
    r = await get_redis()
    return await r.publish(channel, message)


async def incr_with_window(key: str, window_sec: int, limit: int) -> tuple[int, bool]:
    r = await get_redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_sec)
    return count, count <= limit


async def hset_field(hash_key: str, field: str, value: str) -> None:
    r = await get_redis()
    await r.hset(hash_key, field, value)


async def hget_field(hash_key: str, field: str) -> Optional[str]:
    r = await get_redis()
    return await r.hget(hash_key, field)


async def hgetall(hash_key: str) -> dict[str, str]:
    r = await get_redis()
    return await r.hgetall(hash_key)


async def expire_key(key: str, ttl_sec: int) -> None:
    r = await get_redis()
    await r.expire(key, ttl_sec)
