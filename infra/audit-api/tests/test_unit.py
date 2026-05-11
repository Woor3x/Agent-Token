"""
tests/test_unit.py — Audit API 纯逻辑单元测试

覆盖：
  * main._normalise            — event 规范化（补 event_id / timestamp / deny_reasons）
  * filters.build_where_clause — SQL WHERE 子句构建
  * filters.build_sse_filter   — SSE 订阅过滤谓词
  * writer._json_or_none       — JSON 序列化辅助
  * writer._to_row             — event dict → DB 行参数
  * queries._row_to_dict       — DB 行 → response dict（JSON 字段反序列化）
"""
import json


# ─────────────────────────────────────────────────────────────────────────────
# _normalise
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalise:
    def _fn(self):
        from main import _normalise
        return _normalise

    def test_auto_fills_event_id(self):
        out = self._fn()({"event_type": "authz_decision"})
        assert out["event_id"].startswith("evt_")

    def test_preserves_existing_event_id(self):
        out = self._fn()({"event_type": "authz_decision", "event_id": "my-id"})
        assert out["event_id"] == "my-id"

    def test_auto_fills_timestamp(self):
        out = self._fn()({"event_type": "authz_decision"})
        assert "T" in out["timestamp"] and out["timestamp"].endswith("Z")

    def test_preserves_existing_timestamp(self):
        ts = "2025-01-01T00:00:00.000Z"
        out = self._fn()({"event_type": "authz_decision", "timestamp": ts})
        assert out["timestamp"] == ts

    def test_none_deny_reasons_becomes_empty_list(self):
        out = self._fn()({"event_type": "authz_decision"})
        assert out["deny_reasons"] == []

    def test_string_deny_reasons_parsed_as_json(self):
        out = self._fn()({"event_type": "authz_decision", "deny_reasons": '["a","b"]'})
        assert out["deny_reasons"] == ["a", "b"]

    def test_invalid_json_deny_reasons_wrapped_in_list(self):
        out = self._fn()({"event_type": "authz_decision", "deny_reasons": "not-json"})
        assert out["deny_reasons"] == ["not-json"]

    def test_list_deny_reasons_preserved(self):
        out = self._fn()({"event_type": "authz_decision", "deny_reasons": ["x", "y"]})
        assert out["deny_reasons"] == ["x", "y"]


# ─────────────────────────────────────────────────────────────────────────────
# build_where_clause
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildWhereClause:
    def _fn(self, params):
        from filters import build_where_clause
        return build_where_clause(params)

    def test_empty_params_returns_empty(self):
        sql, args = self._fn({})
        assert sql == "" and args == []

    def test_event_type_filter(self):
        sql, args = self._fn({"event_type": "authz_decision"})
        assert sql.startswith("WHERE")
        assert "event_type = ?" in sql
        assert args == ["authz_decision"]

    def test_decision_filter(self):
        sql, args = self._fn({"decision": "deny"})
        assert "decision = ?" in sql and "deny" in args

    def test_deny_reason_uses_like(self):
        sql, args = self._fn({"deny_reason": "scope"})
        assert "deny_reasons LIKE ?" in sql and "%scope%" in args

    def test_sub_maps_to_caller_sub(self):
        sql, args = self._fn({"sub": "user:alice"})
        assert "caller_sub = ?" in sql and "user:alice" in args

    def test_time_range_filter(self):
        sql, args = self._fn({"from": "2025-01-01T00:00:00Z", "to": "2025-12-31T00:00:00Z"})
        assert "timestamp >= ?" in sql and "timestamp <= ?" in sql
        assert len(args) == 2

    def test_multiple_filters_joined_with_and(self):
        sql, args = self._fn({"event_type": "token_issued", "decision": "allow"})
        assert "AND" in sql and len(args) == 2

    def test_trace_id_filter(self):
        sql, args = self._fn({"trace_id": "t-123"})
        assert "trace_id = ?" in sql

    def test_plan_id_filter(self):
        sql, args = self._fn({"plan_id": "p-abc"})
        assert "plan_id = ?" in sql

    def test_caller_agent_filter(self):
        sql, args = self._fn({"caller_agent": "doc_assistant"})
        assert "caller_agent = ?" in sql

    def test_callee_agent_filter(self):
        sql, args = self._fn({"callee_agent": "data_agent"})
        assert "callee_agent = ?" in sql

    def test_purpose_uses_like(self):
        sql, args = self._fn({"purpose": "search"})
        assert "purpose LIKE ?" in sql and "%search%" in args


