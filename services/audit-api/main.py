"""Audit API — FastAPI application entry point with all endpoints."""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

import backup
import queries
from auth import (
    require_admin_token,
    require_service_or_admin_token,
    require_service_token,
)
from config import settings
from db import close_db, init_db
from errors import (
    AuditAPIError,
    NotFoundError,
    audit_error_handler,
    unhandled_error_handler,
)
from filters import build_sse_filter
from models import IngestError, IngestRequest, IngestResponse
from sse import sse_broadcaster
from writer import SSE_SUBSCRIBERS, batch_writer

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

_VALID_WINDOWS = {"1h", "24h", "7d"}
_VALID_EVENT_TYPES = {
    "authz_decision",
    "token_issued",
    "token_consumed",
    "revoke_issued",
    "anomaly",
    "agent_registered",
}


def _make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _normalise(event_dict: dict) -> dict:
    """Fill event_id and timestamp if absent; coerce deny_reasons to list."""
    if not event_dict.get("event_id"):
        event_dict["event_id"] = _make_event_id()
    if not event_dict.get("timestamp"):
        event_dict["timestamp"] = _utcnow_iso()
    deny = event_dict.get("deny_reasons")
    if deny is None:
        event_dict["deny_reasons"] = []
    elif isinstance(deny, str):
        try:
            event_dict["deny_reasons"] = json.loads(deny)
        except Exception:
            event_dict["deny_reasons"] = [deny]
    return event_dict


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────────
    backup.configure(settings.backup_dir)
    await init_db(settings.db_path)
    # Apply settings to singleton (may differ from constructor defaults)
    batch_writer._batch_size = settings.batch_size
    batch_writer._flush_interval = settings.flush_interval_ms / 1000.0
    batch_writer.start()
    logger.info("audit-api started on %s:%d", settings.host, settings.port)
    yield
    # ── Shutdown ───────────────────────────────────────────────────────────────
    await batch_writer.stop()
    await close_db()
    logger.info("audit-api shutdown complete")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Audit API",
    version="1.0.0",
    description="Centralised audit event store for Agent-Token system",
    lifespan=lifespan,
)
app.add_exception_handler(AuditAPIError, audit_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


# ── POST /audit/events ────────────────────────────────────────────────────────

@app.post("/audit/events")
async def ingest_events(
    body: IngestRequest,
    _token: str = Depends(require_service_token),
):
    """Batch receive events from gateway / idp / anomaly (service token required)."""
    accepted = 0
    failed = 0
    errors: list[IngestError] = []

    for event in body.events:
        event_dict = _normalise(event.model_dump(exclude_none=False))

        if event_dict.get("event_type") not in _VALID_EVENT_TYPES:
            eid = event_dict.get("event_id", "unknown")
            errors.append(
                IngestError(
                    event_id=eid,
                    reason=f"unknown event_type: {event_dict.get('event_type')}",
                )
            )
            failed += 1
            continue

        ok = batch_writer.enqueue(event_dict)
        if ok:
            accepted += 1
        else:
            errors.append(
                IngestError(event_id=event_dict["event_id"], reason="queue_full")
            )
            failed += 1

    status_code = 207 if failed else 200
    return JSONResponse(
        status_code=status_code,
        content=IngestResponse(
            accepted=accepted, failed=failed, errors=errors
        ).model_dump(),
    )


# ── GET /audit/events ─────────────────────────────────────────────────────────

@app.get("/audit/events")
async def list_events(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    _token: str = Depends(require_admin_token),
):
    """Paginated event list with optional filters (admin token required)."""
    limit = min(limit, 100)
    # Pass all query params to the filter builder; strip pagination keys first
    params = dict(request.query_params)
    params.pop("limit", None)
    params.pop("offset", None)

    total, events = await queries.list_events(params, limit=limit, offset=offset)
    next_offset = offset + limit if (offset + limit) < total else None
    return {"total": total, "events": events, "next_offset": next_offset}


# ── GET /audit/events/{event_id} ──────────────────────────────────────────────

@app.get("/audit/events/{event_id}")
async def get_event(
    event_id: str,
    _token: str = Depends(require_admin_token),
):
    """Single event lookup (admin token required)."""
    event = await queries.get_event(event_id)
    if event is None:
        raise NotFoundError(message=f"event {event_id!r} not found")
    return event


# ── GET /audit/traces/{trace_id} ─────────────────────────────────────────────

@app.get("/audit/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    _token: str = Depends(require_admin_token),
):
    """Fetch all spans for a trace and return a hierarchical tree (admin token required)."""
    result = await queries.get_trace(trace_id)
    if not result:
        raise NotFoundError(message=f"trace {trace_id!r} not found")
    return result


# ── GET /audit/plans/{plan_id} ────────────────────────────────────────────────

@app.get("/audit/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    _token: str = Depends(require_admin_token),
):
    """Summarise all events for a plan (admin token required)."""
    result = await queries.get_plan(plan_id)
    if not result:
        raise NotFoundError(message=f"plan {plan_id!r} not found")
    return result


# ── GET /audit/stats ──────────────────────────────────────────────────────────

@app.get("/audit/stats")
async def get_stats(
    window: str = "1h",
    _token: str = Depends(require_admin_token),
):
    """Aggregate statistics for a time window: 1h / 24h / 7d (admin token required)."""
    if window not in _VALID_WINDOWS:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"window must be one of {sorted(_VALID_WINDOWS)}",
                }
            },
        )
    return await queries.get_stats(window)


# ── GET /audit/stream (SSE) ───────────────────────────────────────────────────

@app.get("/audit/stream")
async def stream_events(
    request: Request,
    _token: str = Depends(require_service_or_admin_token),
):
    """Server-Sent Events stream for real-time audit consumption (service or admin token)."""
    filter_fn = build_sse_filter(dict(request.query_params))
    client_id = uuid.uuid4().hex[:8]

    async def event_generator():
        q = await sse_broadcaster.subscribe(filter_fn)
        SSE_SUBSCRIBERS.inc()
        try:
            yield f'event: connected\ndata: {{"client_id":"{client_id}"}}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        q.get(), timeout=settings.sse_heartbeat_sec
                    )
                    data = json.dumps(event, ensure_ascii=False)
                    yield f"event: audit_event\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    ts = _utcnow_iso()
                    yield f'event: heartbeat\ndata: {{"ts":"{ts}"}}\n\n'
        finally:
            await sse_broadcaster.unsubscribe(q)
            SSE_SUBSCRIBERS.dec()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /healthz ──────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """Service health check — includes DB ping, queue depth, SSE subscriber count."""
    db_status = "ok"
    try:
        from db import get_db
        db = await get_db()
        await db.execute("SELECT 1")
    except Exception:
        db_status = "error"

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "db": db_status,
        "queue_depth": batch_writer.queue_depth,
        "sse_subscribers": sse_broadcaster.subscriber_count,
    }


# ── GET /metrics ──────────────────────────────────────────────────────────────

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint (no auth required — expose on internal network only)."""
    data = generate_latest()
    return StreamingResponse(content=iter([data]), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
