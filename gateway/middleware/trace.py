"""W3C Traceparent + Baggage injection middleware."""
import random
import time
from fastapi import Request


def _generate_trace_id() -> str:
    return f"{random.getrandbits(128):032x}"


def _generate_span_id() -> str:
    return f"{random.getrandbits(64):016x}"


async def trace_middleware(request: Request, call_next):
    incoming = request.headers.get("traceparent", "")
    trace_id: str
    parent_span: str | None = None

    if incoming and incoming.startswith("00-"):
        parts = incoming.split("-")
        if len(parts) == 4:
            trace_id = parts[1]
            parent_span = parts[2]
        else:
            trace_id = _generate_trace_id()
    else:
        trace_id = _generate_trace_id()

    span_id = _generate_span_id()
    traceparent = f"00-{trace_id}-{span_id}-01"

    # baggage 只携带 trace_id，业务字段（plan_id/sub）在路由层构造 ForwardContext 时补充
    baggage = f"trace_id={trace_id}"

    request.state.trace_id = trace_id
    request.state.span_id = span_id
    request.state.traceparent = traceparent
    request.state.baggage = baggage

    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    response.headers["traceparent"] = traceparent
    return response