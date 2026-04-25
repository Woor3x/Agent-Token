import asyncio
import json
import time
from typing import Optional

from ulid import ULID

from storage import sqlite as db


class AuditWriter:
    def __init__(self, batch_size: int = 50, flush_interval: float = 2.0):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
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
        await self._queue.put(event)
        return event_id

    async def _drain(self) -> None:
        batch = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await db.insert_audit_batch(batch)

    async def _flush_loop(self) -> None:
        while True:
            batch = []
            deadline = asyncio.get_event_loop().time() + self._flush_interval
            while len(batch) < self._batch_size:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(event)
                except asyncio.TimeoutError:
                    break

            if batch:
                try:
                    await db.insert_audit_batch(batch)
                except Exception:
                    pass


_audit_writer: Optional[AuditWriter] = None


def init_audit_writer() -> AuditWriter:
    global _audit_writer
    _audit_writer = AuditWriter()
    return _audit_writer


def get_audit_writer() -> AuditWriter:
    if _audit_writer is None:
        raise RuntimeError("AuditWriter not initialized")
    return _audit_writer
