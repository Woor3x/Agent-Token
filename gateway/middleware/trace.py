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

    # baggage: carry plan_id and sub for downstream correlation
    claims = getattr(request.state, "token_claims", {}) or {}
    baggage_parts = [f"trace_id={trace_id}"]
    if claims.get("plan_id"):
        baggage_parts.append(f"plan_id={claims['plan_id']}")
    if claims.get("sub"):
        baggage_parts.append(f"sub={claims['sub']}")
    baggage = ",".join(baggage_parts)

    request.state.trace_id = trace_id
    request.state.span_id = span_id
    request.state.traceparent = traceparent
    request.state.baggage = baggage

    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    response.headers["traceparent"] = traceparent
    return response
