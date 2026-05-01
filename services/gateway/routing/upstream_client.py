"""Upstream call — httpx AsyncClient with optional mTLS, response sanitizer."""
import logging
from dataclasses import dataclass, field

import httpx
from fastapi.responses import Response

from config import settings
from routing.circuit_breaker import get_breaker
from routing.registry import AgentConfig

logger = logging.getLogger(__name__)

# 转发给上游前必须剥离的请求头：
# host/content-length 由 httpx 重新计算；transfer-encoding 与流式传输冲突
_STRIP_FORWARD_HEADERS = frozenset(["host", "content-length", "transfer-encoding"])

# 从上游响应中剥离、不回传给调用方的响应头
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


@dataclass
class ForwardContext:
    """转发请求所需的上下文，由路由层从 Request 中提取或由后台任务手动构造。"""
    method: str
    path: str
    headers: dict[str, str]
    trace_id: str
    span_id: str
    baggage: str = ""


def _build_ssl_context(cfg: AgentConfig) -> bool | str:
    if not settings.mtls_enabled or not cfg.mtls:
        return True
    return cfg.mtls.get("ca", True)


async def call_upstream(
    agent_id: str,
    cfg: AgentConfig,
    body: bytes,
    ctx: ForwardContext,
) -> Response:
    """转发请求到上游 Agent，处理熔断器、mTLS、超时和响应头清理。"""
    from errors import UpstreamError, UpstreamTimeoutError

    breaker = get_breaker(agent_id)
    await breaker.before_call()

    headers = {
        k: v for k, v in ctx.headers.items()
        if k.lower() not in _STRIP_FORWARD_HEADERS
    }
    headers["traceparent"] = f"00-{ctx.trace_id}-{ctx.span_id}-01"
    if ctx.baggage:
        headers["baggage"] = ctx.baggage

    ssl_ctx = _build_ssl_context(cfg)
    cert = None
    if settings.mtls_enabled and cfg.mtls:
        cert = (cfg.mtls.get("cert"), cfg.mtls.get("key"))

    upstream_url = f"{cfg.upstream.rstrip('/')}{ctx.path}"

    try:
        async with httpx.AsyncClient(
            verify=ssl_ctx,
            cert=cert,
            timeout=cfg.timeout_ms / 1000,
        ) as client:
            r = await client.request(
                method=ctx.method,
                url=upstream_url,
                headers=headers,
                content=body,
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
    """剥离敏感响应头，返回 FastAPI Response。"""
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
