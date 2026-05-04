"""FastAPI auth dependencies for service and admin token validation."""
from fastapi import Request

from config import settings
from errors import AuthError, ForbiddenError


async def require_service_token(request: Request) -> str:
    """Validate Bearer token against the configured set of service tokens.

    Used by: POST /audit/events
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError(message="Missing or invalid Authorization header")
    token = auth[7:].strip()
    if token not in settings.service_token_set:
        raise ForbiddenError(message="Invalid service token")
    return token


async def require_admin_token(request: Request) -> str:
    """Validate Bearer token against the admin token.

    Used by: GET /audit/events, /traces, /plans, /stats
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError(message="Missing or invalid Authorization header")
    token = auth[7:].strip()
    if token != settings.admin_token:
        raise ForbiddenError(message="Invalid admin token")
    return token


async def require_service_or_admin_token(request: Request) -> str:
    """Accept either a service token or admin token.

    Used by: GET /audit/stream (SSE)
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError(message="Missing or invalid Authorization header")
    token = auth[7:].strip()
    if token in settings.service_token_set or token == settings.admin_token:
        return token
    raise ForbiddenError(message="Invalid token")
