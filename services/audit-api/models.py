"""Pydantic v2 models for Audit API request/response validation."""
from typing import Any, Optional

from pydantic import BaseModel


# ── Inbound event (from gateway / idp / anomaly) ─────────────────────────────

class AuditEvent(BaseModel):
    """Single event as posted by a producer. Only event_type is required."""

    event_id: Optional[str] = None          # auto-generated if absent
    timestamp: Optional[str] = None         # ISO-8601 UTC; auto-filled if absent
    event_type: str                          # authz_decision|token_issued|token_consumed|revoke_issued|anomaly|agent_registered

    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    plan_id: Optional[str] = None
    task_id: Optional[str] = None

    decision: Optional[str] = None          # allow|deny
    deny_reasons: Optional[list[str]] = None
    caller_agent: Optional[str] = None
    caller_sub: Optional[str] = None
    caller_jti: Optional[str] = None
    delegation_chain: Optional[Any] = None  # list or dict, stored as JSON
    dpop_jkt: Optional[str] = None

    callee_agent: Optional[str] = None
    callee_action: Optional[str] = None
    callee_resource: Optional[str] = None

    raw_prompt: Optional[str] = None
    purpose: Optional[str] = None

    token_aud: Optional[str] = None
    token_scope: Optional[Any] = None       # list[str] or str, stored as JSON
    token_one_time: Optional[bool] = None
    token_exp: Optional[int] = None
    consumed_at: Optional[str] = None
    consumed_by: Optional[str] = None

    revoke_type: Optional[str] = None
    revoke_value: Optional[str] = None
    revoke_reason: Optional[str] = None

    anomaly_rule: Optional[str] = None
    severity: Optional[str] = None

    result_status: Optional[int] = None
    result_bytes: Optional[int] = None
    latency_ms: Optional[int] = None
    policy_version: Optional[str] = None
    extra: Optional[Any] = None             # dict, stored as JSON


class IngestRequest(BaseModel):
    events: list[AuditEvent]


class IngestError(BaseModel):
    event_id: str
    reason: str


class IngestResponse(BaseModel):
    accepted: int
    failed: int
    errors: list[IngestError] = []


# ── Query responses ───────────────────────────────────────────────────────────

class EventListResponse(BaseModel):
    total: int
    events: list[dict]
    next_offset: Optional[int] = None


class TraceSpan(BaseModel):
    span_id: Optional[str]
    parent_span_id: Optional[str]
    caller: Optional[str]
    callee: Optional[str]
    decision: Optional[str]
    latency_ms: Optional[int]
    event_id: str
    children: list["TraceSpan"] = []


TraceSpan.model_rebuild()


class TraceResponse(BaseModel):
    trace_id: str
    started_at: Optional[str]
    ended_at: Optional[str]
    total_spans: int
    decisions: dict[str, int]
    spans: list[TraceSpan]


class PlanTask(BaseModel):
    task_id: Optional[str]
    agent: Optional[str]
    action: Optional[str]
    jti: Optional[str]
    issued_at: Optional[str]
    consumed_at: Optional[str]
    decision: Optional[str]
    latency_ms: Optional[int]


class PlanResponse(BaseModel):
    plan_id: str
    user: Optional[str]
    orchestrator: Optional[str]
    tasks: list[PlanTask]
    summary: dict[str, int]


class StatsResponse(BaseModel):
    window: str
    total: int
    by_decision: dict[str, int]
    by_agent: dict[str, dict[str, int]]
    by_reason: dict[str, int]
    tokens_issued: int
    tokens_consumed: int
    revoke_events: int
    anomaly_events: int


class HealthResponse(BaseModel):
    status: str
    db: str
    queue_depth: int
    sse_subscribers: int
