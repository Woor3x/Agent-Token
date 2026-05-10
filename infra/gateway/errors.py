from fastapi import Request
from fastapi.responses import JSONResponse
import uuid


class GatewayError(Exception):
    """Base error — subclasses carry HTTP status + business code."""
    http_status: int = 500
    code: str = "SERVER_ERROR"

    def __init__(self, code: str | None = None, message: str = "", detail: str = ""):
        self.code = code or self.__class__.code
        self.message = message or self.code
        self.detail = detail
        super().__init__(self.message)


class AuthnError(GatewayError):
    http_status = 401
    code = "AUTHN_TOKEN_INVALID"


class AuthzError(GatewayError):
    http_status = 403
    code = "AUTHZ_DENIED"


class IntentError(GatewayError):
    http_status = 400
    code = "INTENT_INVALID"


class RateLimitError(GatewayError):
    http_status = 429
    code = "RATE_LIMITED"


class UpstreamError(GatewayError):
    http_status = 502
    code = "UPSTREAM_FAIL"


class CircuitOpenError(GatewayError):
    http_status = 503
    code = "CIRCUIT_OPEN"


class UpstreamTimeoutError(GatewayError):
    http_status = 504
    code = "UPSTREAM_TIMEOUT"


class IdempotencyError(GatewayError):
    http_status = 409
    code = "IDEMPOTENCY_CONFLICT"


# ── named error constructors ──────────────────────────────────────────────────

def authn_invalid(reason: str = "") -> AuthnError:
    return AuthnError("AUTHN_TOKEN_INVALID", f"token invalid: {reason}")

def authn_dpop_invalid(reason: str = "") -> AuthnError:
    return AuthnError("AUTHN_DPOP_INVALID", f"dpop invalid: {reason}")

def authn_revoked(dim: str = "") -> AuthnError:
    return AuthnError("AUTHN_REVOKED", f"revoked: {dim}")

def token_replayed() -> AuthnError:
    return AuthnError("TOKEN_REPLAYED", "one-time token already consumed")

def authz_audience_mismatch() -> AuthzError:
    return AuthzError("AUTHZ_AUDIENCE_MISMATCH", "audience mismatch")

def authz_scope_exceeded() -> AuthzError:
    return AuthzError("AUTHZ_SCOPE_EXCEEDED", "intent exceeds granted scope")

def authz_executor_mismatch() -> AuthzError:
    return AuthzError("AUTHZ_EXECUTOR_MISMATCH", "action executor mismatch")

def authz_delegation_rejected(reason: str = "") -> AuthzError:
    return AuthzError("AUTHZ_DELEGATION_REJECTED", f"delegation rejected: {reason}")

def authz_depth_exceeded() -> AuthzError:
    return AuthzError("AUTHZ_DEPTH_EXCEEDED", "delegation chain too deep")


def _error_body(request: Request, exc: GatewayError) -> dict:
    trace_id = getattr(getattr(request, "state", None), "trace_id", None) or ""
    audit_id = getattr(getattr(request, "state", None), "audit_id", None) or ""
    from config import settings
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "trace_id": trace_id,
            "audit_id": audit_id,
            "policy_version": settings.policy_version,
        }
    }


async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=_error_body(request, exc))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    body = {
        "error": {
            "code": "SERVER_ERROR",
            "message": "internal error",
            "trace_id": getattr(getattr(request, "state", None), "trace_id", None) or "",
            "audit_id": "",
            "policy_version": "",
        }
    }
    return JSONResponse(status_code=500, content=body)
