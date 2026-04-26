"""docx v1: create document + batch_update blocks."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter

from ..config import DOC_STORE

router = APIRouter()


@router.post("/open-apis/docx/v1/documents")
async def create_document(body: dict) -> dict:
    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    DOC_STORE[doc_id] = {
        "document_id": doc_id,
        "title": body.get("title", ""),
        "folder_token": body.get("folder_token", ""),
        "created_at": int(time.time()),
        "blocks": [],
    }
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "document": {
                "document_id": doc_id,
                "revision_id": 1,
                "title": body.get("title", ""),
            }
        },
    }


@router.post("/open-apis/docx/v1/documents/{document_id}/blocks/batch_update")
async def batch_update(document_id: str, body: dict) -> dict:
    doc = DOC_STORE.setdefault(
        document_id, {"document_id": document_id, "title": "", "blocks": []}
    )
    reqs = body.get("requests", [])
    doc["blocks"].extend(reqs)
    return {
        "code": 0,
        "msg": "success",
        "data": {"document_revision_id": len(doc["blocks"]) + 1},
    }
