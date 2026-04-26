import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite


_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized; call init_db() first")
    return _db


async def init_db(sqlite_path: str) -> None:
    global _db
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(sqlite_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA synchronous=NORMAL")

    schema_path = Path(__file__).parent.parent / "audit" / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    await _db.executescript(schema_sql)
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


async def get_agent(agent_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_agent_by_kid(kid: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute("SELECT * FROM agents WHERE kid = ?", (kid,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_agents(status: Optional[str] = None) -> list[dict]:
    db = await get_db()
    if status:
        async with db.execute("SELECT * FROM agents WHERE status = ? ORDER BY registered_at DESC", (status,)) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute("SELECT * FROM agents ORDER BY registered_at DESC") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def insert_agent(agent: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO agents (agent_id, role, kid, public_jwk, alg, status, display_name, contact, registered_at, registered_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent["agent_id"], agent["role"], agent["kid"],
            agent["public_jwk"], agent.get("alg", "RS256"),
            agent.get("status", "active"), agent.get("display_name"),
            agent.get("contact"), agent["registered_at"], agent.get("registered_by"),
        ),
    )
    await db.commit()


async def update_agent_kid(agent_id: str, kid: str, public_jwk: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE agents SET kid = ?, public_jwk = ? WHERE agent_id = ?",
        (kid, public_jwk, agent_id),
    )
    await db.commit()


async def update_agent_status(agent_id: str, status: str) -> None:
    db = await get_db()
    await db.execute("UPDATE agents SET status = ? WHERE agent_id = ?", (status, agent_id))
    await db.commit()


async def upsert_user(user: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO users (user_id, password_hash, permissions, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             password_hash=excluded.password_hash,
             permissions=excluded.permissions,
             updated_at=excluded.updated_at""",
        (
            user["user_id"],
            user.get("password_hash"),
            json.dumps(user.get("permissions", [])),
            user["updated_at"],
        ),
    )
    await db.commit()


async def get_user(user_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["permissions"] = json.loads(d["permissions"])
        return d


async def upsert_jwks_rotation(kid: str, status: str, public_jwk: str, created_at: str) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO jwks_rotation (kid, status, public_jwk, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(kid) DO UPDATE SET status=excluded.status""",
        (kid, status, public_jwk, created_at),
    )
    await db.commit()


async def retire_jwks_key(kid: str, retired_at: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE jwks_rotation SET status='archived', retired_at=? WHERE kid=?",
        (retired_at, kid),
    )
    await db.commit()


async def get_active_jwks_keys() -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM jwks_rotation WHERE status IN ('active','previous') ORDER BY created_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def insert_audit(event: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO audit (event_id, event_type, trace_id, plan_id, task_id, sub, act, aud, decision, deny_reasons, payload, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event["event_id"],
            event["event_type"],
            event.get("trace_id"),
            event.get("plan_id"),
            event.get("task_id"),
            event.get("sub"),
            event.get("act"),
            event.get("aud"),
            event.get("decision"),
            json.dumps(event.get("deny_reasons")) if event.get("deny_reasons") else None,
            json.dumps(event.get("payload", {})),
            event.get("ts", int(time.time())),
        ),
    )
    await db.commit()


async def insert_audit_batch(events: list[dict]) -> None:
    if not events:
        return
    db = await get_db()
    rows = [
        (
            e["event_id"], e["event_type"], e.get("trace_id"), e.get("plan_id"),
            e.get("task_id"), e.get("sub"), e.get("act"), e.get("aud"),
            e.get("decision"),
            json.dumps(e.get("deny_reasons")) if e.get("deny_reasons") else None,
            json.dumps(e.get("payload", {})),
            e.get("ts", int(time.time())),
        )
        for e in events
    ]
    await db.executemany(
        """INSERT OR IGNORE INTO audit (event_id, event_type, trace_id, plan_id, task_id, sub, act, aud, decision, deny_reasons, payload, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    await db.commit()
