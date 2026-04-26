"""Async audit writer — asyncio.Queue → batch flush to SQLite."""
import asyncio
import json
import logging
import time
import uuid

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT    PRIMARY KEY,
    ts          REAL    NOT NULL,
    trace_id    TEXT,
    plan_id     TEXT,
    sub         TEXT,
    target_agent TEXT,
    action      TEXT,
    resource    TEXT,
    decision    TEXT,
    deny_reasons TEXT,
    jti         TEXT,
    dpop_jti    TEXT,
    raw_prompt  TEXT,
    source_ip   TEXT,
    duration_ms REAL,
    extra       TEXT
)
"""


class AuditWriter:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._db: aiosqlite.Connection | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._db = await aiosqlite.connect(settings.audit_db_path)
        await self._db.execute(_DDL)
        await self._db.commit()
        self._task = asyncio.create_task(self._flush_loop())
        logger.info("audit writer started: %s", settings.audit_db_path)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._drain()
        if self._db:
            await self._db.close()

    def emit(self, event: dict) -> None:
        """Non-blocking enqueue. Drops if queue full (audit failure must not block requests)."""
        event.setdefault("id", f"evt_{uuid.uuid4().hex}")
        event.setdefault("ts", time.time())
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("audit queue full, event dropped")

    async def _flush_loop(self) -> None:
        interval = settings.audit_flush_interval_ms / 1000
        batch_size = settings.audit_flush_batch_size
        while True:
            try:
                await asyncio.sleep(interval)
                await self._flush_batch(batch_size)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("audit flush error: %s", exc)

    async def _drain(self) -> None:
        while not self._queue.empty():
            await self._flush_batch(settings.audit_flush_batch_size)

    async def _flush_batch(self, n: int) -> None:
        rows = []
        for _ in range(n):
            try:
                rows.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not rows or self._db is None:
            return
        await self._db.executemany(
            """INSERT OR IGNORE INTO audit_events
               (id, ts, trace_id, plan_id, sub, target_agent, action, resource,
                decision, deny_reasons, jti, dpop_jti, raw_prompt, source_ip, duration_ms, extra)
               VALUES (:id, :ts, :trace_id, :plan_id, :sub, :target_agent, :action, :resource,
                       :decision, :deny_reasons, :jti, :dpop_jti, :raw_prompt, :source_ip,
                       :duration_ms, :extra)""",
            [
                {
                    "id": r.get("id"),
                    "ts": r.get("ts"),
                    "trace_id": r.get("trace_id"),
                    "plan_id": r.get("plan_id"),
                    "sub": r.get("sub"),
                    "target_agent": r.get("target_agent"),
                    "action": r.get("action"),
                    "resource": r.get("resource"),
                    "decision": r.get("decision"),
                    "deny_reasons": json.dumps(r.get("deny_reasons", [])),
                    "jti": r.get("jti"),
                    "dpop_jti": r.get("dpop_jti"),
                    "raw_prompt": r.get("raw_prompt"),
                    "source_ip": r.get("source_ip"),
                    "duration_ms": r.get("duration_ms"),
                    "extra": json.dumps(r.get("extra", {})),
                }
                for r in rows
            ],
        )
        await self._db.commit()


audit_writer = AuditWriter()
