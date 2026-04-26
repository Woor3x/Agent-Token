"""Upstream call — httpx AsyncClient with optional mTLS, response sanitizer."""
import logging
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import Response

from config import settings
from routing.circuit_breaker import get_breaker
from routing.registry import AgentConfig

logger = logging.getLogger(__name__)

_STRIP_RESPONSE_HEADERS = frozenset(
    [
        "x-internal-token",
        "x-agent-secret",
        "authorization",
        "set-cookie",
        "x-forwarded-for",
        "x-real-ip",
    ]
)


def _build_ssl_context(cfg: AgentConfig) -> bool | str:
    if not settings.mtls_enabled or not cfg.mtls:
        return True   # verify cert but no client cert
    # httpx accepts ssl.SSLContext; for simplicity return ca path
    return cfg.mtls.get("ca", True)


async def call_upstream(
    request: Request,
    agent_id: str,
    cfg: AgentConfig,
    body: bytes,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """Forward request to upstream, return FastAPI Response.

    Handles circuit breaker, timeout, and response sanitization.
    """
    from errors import UpstreamError, UpstreamTimeoutError

    breaker = get_breaker(agent_id)
    await breaker.before_call()

    timeout = cfg.timeout_ms / 1000
    forward_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    if extra_headers:
        forward_headers.update(extra_headers)

    # Inject trace context
    trace_id = getattr(request.state, "trace_id", "")
    span_id = getattr(request.state, "span_id", "")
    forward_headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
    baggage = getattr(request.state, "baggage", "")
    if baggage:
        forward_headers["baggage"] = baggage

    ssl_ctx = _build_ssl_context(cfg)
    cert = None
    if settings.mtls_enabled and cfg.mtls:
        cert = (cfg.mtls.get("cert"), cfg.mtls.get("key"))

    try:
        async with httpx.AsyncClient(
            verify=ssl_ctx,
            cert=cert,
            timeout=timeout,
        ) as client:
            # Agent ``/invoke`` is the canonical receiving endpoint (M4 server).
            # Rewrite ``/a2a/invoke`` (gateway path) → ``/invoke`` (agent path).
            agent_path = "/invoke" if request.url.path == "/a2a/invoke" else request.url.path
            upstream_url = f"{cfg.upstream.rstrip('/')}{agent_path}"
            r = await client.request(
                method=request.method,
                url=upstream_url,
                headers=forward_headers,
                content=body,
                params=dict(request.query_params),
            )
    except httpx.TimeoutException as exc:
        await breaker.on_failure()
        raise UpstreamTimeoutError("UPSTREAM_TIMEOUT", str(exc))
    except httpx.HTTPError as exc:
        await breaker.on_failure()
        raise UpstreamError("UPSTREAM_FAIL", str(exc))

    await breaker.on_success()
    return _sanitize(r)


def _sanitize(r: httpx.Response) -> Response:
    """Strip sensitive headers and return FastAPI Response."""
    safe_headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower() not in _STRIP_RESPONSE_HEADERS
    }
    return Response(
        content=r.content,
        status_code=r.status_code,
        headers=safe_headers,
        media_type=r.headers.get("content-type"),
    )
