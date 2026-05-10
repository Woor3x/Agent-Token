"""POST /open-apis/auth/v3/tenant_access_token/internal — mint mock token."""
from __future__ import annotations

from fastapi import APIRouter

from ..config import load_fixtures

router = APIRouter()


@router.post("/open-apis/auth/v3/tenant_access_token/internal")
async def tenant_access_token(body: dict) -> dict:
    # Real API expects {"app_id":..,"app_secret":..}; we accept anything.
    fx = load_fixtures().get("tenant_access_token", {})
    return {
        "code": 0,
        "msg": "ok",
        "tenant_access_token": fx.get("token", "t-mock"),
        "expire": int(fx.get("expire", 7200)),
    }
