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


@router.get("/open-apis/bitable/v1/apps/{app_token}/tables")
async def list_tables(app_token: str, page_size: int = Query(50, ge=1, le=200)) -> dict:
    fixtures = load_fixtures()
    tables = fixtures.get("bitable_tables", {}).get(app_token)
    if tables is None:
        # Fall back: derive from the bitable fixture map so editors can drop
        # in a new app_token without also touching ``bitable_tables``.
        bitable = fixtures.get("bitable", {})
        app = bitable.get(app_token)
        if app is None:
            raise HTTPException(
                status_code=404,
                detail={"code": 91402, "msg": f"app_token {app_token} not found"},
            )
        tables = [{"table_id": tid, "name": tid} for tid in app.keys()]
    items = tables[:page_size]
    return {
        "code": 0,
        "msg": "success",
        "data": {"items": items, "has_more": False, "page_token": "", "total": len(items)},
    }
