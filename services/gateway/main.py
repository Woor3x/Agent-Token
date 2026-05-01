"""Gateway (PEP) — FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from config import settings
from errors import GatewayError, gateway_error_handler, unhandled_error_handler
from middleware.audit import audit_writer
from middleware.authn import authn_middleware
from middleware.rate_limit import rate_limit_middleware
from middleware.trace import trace_middleware
from revoke.subscriber import run_subscriber
from routing.registry import registry
from jwt_token.jwks_cache import jwks_cache

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _run_subscriber_with_retry(app: FastAPI, redis_client) -> None:
    """subscriber 崩溃后自动重连。"""
    while True:
        try:
            await run_subscriber(redis_client)
            raise RuntimeError("subscriber exited unexpectedly")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("revoke subscriber crashed: %s — restarting in 5s", exc)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    redis_client = aioredis.from_url(
        settings.redis_url,
        password=settings.redis_password or None,
        decode_responses=False,
    )
    app.state.redis = redis_client

    await jwks_cache.start()
    await registry.load()
    await audit_writer.start()

    sub_task = asyncio.create_task(_run_subscriber_with_retry(app, redis_client))
    logger.info("gateway started on %s:%d", settings.host, settings.port)

    yield

    # Shutdown
    sub_task.cancel()
    await audit_writer.stop()
    await jwks_cache.stop()
    await redis_client.aclose()
    logger.info("gateway shutdown complete")


app = FastAPI(title="A2A Gateway", version="1.0.0", lifespan=lifespan)

# ── Exception handlers ─────────────────────────────────────────────────────────
app.add_exception_handler(GatewayError, gateway_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

# ── Middleware ────────────────────────────────────────────────────────────────
# Starlette 注册顺序与执行顺序相反（后注册先执行），所以倒着写
# 实际执行顺序：trace → authn → rate_limit → [路由] → rate_limit → authn → trace
app.middleware("http")(rate_limit_middleware)  # 最先注册 = 最后执行
app.middleware("http")(authn_middleware)       # JWT + DPoP + 撤销查询
app.middleware("http")(trace_middleware)       # 最后注册 = 最先执行，所有请求都能拿到 trace_id


# ── Routes ────────────────────────────────────────────────────────────────────
from routes.invoke import router as invoke_router
from routes.plan import router as plan_router
from routes.admin import router as admin_router

app.include_router(invoke_router)
app.include_router(plan_router)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz(request: Request):
    redis: aioredis.Redis = request.app.state.redis
    checks: dict[str, str] = {}
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    import httpx
    for name, url in [("idp", settings.idp_jwks_url), ("opa", f"{settings.opa_url}/allow")]:
        try:
            async with httpx.AsyncClient(timeout=1.0) as c:
                r = await c.get(url if name == "idp" else url.rsplit("/", 1)[0])
                checks[name] = "ok" if r.status_code < 500 else "error"
        except Exception:
            checks[name] = "error"

    from routing.circuit_breaker import all_breaker_states
    return {
        "status": "ok" if all(v == "ok" for v in checks.values()) else "degraded",
        "upstreams": checks,
        "circuit_breakers": all_breaker_states(),
    }


@app.get("/metrics")
async def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
