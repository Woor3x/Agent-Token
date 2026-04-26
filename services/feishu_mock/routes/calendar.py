"""GET /open-apis/calendar/v4/calendars/{cal_id}/events."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import load_fixtures

router = APIRouter()


@router.get("/open-apis/calendar/v4/calendars/{cal_id}/events")
async def list_events(cal_id: str) -> dict:
    calendar = load_fixtures().get("calendar", {})
    cal = calendar.get(cal_id)
    if cal is None:
        raise HTTPException(
            status_code=404, detail={"code": 195100, "msg": f"calendar {cal_id} not found"}
        )
    events = cal.get("events", [])
    return {"code": 0, "msg": "success", "data": {"items": events}}
