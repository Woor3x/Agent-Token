"""SSE broadcaster: fan-out events to N subscribers with per-subscriber filters."""
import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class SSEBroadcaster:
    def __init__(self) -> None:
        # list of (queue, filter_fn) tuples
        self._subs: list[tuple[asyncio.Queue, Callable[[dict], bool]]] = []
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    async def subscribe(self, filter_fn: Callable[[dict], bool]) -> asyncio.Queue:
        """Register a new SSE subscriber with an optional event filter."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subs.append((q, filter_fn))
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue (called when the SSE connection closes)."""
        async with self._lock:
            self._subs = [(x, f) for (x, f) in self._subs if x is not q]

    async def broadcast(self, events: list[dict]) -> None:
        """Push matching events to all subscriber queues. Drops for slow consumers."""
        async with self._lock:
            subs = list(self._subs)
        for q, filter_fn in subs:
            for event in events:
                if filter_fn(event):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.debug("SSE subscriber queue full, event dropped")


sse_broadcaster = SSEBroadcaster()
