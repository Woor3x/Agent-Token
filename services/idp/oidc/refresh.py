from fastapi import APIRouter, Form

from errors import InvalidRequest
from oidc.session import revoke_refresh_token

router = APIRouter()


@router.post("/oidc/revoke")
async def revoke_token(
    token: str = Form(...),
    token_type_hint: str = Form(default="refresh_token"),
):
    if token_type_hint == "refresh_token":
        await revoke_refresh_token(token)
    return {}
