"""GET /open-apis/contact/v3/departments/{dept_id}/users."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import load_fixtures

router = APIRouter()


@router.get("/open-apis/contact/v3/departments/{dept_id}/users")
async def list_users(dept_id: str) -> dict:
    contact = load_fixtures().get("contact", {})
    dept = contact.get(dept_id)
    if dept is None:
        raise HTTPException(
            status_code=404, detail={"code": 33301, "msg": f"department {dept_id} not found"}
        )
    users = dept.get("users", [])
    return {
        "code": 0,
        "msg": "success",
        "data": {"items": users, "has_more": False, "page_token": ""},
    }
