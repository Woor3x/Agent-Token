import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from ulid import ULID

from storage import sqlite as db

logger = logging.getLogger(__name__)

# Maximum number of events buffered in-memory.
# If the queue is full (DB persistently down), put_nowait raises QueueFull
# and we emit a CRITICAL log rather than silently growing unboundedly.
_QUEUE_MAXSIZE = 10_000

# Retry back-off on DB flush failure (seconds).
_BACKOFF_INITIAL = 0.5
_BACKOFF_MAX = 30.0

# ── Audit API forwarding (fire-and-forget) ────────────────────────────────────

_TYPE_MAP = {
    "token.issue": "token_issued",
    "token.revoke": "revoke_issued",
    "agent.register": "agent_registered",
}


def _to_audit_event(event: dict) -> dict:
    """Map an IdP audit event dict → audit-api event schema."""
    ts = event.get("ts")
    timestamp = (
        datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if ts is not None
        else None
    )
    payload = event.get("payload") or {}
    deny_raw = event.get("deny_reasons")
    if isinstance(deny_raw, str):
        try:
            deny_reasons = json.loads(deny_raw)
        except Exception:
            deny_reasons = []
    else:
        deny_reasons = deny_raw or []

    return {
        "event_type": _TYPE_MAP.get(event.get("event_type", ""), event.get("event_type", "")),
        "timestamp": timestamp,
        "trace_id": event.get("trace_id"),
        "plan_id": event.get("plan_id"),
        "task_id": event.get("task_id"),
        "caller_sub": event.get("sub"),
        "caller_agent": event.get("act"),
        "token_aud": event.get("aud"),
        "decision": event.get("decision"),
        "deny_reasons": deny_reasons,
        "extra": payload,
    }


async def _forward_to_audit_api(batch: list[dict]) -> None:
    """POST a batch of IdP events to the central audit-api. Silently ignores all errors."""
    from config import settings  # local import to avoid circular at module level
    url = settings.audit_api_url
    token = settings.audit_api_token
    if not url or not token:
        return
    try:
        import httpx
        events = [_to_audit_event(e) for e in batch]
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{url}/audit/events",
                json={"events": events},
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:
        logger.debug("audit-api forward failed (non-fatal): %s", exc)


class AuditWriter:
    def __init__(self, batch_size: int = 50, flush_interval: float = 2.0):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._task: Optional[asyncio.Task] = None
        self._backoff = _BACKOFF_INITIAL

    def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Graceful shutdown: cancel background loop then drain remaining events."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._drain()

    async def write(self, event: dict) -> str:
        event_id = str(ULID())
        event["event_id"] = event_id
        if "ts" not in event:
            event["ts"] = int(time.time())
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Fix D: queue full means the DB has been down for an extended period.
            # Log CRITICAL so ops gets paged; the event is dropped (unavoidable).
            logger.critical(
                "audit queue full (%d cap), dropping event %s type=%s",
                _QUEUE_MAXSIZE, event_id, event.get("event_type"),
            )
        return event_id

    async def _drain(self) -> None:
        """Flush every remaining event synchronously (called at shutdown)."""
        batch: list[dict] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            try:
                await db.insert_audit_batch(batch)
                asyncio.create_task(_forward_to_audit_api(batch))
            except Exception as exc:
                logger.error(
                    "audit drain failed, %d events lost: %s", len(batch), exc
                )

    async def _flush_loop(self) -> None:
        while True:
            batch: list[dict] = []
            deadline = asyncio.get_event_loop().time() + self._flush_interval

            while len(batch) < self._batch_size:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=remaining
                    )
                    batch.append(event)
                except asyncio.TimeoutError:
                    break

            if not batch:
                continue

            try:
                await db.insert_audit_batch(batch)
                # Reset back-off on success.
                self._backoff = _BACKOFF_INITIAL
                asyncio.create_task(_forward_to_audit_api(batch))
            except Exception as exc:
                # Fix D: don't silently swallow failures.
                # Re-queue events so they aren't permanently lost on transient
                # DB errors (e.g. WAL checkpoint contention, brief lock).
                logger.error(
                    "audit flush failed (%d events): %s — re-queuing with %.1fs back-off",
                    len(batch), exc, self._backoff,
                )
                requeued, dropped = 0, 0
                for event in batch:
                    try:
                        self._queue.put_nowait(event)
                        requeued += 1
                    except asyncio.QueueFull:
                        dropped += 1
                        logger.critical(
                            "audit queue full during re-queue, dropping event %s",
                            event.get("event_id"),
                        )
                if dropped:
                    logger.critical(
                        "audit: %d events permanently dropped (queue full)", dropped
                    )

                # Exponential back-off so we don't hammer a broken DB.
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, _BACKOFF_MAX)


_audit_writer: Optional[AuditWriter] = None


def init_audit_writer() -> AuditWriter:
    global _audit_writer
    _audit_writer = AuditWriter()
    return _audit_writer


def get_audit_writer() -> AuditWriter:
    if _audit_writer is None:
        raise RuntimeError("AuditWriter not initialized")
    return _audit_writer
