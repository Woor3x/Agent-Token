"""AuditAPI exception hierarchy + FastAPI error handlers."""
from fastapi import Request
from fastapi.responses import JSONResponse


class AuditAPIError(Exception):
    http_status: int = 500
    code: str = "SERVER_ERROR"

    def __init__(self, code: str | None = None, message: str = "", detail: str = ""):
        self.code = code or self.__class__.code
        self.message = message or self.code
        self.detail = detail
        super().__init__(self.message)


class AuthError(AuditAPIError):
    http_status = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AuditAPIError):
    http_status = 403
    code = "FORBIDDEN"


class NotFoundError(AuditAPIError):
    http_status = 404
    code = "NOT_FOUND"


class ValidationError(AuditAPIError):
    http_status = 422
    code = "VALIDATION_ERROR"


async def audit_error_handler(request: Request, exc: AuditAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "SERVER_ERROR", "message": "internal error"}},
    )
