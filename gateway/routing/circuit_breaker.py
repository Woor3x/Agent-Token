"""Per-upstream circuit breaker — closed / open / half-open state machine."""
import asyncio
import logging
import time
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        upstream_id: str,
        failure_threshold: int = settings.cb_failure_threshold,
        open_duration: int = settings.cb_open_duration,
        half_open_probes: int = settings.cb_half_open_probes,
    ) -> None:
        self.upstream_id = upstream_id
        self._failure_threshold = failure_threshold
        self._open_duration = open_duration
        self._half_open_probes = half_open_probes

        self._state = State.CLOSED
        self._failures = 0
        self._opened_at: float = 0.0
        self._probes_allowed = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    async def before_call(self) -> None:
        """Check if the call is allowed. Raises CircuitOpenError if not."""
        from errors import CircuitOpenError
        async with self._lock:
            if self._state == State.OPEN:
                if time.time() - self._opened_at >= self._open_duration:
                    self._state = State.HALF_OPEN
                    self._probes_allowed = self._half_open_probes
                    logger.info("circuit %s → half_open", self.upstream_id)
                else:
                    raise CircuitOpenError("CIRCUIT_OPEN", self.upstream_id)
            if self._state == State.HALF_OPEN:
                if self._probes_allowed <= 0:
                    raise CircuitOpenError("CIRCUIT_OPEN", self.upstream_id)
                self._probes_allowed -= 1

    async def on_success(self) -> None:
        async with self._lock:
            if self._state in (State.HALF_OPEN, State.OPEN):
                logger.info("circuit %s → closed", self.upstream_id)
            self._state = State.CLOSED
            self._failures = 0

    async def on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            if self._state == State.HALF_OPEN or self._failures >= self._failure_threshold:
                self._state = State.OPEN
                self._opened_at = time.time()
                logger.warning(
                    "circuit %s → open (failures=%d)", self.upstream_id, self._failures
                )


_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(upstream_id: str) -> CircuitBreaker:
    if upstream_id not in _breakers:
        _breakers[upstream_id] = CircuitBreaker(upstream_id)
    return _breakers[upstream_id]


def all_breaker_states() -> dict[str, str]:
    return {k: v.state.value for k, v in _breakers.items()}
