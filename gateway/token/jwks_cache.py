"""JWKS LRU cache with 10-min background refresh. Supports multi-kid rotation."""
import asyncio
import logging
import time
from collections import OrderedDict

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import RSAAlgorithm

from config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = settings.jwks_cache_ttl
_MAX_KIDS = 20


class JwksCache:
    def __init__(self) -> None:
        self._keys: OrderedDict[str, RSAPublicKey] = OrderedDict()
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=5.0, verify=settings.mtls_ca if settings.mtls_enabled else True)
        await self._refresh()

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, kid: str) -> RSAPublicKey:
        async with self._lock:
            if time.time() - self._fetched_at > _CACHE_TTL:
                await self._refresh_unlocked()
            key = self._keys.get(kid)
            if key is None:
                # force refresh once on cache miss (rotation window)
                await self._refresh_unlocked()
                key = self._keys.get(kid)
            if key is None:
                raise ValueError(f"unknown kid: {kid}")
            # LRU: move to end
            self._keys.move_to_end(kid)
            return key

    async def _refresh(self) -> None:
        async with self._lock:
            await self._refresh_unlocked()

    async def _refresh_unlocked(self) -> None:
        try:
            assert self._client is not None
            r = await self._client.get(settings.idp_jwks_url)
            r.raise_for_status()
            jwks = r.json()
            new_keys: dict[str, RSAPublicKey] = {}
            for jwk in jwks.get("keys", []):
                kid = jwk.get("kid")
                if not kid:
                    continue
                key = RSAAlgorithm.from_jwk(jwk)
                new_keys[kid] = key
            # merge keeping LRU order, evict oldest beyond cap
            for k, v in new_keys.items():
                self._keys[k] = v
                self._keys.move_to_end(k)
            while len(self._keys) > _MAX_KIDS:
                self._keys.popitem(last=False)
            self._fetched_at = time.time()
            logger.info("jwks refreshed, %d keys", len(self._keys))
        except Exception as exc:
            logger.warning("jwks refresh failed: %s", exc)


jwks_cache = JwksCache()
