"""Feishu Mock server smoke tests."""
from __future__ import annotations

import httpx
import pytest

from services.feishu_mock.main import app


@pytest.mark.asyncio
async def test_tenant_access_token() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": "x", "app_secret": "y"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["tenant_access_token"].startswith("t-")


@pytest.mark.asyncio
async def test_bitable_records_happy_path() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get(
            "/open-apis/bitable/v1/apps/bascn_alice/tables/tbl_q1/records?page_size=10"
        )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert len(items) == 4
    assert items[0]["fields"]["region"] == "North"


@pytest.mark.asyncio
async def test_bitable_missing_app() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get(
            "/open-apis/bitable/v1/apps/unknown/tables/tbl_q1/records"
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_contact_and_calendar() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get("/open-apis/contact/v3/departments/sales/users")
        assert r.status_code == 200
        users = r.json()["data"]["items"]
        assert {u["name"] for u in users} == {"Alice", "Bob"}
        r2 = await c.get("/open-apis/calendar/v4/calendars/cal_team/events")
        assert r2.status_code == 200
        assert len(r2.json()["data"]["items"]) == 2


@pytest.mark.asyncio
async def test_docx_create_and_update() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/open-apis/docx/v1/documents",
            json={"folder_token": "", "title": "Hello"},
        )
        assert r.status_code == 200
        doc_id = r.json()["data"]["document"]["document_id"]
        r2 = await c.post(
            f"/open-apis/docx/v1/documents/{doc_id}/blocks/batch_update",
            json={"requests": [{"block_type": "text", "text": "hi"}]},
        )
        assert r2.status_code == 200
        assert r2.json()["data"]["document_revision_id"] >= 1
