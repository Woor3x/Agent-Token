import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from agents.loader import load_capabilities
from agents.sod_check import run_global_sod_check
from audit.writer import get_audit_writer, init_audit_writer
from config import settings
from errors import IdPError
from jwks.cache import invalidate_cache
from kms.store import init_kms
from storage.redis import init_redis, close_redis
from storage.sqlite import close_db, init_db
from users.loader import load_users

REQUEST_COUNT = Counter(
    "idp_requests_total", "Total HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "idp_request_duration_seconds", "HTTP request latency", ["method", "path"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(settings.sqlite_path)
    await init_redis(settings.redis_url)
    init_kms(settings.idp_kms_passphrase, settings.kms_keys_dir)
    load_capabilities(settings.capabilities_dir)
    run_global_sod_check()
    await load_users(settings.users_dir)
    writer = init_audit_writer()
    writer.start()
    yield
    await writer.stop()
    await close_redis()
    await close_db()


app = FastAPI(
    title="Agent-Token IdP",
    version="1.0.0",
    description="Identity Provider for Agent-Token system (RFC 7523 / RFC 8693 / DPoP)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()

    response = await call_next(request)

    elapsed = time.monotonic() - start
    route = request.scope.get("route")
    path = route.path if route else "other"
    method = request.method
    status = response.status_code

    REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(elapsed)

    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(IdPError)
async def idp_error_handler(request: Request, exc: IdPError):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "trace_id": request_id,
                "policy_version": settings.policy_version,
            }
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "server_error",
                "message": "Internal server error",
                "trace_id": request_id,
                "policy_version": settings.policy_version,
            }
        },
    )


@app.get("/.well-known/openid-configuration")
async def openid_configuration():
    base = settings.idp_issuer
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oidc/authorize",
        "token_endpoint": f"{base}/oidc/token",
        "userinfo_endpoint": f"{base}/oidc/userinfo",
        "jwks_uri": f"{base}/jwks",
        "scopes_supported": ["openid", "profile", "agent:invoke"],
        "response_types_supported": ["code"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:token-exchange",
        ],
        "token_endpoint_auth_methods_supported": [
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ],
        "code_challenge_methods_supported": ["S256"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/healthz")
async def healthz():
    from storage.sqlite import get_db
    from storage.redis import get_redis
    checks: dict[str, str] = {}

    try:
        db = await get_db()
        await db.execute("SELECT 1")
        checks["sqlite"] = "ok"
    except Exception as exc:
        checks["sqlite"] = f"error: {exc}"

    try:
        r = await get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    try:
        from kms.store import get_kms
        get_kms().get_active_signing_key()
        checks["kms"] = "ok"
    except Exception as exc:
        checks["kms"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )


@app.get("/metrics")
async def metrics():
    from fastapi.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/admin/reload")
async def admin_reload(request: Request):
    from agents.register import _check_admin
    _check_admin(request)

    load_capabilities(settings.capabilities_dir)
    run_global_sod_check()
    await load_users(settings.users_dir)
    invalidate_cache()

    return {"reloaded": True}


@app.post("/admin/rotate-idp-key")
async def admin_rotate_idp_key(request: Request):
    from agents.register import _check_admin
    from kms.rotator import rotate_idp_key
    _check_admin(request)
    result = await rotate_idp_key()
    invalidate_cache()
    return result


from jwks.handler import router as jwks_router
from oidc.authorize import router as authorize_router
from oidc.token import router as oidc_token_router
from oidc.userinfo import router as userinfo_router
from oidc.refresh import router as refresh_router
from token_exchange.handler import router as token_exchange_router
from plan.validate import router as plan_router
from revoke.handler import router as revoke_router
from agents.register import router as agents_register_router
from agents.rotate import router as agents_rotate_router

app.include_router(jwks_router)
app.include_router(authorize_router)
app.include_router(oidc_token_router)
app.include_router(userinfo_router)
app.include_router(refresh_router)
app.include_router(token_exchange_router)
app.include_router(plan_router)
app.include_router(revoke_router)
app.include_router(agents_register_router)
app.include_router(agents_rotate_router)
