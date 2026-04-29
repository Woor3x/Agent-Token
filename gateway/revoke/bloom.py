"""Thread-safe in-process Bloom filter for fast revocation pre-check."""
import math
import threading
import mmh3

class BloomFilter:
    def __init__(self, capacity: int = 100_000, error_rate: float = 0.001) -> None:
        n = capacity
        p = error_rate
        self._m = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
        self._k = max(1, round((self._m / n) * math.log(2)))
        
        self._bits = bytearray(math.ceil(self._m / 8))
        self._lock = threading.Lock()

    def _get_positions(self, item: str) -> list[int]:
        h1, h2 = mmh3.hash64(item)
        return [((h1 + i * h2) % self._m) for i in range(self._k)]

    def add(self, item: str) -> None:
        positions = self._get_positions(item)
        with self._lock:
            for idx in positions:
                self._bits[idx // 8] |= (1 << (idx % 8))

    def might_contain(self, item: str) -> bool:
        positions = self._get_positions(item)
        with self._lock:
            return all(
                self._bits[idx // 8] & (1 << (idx % 8))
                for idx in positions
            )

revoke_bloom = BloomFilter()
