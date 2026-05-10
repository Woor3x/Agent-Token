"""SQL WHERE clause builder for paginated queries, and SSE filter function builder."""
from collections.abc import Callable
from typing import Any


# ── Query filter → SQL WHERE clause ──────────────────────────────────────────

def build_where_clause(params: dict) -> tuple[str, list[Any]]:
    """Build a parameterised WHERE clause from query parameter dict.

    Recognised keys:
        event_type, decision, deny_reason, caller_agent, callee_agent,
        sub, trace_id, plan_id, from, to, purpose

    Returns (where_sql, positional_args).
    where_sql is either "" (no filter) or "WHERE expr AND expr ...".
    """
    clauses: list[str] = []
    args: list[Any] = []

    if v := params.get("event_type"):
        clauses.append("event_type = ?")
        args.append(v)
    if v := params.get("decision"):
        clauses.append("decision = ?")
        args.append(v)
    if v := params.get("deny_reason"):
        # deny_reasons stored as JSON array; LIKE for substring match
        clauses.append("deny_reasons LIKE ?")
        args.append(f"%{v}%")
    if v := params.get("caller_agent"):
        clauses.append("caller_agent = ?")
        args.append(v)
    if v := params.get("callee_agent"):
        clauses.append("callee_agent = ?")
        args.append(v)
    if v := params.get("sub"):
        clauses.append("caller_sub = ?")
        args.append(v)
    if v := params.get("trace_id"):
        clauses.append("trace_id = ?")
        args.append(v)
    if v := params.get("plan_id"):
        clauses.append("plan_id = ?")
        args.append(v)
    if v := params.get("from"):
        clauses.append("timestamp >= ?")
        args.append(v)
    if v := params.get("to"):
        clauses.append("timestamp <= ?")
        args.append(v)
    if v := params.get("purpose"):
        clauses.append("purpose LIKE ?")
        args.append(f"%{v}%")

    if not clauses:
        return ("", args)
    return ("WHERE " + " AND ".join(clauses), args)


# ── SSE filter function builder ───────────────────────────────────────────────

def build_sse_filter(query_params: dict) -> Callable[[dict], bool]:
    """Return a predicate function for SSE subscriber filtering.

    Empty query_params → accept all events.
    Recognised keys: event_type, decision, caller_agent, callee_agent.
    """
    event_type = query_params.get("event_type")
    decision = query_params.get("decision")
    caller_agent = query_params.get("caller_agent")
    callee_agent = query_params.get("callee_agent")

    def filter_fn(event: dict) -> bool:
        if event_type and event.get("event_type") != event_type:
            return False
        if decision and event.get("decision") != decision:
            return False
        if caller_agent and event.get("caller_agent") != caller_agent:
            return False
        if callee_agent and event.get("callee_agent") != callee_agent:
            return False
        return True

    return filter_fn
