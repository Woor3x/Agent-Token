"""
tests/test_api.py — Audit API 全链路集成测试

策略
────
* FastAPI app 通过 httpx.ASGITransport 驱动，走真实 route handler
* SQLite 用 :memory: 数据库，不落盘
* BatchWriter 以 batch_size=1 + flush_interval=10ms 运行，几乎即时落库
* settings 字段在 fixture 中直接覆写（pydantic-settings 字段默认可变）

覆盖范围
────────
Auth    : service token / admin token / 缺头 / 错误 token / 权限隔离
Ingest  : 单条、批量、未知 event_type(207)、混合批次、所有合法类型、字段自动填充
Query   : GET list 分页、多维过滤（event_type / decision / trace_id）、单条 get / 404
Trace   : span 树装配（parent_span_id → children）、not found
Plan    : plan 聚合（user / orchestrator / tasks / summary）、not found
Stats   : 三种时间窗口、非法 window → 400
Healthz : DB 在线检查
SSE     : 连接建立后收到 connected 事件
"""

import asyncio
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

# ─────────────────────────────────────────────────────────────────────────────
# 常量与辅助
# ─────────────────────────────────────────────────────────────────────────────

ADMIN_HDR = {"Authorization": "Bearer admin-test"}
SVC_HDR   = {"Authorization": "Bearer svc-test"}
POST_URL  = "/audit/events"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _event(event_type: str = "authz_decision", **kwargs) -> dict:
    return {"event_type": event_type, **kwargs}


# ─────────────────────────────────────────────────────────────────────────────
# Fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def api():
    """
    启动 audit-api ASGI 应用，注入测试依赖。
    返回 httpx.AsyncClient。
    """
    import sys
    api_dir = str(Path(__file__).parent.parent)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    # 覆写 settings（pydantic-settings 默认可变，@property service_token_set 动态计算）
    from config import settings
    settings.admin_token = "admin-test"
    settings.service_tokens = "svc-test"
    settings.batch_size = 1
    settings.flush_interval_ms = 10
    settings.backup_dir = "/tmp/audit_test_backup"
    settings.sse_heartbeat_sec = 0.05

    import db as _db_mod
    import writer as _writer_mod
    import backup as _backup_mod
    from main import app

    # 手动初始化（ASGITransport 不触发 lifespan）
    _backup_mod.configure("/tmp/audit_test_backup")
    await _db_mod.init_db(":memory:")

    _writer_mod.batch_writer._batch_size = 1
    _writer_mod.batch_writer._flush_interval = 0.01
    _writer_mod.batch_writer.start()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    await _writer_mod.batch_writer.stop()
    await _db_mod.close_db()


async def _wait_flush() -> None:
    """等待 BatchWriter 完成落库（batch_size=1, interval=10ms → 50ms 足够）。"""
    await asyncio.sleep(0.05)


async def _ingest(client, *events, **event_kwargs) -> None:
    """写入一批事件并等待落库。"""
    if not events:
        events = (_event(**event_kwargs),)
    r = await client.post(POST_URL, headers=SVC_HDR,
                          json={"events": list(events)})
    assert r.status_code in (200, 207)
    await _wait_flush()


