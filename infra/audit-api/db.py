"""SQLite initialisation with WAL pragmas, DDL creation, and connection helpers."""
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_db: Optional[aiosqlite.Connection] = None

_DDL = """
CREATE TABLE IF NOT EXISTS events (
  event_id         TEXT PRIMARY KEY,
  timestamp        TEXT NOT NULL,
  event_type       TEXT NOT NULL,
  trace_id         TEXT,
  span_id          TEXT,
  parent_span_id   TEXT,
  plan_id          TEXT,
  task_id          TEXT,
  decision         TEXT,
  deny_reasons     TEXT,
  caller_agent     TEXT,
  caller_sub       TEXT,
  caller_jti       TEXT,
  delegation_chain TEXT,
  dpop_jkt         TEXT,
  callee_agent     TEXT,
  callee_action    TEXT,
  callee_resource  TEXT,
  raw_prompt       TEXT,
  purpose          TEXT,
  token_aud        TEXT,
  token_scope      TEXT,
  token_one_time   INTEGER,
  token_exp        INTEGER,
  consumed_at      TEXT,
  consumed_by      TEXT,
  revoke_type      TEXT,
  revoke_value     TEXT,
  revoke_reason    TEXT,
  anomaly_rule     TEXT,
  severity         TEXT,
  result_status    INTEGER,
  result_bytes     INTEGER,
  latency_ms       INTEGER,
  policy_version   TEXT,
  extra            TEXT
);

CREATE INDEX IF NOT EXISTS idx_timestamp    ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_trace_id     ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_plan_id      ON events(plan_id);
CREATE INDEX IF NOT EXISTS idx_caller_agent ON events(caller_agent);
CREATE INDEX IF NOT EXISTS idx_callee_agent ON events(callee_agent);
CREATE INDEX IF NOT EXISTS idx_event_type   ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_decision     ON events(decision);
CREATE INDEX IF NOT EXISTS idx_trace_ts     ON events(trace_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_plan_ts      ON events(plan_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_jti_consumed ON events(caller_jti, consumed_at);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open SQLite connection, apply WAL pragmas and create schema."""
    global _db
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA cache_size=-64000")    # 64 MB
    await _db.execute("PRAGMA mmap_size=268435456")  # 256 MB
    await _db.execute("PRAGMA foreign_keys=ON")
    # executescript cannot run inside an open transaction; use it directly
    await _db.executescript(_DDL)
    await _db.commit()
    logger.info("audit db initialised: %s", db_path)
    return _db


async def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialised; call init_db() first")
    return _db


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None
        logger.info("audit db closed")
