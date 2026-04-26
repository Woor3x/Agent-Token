"""GET /open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..config import load_fixtures

router = APIRouter()


@router.get(
    "/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
)
async def list_records(
    app_token: str,
    table_id: str,
    page_size: int = Query(100, ge=1, le=1000),
    view_id: str | None = None,
) -> dict:
    bitable = load_fixtures().get("bitable", {})
    app = bitable.get(app_token)
    if not app:
        raise HTTPException(
            status_code=404, detail={"code": 91402, "msg": f"app_token {app_token} not found"}
        )
    table = app.get(table_id)
    if table is None:
        raise HTTPException(
            status_code=404, detail={"code": 91403, "msg": f"table {table_id} not found"}
        )
    records = table.get("records", [])[:page_size]
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "items": records,
            "has_more": False,
            "page_token": "",
            "total": len(records),
        },
    }
