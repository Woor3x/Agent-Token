import time
from typing import Optional

_cache_keys: Optional[list] = None
_cache_ts: float = 0.0
CACHE_TTL_SEC = 60


def get_cached_keys() -> Optional[list]:
    if _cache_keys is not None and (time.monotonic() - _cache_ts) < CACHE_TTL_SEC:
        return _cache_keys
    return None


def set_cached_keys(keys: list) -> None:
    global _cache_keys, _cache_ts
    _cache_keys = keys
    _cache_ts = time.monotonic()


def invalidate_cache() -> None:
    global _cache_keys, _cache_ts
    _cache_keys = None
    _cache_ts = 0.0
