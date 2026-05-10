"""BatchWriter: asyncio.Queue → SQLite batch flush + SSE broadcast + Prometheus metrics."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram

import backup
from db import get_db
from sse import sse_broadcaster

logger = logging.getLogger(__name__)

# ── Prometheus metrics ────────────────────────────────────────────────────────

EVENTS_WRITTEN = Counter(
    "audit_events_written_total",
    "Events successfully written to SQLite",
    ["event_type"],
)
EVENTS_FAILED = Counter(
    "audit_events_failed_total",
    "Events that failed to write (backed up or dropped)",
    ["reason"],
)
QUEUE_DEPTH = Gauge("audit_queue_depth", "Current items in the ingest queue")
WRITE_LATENCY = Histogram(
    "audit_write_latency_ms",
    "Batch flush latency in milliseconds",
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000],
)
SSE_SUBSCRIBERS = Gauge("audit_sse_subscribers", "Active SSE subscriber connections")

# ── INSERT SQL ────────────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT OR IGNORE INTO events (
  event_id, timestamp, event_type,
  trace_id, span_id, parent_span_id,
  plan_id, task_id,
  decision, deny_reasons,
  caller_agent, caller_sub, caller_jti, delegation_chain, dpop_jkt,
  callee_agent, callee_action, callee_resource,
  raw_prompt, purpose,
  token_aud, token_scope, token_one_time, token_exp,
  consumed_at, consumed_by,
  revoke_type, revoke_value, revoke_reason,
  anomaly_rule, severity,
  result_status, result_bytes, latency_ms,
  policy_version, extra
) VALUES (
  :event_id, :timestamp, :event_type,
  :trace_id, :span_id, :parent_span_id,
  :plan_id, :task_id,
  :decision, :deny_reasons,
  :caller_agent, :caller_sub, :caller_jti, :delegation_chain, :dpop_jkt,
  :callee_agent, :callee_action, :callee_resource,
  :raw_prompt, :purpose,
  :token_aud, :token_scope, :token_one_time, :token_exp,
  :consumed_at, :consumed_by,
  :revoke_type, :revoke_value, :revoke_reason,
  :anomaly_rule, :severity,
  :result_status, :result_bytes, :latency_ms,
  :policy_version, :extra
)
"""


def _json_or_none(v) -> Optional[str]:
    """Coerce a value to a JSON string, or None if the value itself is None."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _to_row(event: dict) -> dict:
    """Coerce a normalised event dict into a DB-ready parameter dict."""
    one_time = event.get("token_one_time")
    return {
        "event_id": event.get("event_id"),
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "trace_id": event.get("trace_id"),
        "span_id": event.get("span_id"),
        "parent_span_id": event.get("parent_span_id"),
        "plan_id": event.get("plan_id"),
        "task_id": event.get("task_id"),
        "decision": event.get("decision"),
        "deny_reasons": _json_or_none(event.get("deny_reasons")),
        "caller_agent": event.get("caller_agent"),
        "caller_sub": event.get("caller_sub"),
        "caller_jti": event.get("caller_jti"),
        "delegation_chain": _json_or_none(event.get("delegation_chain")),
        "dpop_jkt": event.get("dpop_jkt"),
        "callee_agent": event.get("callee_agent"),
        "callee_action": event.get("callee_action"),
        "callee_resource": event.get("callee_resource"),
        "raw_prompt": event.get("raw_prompt"),
        "purpose": event.get("purpose"),
        "token_aud": event.get("token_aud"),
        "token_scope": _json_or_none(event.get("token_scope")),
        "token_one_time": int(one_time) if one_time is not None else None,
        "token_exp": event.get("token_exp"),
        "consumed_at": event.get("consumed_at"),
        "consumed_by": event.get("consumed_by"),
        "revoke_type": event.get("revoke_type"),
        "revoke_value": event.get("revoke_value"),
        "revoke_reason": event.get("revoke_reason"),
        "anomaly_rule": event.get("anomaly_rule"),
        "severity": event.get("severity"),
        "result_status": event.get("result_status"),
        "result_bytes": event.get("result_bytes"),
        "latency_ms": event.get("latency_ms"),
        "policy_version": event.get("policy_version"),
        "extra": _json_or_none(event.get("extra")),
    }


class BatchWriter:
    """Async batch writer: asyncio.Queue → periodic SQLite flush + SSE broadcast."""

    def __init__(self, batch_size: int = 50, flush_interval_ms: int = 100) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_ms / 1000.0
        self._task: Optional[asyncio.Task] = None

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        logger.info(
            "BatchWriter started (batch=%d interval=%.3fs)",
            self._batch_size, self._flush_interval,
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._drain()
        logger.info("BatchWriter stopped")

    def enqueue(self, event: dict) -> bool:
        """Non-blocking enqueue. Returns False and backs up if queue full."""
        try:
            self._queue.put_nowait(event)
            QUEUE_DEPTH.inc()
            return True
        except asyncio.QueueFull:
            logger.warning("audit queue full, backing up event %s", event.get("event_id"))
            backup.write_backup([event])
            EVENTS_FAILED.labels(reason="queue_full").inc()
            return False

    async def _run(self) -> None:
        buf: list[dict] = []
        last_flush = time.monotonic()
        while True:
            try:
                timeout = max(0.001, self._flush_interval - (time.monotonic() - last_flush))
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                buf.append(event)
                QUEUE_DEPTH.dec()
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

            elapsed = time.monotonic() - last_flush
            if len(buf) >= self._batch_size or (buf and elapsed >= self._flush_interval):
                await self._flush(buf)
                buf = []
                last_flush = time.monotonic()

    async def _drain(self) -> None:
        buf: list[dict] = []
        while not self._queue.empty():
            try:
                buf.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if buf:
            await self._flush(buf)

    async def _flush(self, events: list[dict]) -> None:
        if not events:
            return
        t0 = time.monotonic()
        try:
            db = await get_db()
            rows = [_to_row(e) for e in events]
            await db.executemany(_INSERT_SQL, rows)
            await db.commit()
            elapsed_ms = (time.monotonic() - t0) * 1000
            WRITE_LATENCY.observe(elapsed_ms)
            for e in events:
                EVENTS_WRITTEN.labels(event_type=e.get("event_type", "unknown")).inc()
            # Broadcast to SSE subscribers after successful write
            await sse_broadcaster.broadcast(events)
        except Exception as exc:
            logger.error(
                "audit flush failed (%d events): %s — backing up", len(events), exc
            )
            backup.write_backup(events)
            EVENTS_FAILED.labels(reason="db_error").inc(len(events))


# Module-level singleton; start() / stop() called in FastAPI lifespan
batch_writer = BatchWriter()
