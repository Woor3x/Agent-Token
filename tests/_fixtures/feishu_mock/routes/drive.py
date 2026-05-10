"""GET /open-apis/drive/v1/files — mock drive listing.

Mirrors the real Feishu Open Platform shape closely enough for the front-end
file picker to work against either backend without code branching.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..config import load_fixtures

router = APIRouter()


@router.get("/open-apis/drive/v1/files")
async def list_files(
    folder_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    drive = load_fixtures().get("drive", {})
    # ``folder_token`` empty (or absent) → app's root drive.
    folder = drive.get(folder_token) if folder_token else drive.get("root", {})
    if folder is None:
        return {
            "code": 0,
            "msg": "success",
            "data": {"files": [], "has_more": False, "next_page_token": ""},
        }
    files = folder.get("files", [])[:page_size]
    return {
        "code": 0,
        "msg": "success",
        "data": {"files": files, "has_more": False, "next_page_token": ""},
    }
