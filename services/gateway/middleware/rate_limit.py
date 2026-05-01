"""Token bucket rate limiter per agent_id+action, backed by Redis."""
import logging
import time

import redis.asyncio as aioredis
from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from errors import RateLimitError, _error_body

logger = logging.getLogger(__name__)

# Lua script: atomic token bucket refill + consume
_LUA = """
local key        = KEYS[1]
local capacity   = tonumber(ARGV[1])
local refill     = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local cost       = tonumber(ARGV[4])

local data       = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens     = tonumber(data[1]) or capacity
local last       = tonumber(data[2]) or now

local delta      = (now - last) * refill
tokens           = math.min(capacity, tokens + delta)

if tokens < cost then
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 0
end

tokens = tokens - cost
redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
redis.call('EXPIRE', key, 3600)
return 1
"""


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/healthz", "/metrics") or path.startswith("/admin/"):
        return await call_next(request)

    claims = getattr(request.state, "token_claims", None)
    if claims is None:
        return await call_next(request)

    sub = claims.get("sub", "unknown")
    target_agent = request.headers.get("X-Target-Agent", "unknown")
    bucket_key = f"rl:{sub}:{target_agent}"

    redis: aioredis.Redis = request.app.state.redis
    now = time.time()
    try:
        result = await redis.eval(
            _LUA,
            1,
            bucket_key,
            str(settings.rate_limit_capacity),
            str(settings.rate_limit_refill_rate),
            str(now),
            "1",
        )
        if result == 0:
            exc = RateLimitError("RATE_LIMITED", "token bucket exhausted")
            return JSONResponse(status_code=429, content=_error_body(request, exc))
    except Exception as exc:
        logger.warning("rate_limit redis error: %s — skipping", exc)

    return await call_next(request)
