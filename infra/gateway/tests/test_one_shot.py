"""M2-T7: one-shot token destroy — TOKEN_REPLAYED test (maps to M5-T7)."""
import time
import pytest
from unittest.mock import AsyncMock

from authz.one_shot import consume_one_shot
from errors import AuthnError


@pytest.mark.asyncio
async def test_token_oneshot_destroy():
    """First consume succeeds, second raises TOKEN_REPLAYED."""
    redis = AsyncMock()
    # First call: SETNX returns True (success)
    redis.set = AsyncMock(return_value=True)
    claims = {"jti": "jti-oneshot", "exp": int(time.time()) + 60}
    await consume_one_shot(redis, claims)

    # Second call: SETNX returns False (already set)
    redis.set = AsyncMock(return_value=False)
    with pytest.raises(AuthnError) as exc_info:
        await consume_one_shot(redis, claims)
    assert exc_info.value.code == "TOKEN_REPLAYED"


@pytest.mark.asyncio
async def test_token_oneshot_concurrent():
    """Concurrent requests with same jti: only one should succeed."""
    call_count = 0

    async def setnx_once(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return call_count == 1  # first wins

    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=setnx_once)
    claims = {"jti": "jti-concurrent", "exp": int(time.time()) + 60}

    import asyncio
    results = await asyncio.gather(
        consume_one_shot(redis, claims),
        consume_one_shot(redis, claims),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if not isinstance(r, Exception))
    failures = sum(1 for r in results if isinstance(r, AuthnError))
    assert successes == 1
    assert failures == 1
