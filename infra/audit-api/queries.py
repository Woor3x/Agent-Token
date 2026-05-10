"""SQL read queries for all REST endpoints."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from db import get_db
from filters import build_where_clause

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    """Convert an aiosqlite Row to dict, deserialising JSON fields."""
    d = dict(row)
    for field in ("deny_reasons", "delegation_chain", "token_scope", "extra"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


async def list_events(
    params: dict,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    """Paginated event list with optional filters. Returns (total_count, rows)."""
    db = await get_db()
    where_sql, args = build_where_clause(params)

    count_sql = f"SELECT COUNT(*) FROM events {where_sql}"
    async with db.execute(count_sql, args) as cur:
        row = await cur.fetchone()
        total = row[0] if row else 0

    query_sql = f"""
        SELECT * FROM events {where_sql}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """
    async with db.execute(query_sql, args + [limit, offset]) as cur:
        rows = await cur.fetchall()

    return total, [_row_to_dict(r) for r in rows]


async def get_event(event_id: str) -> Optional[dict]:
    """Single event lookup by event_id."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM events WHERE event_id = ?", (event_id,)
    ) as cur:
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None


async def get_trace(trace_id: str) -> dict:
    """Fetch all events for a trace and assemble a hierarchical span tree."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM events WHERE trace_id = ? ORDER BY timestamp ASC",
        (trace_id,),
    ) as cur:
        rows = [_row_to_dict(r) for r in await cur.fetchall()]

    if not rows:
        return {}

    started_at = rows[0]["timestamp"]
    ended_at = rows[-1]["timestamp"]

    decisions: dict[str, int] = {}
    for r in rows:
        d = r.get("decision") or "unknown"
        decisions[d] = decisions.get(d, 0) + 1

    # Build span index keyed by span_id (fallback to event_id when span_id absent)
    span_by_id: dict[str, dict] = {}
    for r in rows:
        sid = r.get("span_id") or r["event_id"]
        span_by_id[sid] = {
            "span_id": sid,
            "parent_span_id": r.get("parent_span_id"),
            "caller": r.get("caller_sub") or r.get("caller_agent"),
            "callee": r.get("callee_agent"),
            "decision": r.get("decision"),
            "latency_ms": r.get("latency_ms"),
            "event_id": r["event_id"],
            "children": [],
        }

    # Wire up parent → child relationships; spans without a parent are roots
    roots: list[dict] = []
    for sid, span in span_by_id.items():
        pid = span["parent_span_id"]
        if pid and pid in span_by_id:
            span_by_id[pid]["children"].append(span)
        else:
            roots.append(span)

    return {
        "trace_id": trace_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "total_spans": len(rows),
        "decisions": decisions,
        "spans": roots,
    }


async def get_plan(plan_id: str) -> dict:
    """Summarise all events belonging to a plan_id."""
    db = await get_db()
    async with db.execute(
        "SELECT * FROM events WHERE plan_id = ? ORDER BY timestamp ASC",
        (plan_id,),
    ) as cur:
        rows = [_row_to_dict(r) for r in await cur.fetchall()]

    if not rows:
        return {}

    # Derive user and orchestrator from the earliest events that have them
    user: Optional[str] = None
    orchestrator: Optional[str] = None
    for r in rows:
        if not user and r.get("caller_sub"):
            user = r["caller_sub"]
        if not orchestrator and r.get("caller_agent"):
            orchestrator = r["caller_agent"]

    summary: dict[str, int] = {"total": 0, "allow": 0, "deny": 0}
    tasks: list[dict] = []

    for r in rows:
        summary["total"] += 1
        d = r.get("decision", "")
        if d in ("allow", "deny"):
            summary[d] = summary.get(d, 0) + 1
        tasks.append({
            "task_id": r.get("task_id"),
            "agent": r.get("callee_agent"),
            "action": r.get("callee_action"),
            "jti": r.get("caller_jti"),
            "issued_at": r.get("timestamp"),
            "consumed_at": r.get("consumed_at"),
            "decision": r.get("decision"),
            "latency_ms": r.get("latency_ms"),
        })

    return {
        "plan_id": plan_id,
        "user": user,
        "orchestrator": orchestrator,
        "tasks": tasks,
        "summary": summary,
    }


_WINDOW_SECONDS: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 604800}


async def get_stats(window: str = "1h") -> dict:
    """Aggregate statistics over the given time window (1h / 24h / 7d)."""
    secs = _WINDOW_SECONDS.get(window, 3600)
    since = (
        datetime.now(timezone.utc) - timedelta(seconds=secs)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = await get_db()

    # Total count in window
    async with db.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp >= ?", (since,)
    ) as cur:
        total = (await cur.fetchone())[0]

    # Breakdown by decision
    by_decision: dict[str, int] = {}
    async with db.execute(
        """SELECT decision, COUNT(*) FROM events
           WHERE timestamp >= ? AND decision IS NOT NULL
           GROUP BY decision""",
        (since,),
    ) as cur:
        for row in await cur.fetchall():
            by_decision[row[0]] = row[1]

    # Breakdown by callee_agent + decision
    by_agent: dict[str, dict[str, int]] = {}
    async with db.execute(
        """SELECT callee_agent, decision, COUNT(*) FROM events
           WHERE timestamp >= ? AND callee_agent IS NOT NULL AND decision IS NOT NULL
           GROUP BY callee_agent, decision""",
        (since,),
    ) as cur:
        for row in await cur.fetchall():
            agent, dec, cnt = row[0], row[1], row[2]
            by_agent.setdefault(agent, {})[dec] = cnt

    # Event type counts (tokens / revokes / anomalies)
    async with db.execute(
        "SELECT event_type, COUNT(*) FROM events WHERE timestamp >= ? GROUP BY event_type",
        (since,),
    ) as cur:
        type_counts: dict[str, int] = {r[0]: r[1] for r in await cur.fetchall()}

    # deny_reasons breakdown — requires Python-side JSON parsing
    by_reason: dict[str, int] = {}
    async with db.execute(
        """SELECT deny_reasons FROM events
           WHERE timestamp >= ? AND deny_reasons IS NOT NULL AND deny_reasons != '[]'""",
        (since,),
    ) as cur:
        for (raw,) in await cur.fetchall():
            try:
                reasons = json.loads(raw) if isinstance(raw, str) else raw
                for reason in (reasons or []):
                    by_reason[reason] = by_reason.get(reason, 0) + 1
            except Exception:
                pass

    return {
        "window": window,
        "total": total,
        "by_decision": by_decision,
        "by_agent": by_agent,
        "by_reason": by_reason,
        "tokens_issued": type_counts.get("token_issued", 0),
        "tokens_consumed": type_counts.get("token_consumed", 0),
        "revoke_events": type_counts.get("revoke_issued", 0),
        "anomaly_events": type_counts.get("anomaly", 0),
    }