# ─────────────────────────────────────────────────────────────────────────────
# 认证
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth:
    async def test_post_no_auth_header(self, api):
        r = await api.post(POST_URL, json={"events": [_event()]})
        assert r.status_code == 401

    async def test_post_wrong_service_token(self, api):
        r = await api.post(POST_URL, json={"events": [_event()]},
                           headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403

    async def test_get_no_auth_header(self, api):
        r = await api.get("/audit/events")
        assert r.status_code == 401

    async def test_get_wrong_admin_token(self, api):
        r = await api.get("/audit/events", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403

    async def test_service_token_cannot_read(self, api):
        """service token 只能写，不能读。"""
        r = await api.get("/audit/events", headers=SVC_HDR)
        assert r.status_code == 403

    async def test_admin_token_can_read(self, api):
        r = await api.get("/audit/events", headers=ADMIN_HDR)
        assert r.status_code == 200

    async def test_admin_token_cannot_post(self, api):
        """admin token 不在 service_token_set 中，不能写。"""
        r = await api.post(POST_URL, json={"events": [_event()]}, headers=ADMIN_HDR)
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Ingest
# ─────────────────────────────────────────────────────────────────────────────

class TestIngest:
    async def test_single_event_accepted(self, api):
        r = await api.post(POST_URL, headers=SVC_HDR,
                           json={"events": [_event(event_id=f"evt_{_uid()}")]})
        assert r.status_code == 200
        data = r.json()
        assert data["accepted"] == 1 and data["failed"] == 0

    async def test_batch_events_accepted(self, api):
        events = [_event(event_type="token_issued") for _ in range(3)]
        r = await api.post(POST_URL, headers=SVC_HDR, json={"events": events})
        assert r.status_code == 200
        assert r.json()["accepted"] == 3

    async def test_unknown_event_type_returns_207(self, api):
        r = await api.post(POST_URL, headers=SVC_HDR,
                           json={"events": [_event(event_type="bad_type")]})
        assert r.status_code == 207
        data = r.json()
        assert data["accepted"] == 0 and data["failed"] == 1
        assert "bad_type" in data["errors"][0]["reason"]

    async def test_mixed_batch_returns_207(self, api):
        events = [_event(), _event(event_type="bad_type")]
        r = await api.post(POST_URL, headers=SVC_HDR, json={"events": events})
        assert r.status_code == 207
        assert r.json()["accepted"] == 1 and r.json()["failed"] == 1

    async def test_event_id_auto_generated(self, api):
        """未传 event_id 时服务器自动补全，仍应 accepted=1。"""
        r = await api.post(POST_URL, headers=SVC_HDR,
                           json={"events": [{"event_type": "authz_decision"}]})
        assert r.status_code == 200
        assert r.json()["accepted"] == 1

    async def test_all_valid_event_types_accepted(self, api):
        valid_types = [
            "authz_decision", "token_issued", "token_consumed",
            "revoke_issued", "anomaly", "agent_registered",
            "key_rotated", "plan_validated",
        ]
        events = [_event(event_type=t) for t in valid_types]
        r = await api.post(POST_URL, headers=SVC_HDR, json={"events": events})
        assert r.status_code == 200
        assert r.json()["accepted"] == len(valid_types)


# ─────────────────────────────────────────────────────────────────────────────
# Event query
# ─────────────────────────────────────────────────────────────────────────────

class TestEventQuery:
    async def _write(self, client, **kwargs) -> str:
        eid = f"evt_{_uid()}"
        await _ingest(client, _event(event_id=eid, **kwargs))
        return eid

    async def test_list_returns_written_event(self, api):
        eid = await self._write(api, event_type="authz_decision")
        r = await api.get("/audit/events", headers=ADMIN_HDR)
        assert r.status_code == 200
        ids = [e["event_id"] for e in r.json()["events"]]
        assert eid in ids

    async def test_filter_by_event_type(self, api):
        eid = await self._write(api, event_type="revoke_issued")
        r = await api.get("/audit/events?event_type=revoke_issued", headers=ADMIN_HDR)
        for e in r.json()["events"]:
            assert e["event_type"] == "revoke_issued"
        assert eid in [e["event_id"] for e in r.json()["events"]]

    async def test_filter_by_trace_id(self, api):
        trace = f"trace-{_uid()}"
        eid = await self._write(api, trace_id=trace)
        r = await api.get(f"/audit/events?trace_id={trace}", headers=ADMIN_HDR)
        data = r.json()
        assert data["total"] == 1
        assert data["events"][0]["event_id"] == eid

    async def test_filter_by_decision(self, api):
        unique_trace = f"trace-{_uid()}"
        eid = await self._write(api, decision="deny", trace_id=unique_trace)
        r = await api.get(f"/audit/events?decision=deny&trace_id={unique_trace}", headers=ADMIN_HDR)
        assert any(e["event_id"] == eid for e in r.json()["events"])

    async def test_get_event_by_id(self, api):
        eid = await self._write(api, event_type="token_issued")
        r = await api.get(f"/audit/events/{eid}", headers=ADMIN_HDR)
        assert r.status_code == 200
        assert r.json()["event_id"] == eid

    async def test_get_event_not_found(self, api):
        r = await api.get("/audit/events/evt_nonexistent_000", headers=ADMIN_HDR)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"

    async def test_pagination_limit_respected(self, api):
        r = await api.get("/audit/events?limit=2", headers=ADMIN_HDR)
        assert r.status_code == 200
        assert len(r.json()["events"]) <= 2

    async def test_limit_capped_at_100(self, api):
        r = await api.get("/audit/events?limit=9999", headers=ADMIN_HDR)
        assert r.status_code == 200
        assert len(r.json()["events"]) <= 100


# ─────────────────────────────────────────────────────────────────────────────
# Trace
# ─────────────────────────────────────────────────────────────────────────────

class TestTrace:
    async def test_trace_span_tree(self, api):
        trace = f"trace-{_uid()}"
        root_span  = f"span-root-{_uid()}"
        child_span = f"span-child-{_uid()}"

        events = [
            _event(event_id=f"evt_{_uid()}", trace_id=trace,
                   span_id=root_span, decision="allow",
                   caller_agent="doc_assistant", callee_agent="data_agent"),
            _event(event_id=f"evt_{_uid()}", trace_id=trace,
                   span_id=child_span, parent_span_id=root_span,
                   decision="allow", caller_agent="data_agent"),
        ]
        await _ingest(api, *events)

        r = await api.get(f"/audit/traces/{trace}", headers=ADMIN_HDR)
        assert r.status_code == 200
        data = r.json()
        assert data["trace_id"] == trace
        assert data["total_spans"] == 2

        root = next(s for s in data["spans"] if s["span_id"] == root_span)
        assert len(root["children"]) == 1
        assert root["children"][0]["span_id"] == child_span

    async def test_trace_decisions_aggregated(self, api):
        trace = f"trace-{_uid()}"
        events = [
            _event(event_id=f"evt_{_uid()}", trace_id=trace, decision="allow"),
            _event(event_id=f"evt_{_uid()}", trace_id=trace, decision="deny"),
        ]
        await _ingest(api, *events)

        r = await api.get(f"/audit/traces/{trace}", headers=ADMIN_HDR)
        data = r.json()
        assert data["decisions"].get("allow") == 1
        assert data["decisions"].get("deny") == 1

    async def test_trace_not_found(self, api):
        r = await api.get("/audit/traces/nonexistent-trace-xyz", headers=ADMIN_HDR)
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Plan
# ─────────────────────────────────────────────────────────────────────────────

class TestPlan:
    async def test_plan_summary(self, api):
        plan = f"plan-{_uid()}"
        events = [
            _event(event_id=f"evt_{_uid()}", plan_id=plan, task_id="t1",
                   decision="allow", caller_sub="user:alice",
                   caller_agent="doc_assistant", callee_agent="data_agent",
                   callee_action="feishu.bitable.read"),
            _event(event_id=f"evt_{_uid()}", plan_id=plan, task_id="t2",
                   decision="deny", caller_sub="user:alice",
                   caller_agent="doc_assistant", callee_agent="web_agent",
                   callee_action="web.search"),
        ]
        await _ingest(api, *events)

        r = await api.get(f"/audit/plans/{plan}", headers=ADMIN_HDR)
        assert r.status_code == 200
        data = r.json()
        assert data["plan_id"] == plan
        assert data["user"] == "user:alice"
        assert data["orchestrator"] == "doc_assistant"
        assert data["summary"]["total"] == 2
        assert data["summary"]["allow"] == 1
        assert data["summary"]["deny"] == 1
        assert len(data["tasks"]) == 2

    async def test_plan_not_found(self, api):
        r = await api.get("/audit/plans/nonexistent-plan-xyz", headers=ADMIN_HDR)
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────

class TestStats:
    async def test_stats_structure(self, api):
        await _ingest(api, event_type="authz_decision", decision="allow")
        await _ingest(api, event_type="token_issued")

        r = await api.get("/audit/stats?window=1h", headers=ADMIN_HDR)
        assert r.status_code == 200
        data = r.json()
        assert data["window"] == "1h"
        assert "total" in data
        assert "by_decision" in data
        assert "by_agent" in data
        assert "tokens_issued" in data
        assert "tokens_consumed" in data

    async def test_all_valid_windows(self, api):
        for window in ("1h", "24h", "7d"):
            r = await api.get(f"/audit/stats?window={window}", headers=ADMIN_HDR)
            assert r.status_code == 200, f"window={window} 失败"
            assert r.json()["window"] == window

    async def test_invalid_window_returns_400(self, api):
        r = await api.get("/audit/stats?window=3m", headers=ADMIN_HDR)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# Healthz
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthz:
    async def test_healthz_ok(self, api):
        r = await api.get("/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert "queue_depth" in data
        assert "sse_subscribers" in data


# ─────────────────────────────────────────────────────────────────────────────
# SSE
# ─────────────────────────────────────────────────────────────────────────────

class TestSSE:
    # NOTE: httpx.ASGITransport buffers the entire response body before returning
    # a Response object (see httpx/_transports/asgi.py: `await self.app(scope, receive, send)`).
    # Infinite SSE streams never complete, so these tests require a live uvicorn server.
    # Run with: LIVE_AUDIT_URL=http://localhost:8090 pytest -k sse

    @pytest.mark.skipif(
        not __import__("os").getenv("LIVE_AUDIT_URL"),
        reason="SSE tests require a live server: set LIVE_AUDIT_URL=http://localhost:8090",
    )
    async def test_sse_connected_event_received(self, api):
        """连接建立后应立即收到 event: connected。"""
        import httpx, os
        base = os.environ["LIVE_AUDIT_URL"]
        # Use live-server tokens if provided, otherwise fall back to ASGITransport test tokens
        svc_token = os.getenv("LIVE_SVC_TOKEN", SVC_HDR["Authorization"].split()[-1])
        live_svc_hdr = {"Authorization": f"Bearer {svc_token}"}
        async with httpx.AsyncClient(base_url=base) as client:
            lines: list[str] = []
            async with client.stream("GET", "/audit/stream", headers=live_svc_hdr) as r:
                assert r.status_code == 200
                async for line in r.aiter_lines():
                    if line:
                        lines.append(line)
                        break
            assert lines and "connected" in lines[0]

    @pytest.mark.skipif(
        not __import__("os").getenv("LIVE_AUDIT_URL"),
        reason="SSE tests require a live server: set LIVE_AUDIT_URL=http://localhost:8090",
    )
    async def test_sse_admin_token_accepted(self, api):
        import httpx, os
        base = os.environ["LIVE_AUDIT_URL"]
        admin_token = os.getenv("LIVE_ADMIN_TOKEN", ADMIN_HDR["Authorization"].split()[-1])
        live_admin_hdr = {"Authorization": f"Bearer {admin_token}"}
        async with httpx.AsyncClient(base_url=base) as client:
            async with client.stream("GET", "/audit/stream", headers=live_admin_hdr) as r:
                assert r.status_code == 200

    async def test_sse_no_token_rejected(self, api):
        """无 token 拒绝（不涉及流式读取，可用 ASGITransport 测试）。"""
        r = await api.get("/audit/stream")
        assert r.status_code == 401
