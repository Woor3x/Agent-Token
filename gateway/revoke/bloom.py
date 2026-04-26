"""Thread-safe in-process Bloom filter for fast revocation pre-check."""
import hashlib
import math
import threading


class BloomFilter:
    def __init__(self, capacity: int = 100_000, error_rate: float = 0.001) -> None:
        self._lock = threading.Lock()
        # optimal bit-array size and hash count
        n = capacity
        p = error_rate
        m = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
        k = max(1, round((m / n) * math.log(2)))
        self._m = m
        self._k = k
        self._bits = bytearray(math.ceil(m / 8))

    def _hashes(self, item: str) -> list[int]:
        h = []
        data = item.encode()
        for i in range(self._k):
            digest = hashlib.sha256(data + i.to_bytes(2, "big")).digest()
            h.append(int.from_bytes(digest[:8], "big") % self._m)
        return h

    def add(self, item: str) -> None:
        with self._lock:
            for idx in self._hashes(item):
                self._bits[idx // 8] |= 1 << (idx % 8)

    def might_contain(self, item: str) -> bool:
        with self._lock:
            return all(
                self._bits[idx // 8] & (1 << (idx % 8))
                for idx in self._hashes(item)
            )


revoke_bloom = BloomFilter()
