"""POST /admin/reload — hot-reload registry.yaml."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import settings
from routing.registry import registry

router = APIRouter()


@router.post("/admin/reload")
async def admin_reload(request: Request):
    token = request.headers.get("Authorization", "")
    if token != f"Bearer {settings.admin_token}":
        return JSONResponse(status_code=401, content={"error": {"code": "AUTHN_TOKEN_INVALID", "message": "invalid admin token"}})
    count = await registry.reload()
    return JSONResponse({"reloaded": True, "agents": count})