# ─────────────────────────────────────────────────────────────────────────────
# build_sse_filter
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildSseFilter:
    def _fn(self, params):
        from filters import build_sse_filter
        return build_sse_filter(params)

    def test_no_params_accepts_all(self):
        fn = self._fn({})
        assert fn({}) is True
        assert fn({"event_type": "anything", "decision": "allow"}) is True

    def test_event_type_match(self):
        fn = self._fn({"event_type": "token_issued"})
        assert fn({"event_type": "token_issued"}) is True
        assert fn({"event_type": "authz_decision"}) is False

    def test_decision_match(self):
        fn = self._fn({"decision": "deny"})
        assert fn({"decision": "deny"}) is True
        assert fn({"decision": "allow"}) is False

    def test_caller_agent_match(self):
        fn = self._fn({"caller_agent": "doc_assistant"})
        assert fn({"caller_agent": "doc_assistant"}) is True
        assert fn({"caller_agent": "data_agent"}) is False

    def test_callee_agent_match(self):
        fn = self._fn({"callee_agent": "data_agent"})
        assert fn({"callee_agent": "data_agent"}) is True
        assert fn({"callee_agent": "web_agent"}) is False

    def test_all_filters_must_match(self):
        fn = self._fn({"event_type": "authz_decision", "decision": "allow"})
        assert fn({"event_type": "authz_decision", "decision": "allow"}) is True
        assert fn({"event_type": "authz_decision", "decision": "deny"}) is False
        assert fn({"event_type": "token_issued", "decision": "allow"}) is False


# ─────────────────────────────────────────────────────────────────────────────
# writer._json_or_none / _to_row
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonOrNone:
    def _fn(self, v):
        from writer import _json_or_none
        return _json_or_none(v)

    def test_none_returns_none(self):
        assert self._fn(None) is None

    def test_string_returned_as_is(self):
        assert self._fn("hello") == "hello"

    def test_list_serialized(self):
        assert json.loads(self._fn(["a", "b"])) == ["a", "b"]

    def test_dict_serialized(self):
        assert json.loads(self._fn({"k": "v"})) == {"k": "v"}

    def test_empty_list_serialized(self):
        assert self._fn([]) == "[]"


class TestToRow:
    def _base(self, **kw):
        base = {
            "event_id": "evt_test",
            "timestamp": "2025-01-01T00:00:00.000Z",
            "event_type": "authz_decision",
            "decision": "allow",
            "deny_reasons": [],
        }
        base.update(kw)
        return base

    def _row(self, **kw):
        from writer import _to_row
        return _to_row(self._base(**kw))

    def test_scalar_fields_pass_through(self):
        row = self._row()
        assert row["event_id"] == "evt_test"
        assert row["event_type"] == "authz_decision"
        assert row["decision"] == "allow"

    def test_deny_reasons_list_serialized(self):
        row = self._row(deny_reasons=["a", "b"])
        assert json.loads(row["deny_reasons"]) == ["a", "b"]

    def test_empty_deny_reasons_serialized(self):
        assert self._row()["deny_reasons"] == "[]"

    def test_token_one_time_true_becomes_1(self):
        assert self._row(token_one_time=True)["token_one_time"] == 1

    def test_token_one_time_false_becomes_0(self):
        assert self._row(token_one_time=False)["token_one_time"] == 0

    def test_token_one_time_none_stays_none(self):
        assert self._row()["token_one_time"] is None

    def test_extra_dict_serialized(self):
        row = self._row(extra={"foo": "bar"})
        assert json.loads(row["extra"]) == {"foo": "bar"}

    def test_optional_absent_fields_are_none(self):
        row = self._row()
        assert row["trace_id"] is None
        assert row["plan_id"] is None
        assert row["caller_agent"] is None


# ─────────────────────────────────────────────────────────────────────────────
# queries._row_to_dict
# ─────────────────────────────────────────────────────────────────────────────

class TestRowToDict:
    def _base(self, **kw):
        base = {
            "event_id": "evt_001",
            "timestamp": "2025-01-01T00:00:00.000Z",
            "event_type": "authz_decision",
            "deny_reasons": None,
            "delegation_chain": None,
            "token_scope": None,
            "extra": None,
        }
        base.update(kw)
        return base

    def _convert(self, **kw):
        from queries import _row_to_dict
        return _row_to_dict(self._base(**kw))

    def test_non_json_fields_pass_through(self):
        d = self._convert()
        assert d["event_id"] == "evt_001"
        assert d["event_type"] == "authz_decision"

    def test_deny_reasons_json_deserialized(self):
        d = self._convert(deny_reasons='["a","b"]')
        assert d["deny_reasons"] == ["a", "b"]

    def test_token_scope_json_deserialized(self):
        d = self._convert(token_scope='["read","write"]')
        assert d["token_scope"] == ["read", "write"]

    def test_extra_json_deserialized(self):
        d = self._convert(extra='{"key":"val"}')
        assert d["extra"] == {"key": "val"}

    def test_delegation_chain_json_deserialized(self):
        chain = [{"sub": "agent:a"}, {"sub": "agent:b"}]
        d = self._convert(delegation_chain=json.dumps(chain))
        assert d["delegation_chain"] == chain

    def test_invalid_json_left_as_string(self):
        d = self._convert(deny_reasons="not-json")
        assert d["deny_reasons"] == "not-json"

    def test_none_json_fields_stay_none(self):
        d = self._convert()
        assert d["deny_reasons"] is None
        assert d["extra"] is None
