# 测试覆盖率报告

## 总览

| 服务 | 测试文件 | 测试数 | 通过 | 跳过 |
|------|----------|--------|------|------|
| audit-api | test_unit.py | 46 | ✅ 46 | 0 |
| audit-api | test_api.py | 33 | ✅ 33 | 0 |
| **合计**  | **2 个文件** | **79** | **79** | 0    |

---

## audit-api — `infra/audit-api/tests/`

### test_unit.py — 46/46 ✅

纯逻辑单元测试，不依赖 HTTP 栈，直接调用内部函数。

| 测试类 | 用例数 | 覆盖功能 |
|--------|--------|----------|
| `TestNormalise` | 8 | `event_id` / `timestamp` 自动填充；`deny_reasons` 的 None / 字符串 / 无效 JSON / 列表四种形态归一化 |
| `TestBuildWhereClause` | 12 | SQL 过滤构建：空参数、event_type、decision、deny_reason LIKE、sub→caller_sub 映射、时间范围、多条件 AND 拼接、trace_id / plan_id / caller_agent / callee_agent / purpose LIKE |
| `TestBuildSseFilter` | 6 | SSE 过滤函数构建：无参数全通、event_type / decision / caller_agent / callee_agent 单条匹配、多条件 AND |
| `TestJsonOrNone` | 5 | None 透传；字符串原样返回；list / dict / 空列表 JSON 序列化 |
| `TestToRow` | 8 | 标量字段直通；deny_reasons 序列化；token_one_time 布尔→整数；extra dict 序列化；可选字段缺失→None |
| `TestRowToDict` | 7 | 非 JSON 字段直通；deny_reasons / token_scope / extra / delegation_chain JSON 反序列化；无效 JSON 保持字符串；None 字段保持 None |

---

### test_api.py — 33/33 ✅

全链路集成测试，httpx.AsyncClient + ASGITransport，SQLite :memory:，batch_size=1 保证即时落库。

#### TestAuth — 7/7 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_post_no_auth_header` | 无 Authorization 头 → 401 |
| `test_post_wrong_service_token` | 错误 service token → 401 |
| `test_get_no_auth_header` | GET 无 Authorization → 401 |
| `test_get_wrong_admin_token` | 错误 admin token → 401 |
| `test_service_token_cannot_read` | service token 不能读事件 → 403 |
| `test_admin_token_can_read` | admin token 可读 → 200 |
| `test_admin_token_cannot_post` | admin token 不能写 → 403 |

#### TestIngest — 6/6 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_single_event_accepted` | 单条事件 → 200, accepted=1 |
| `test_batch_events_accepted` | 批量 3 条 → 200, accepted=3 |
| `test_unknown_event_type_returns_207` | 未知 event_type → 207, failed=1 |
| `test_mixed_batch_returns_207` | 混合批次（1好1坏）→ 207, accepted=1 failed=1 |
| `test_event_id_auto_generated` | 缺 event_id 时自动生成 |
| `test_all_valid_event_types_accepted` | 全部合法 event_type 均被接受 |

#### TestEventQuery — 8/8 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_list_returns_written_event` | 写入后可列表查到 |
| `test_filter_by_event_type` | ?event_type= 过滤生效 |
| `test_filter_by_trace_id` | ?trace_id= 过滤生效 |
| `test_filter_by_decision` | ?decision= 过滤生效 |
| `test_get_event_by_id` | GET /audit/events/{id} 返回正确记录 |
| `test_get_event_not_found` | 未知 id → 404 |
| `test_pagination_limit_respected` | limit 参数分页生效 |
| `test_limit_capped_at_100` | limit > 100 自动截断到 100 |

#### TestTrace — 3/3 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_trace_span_tree` | parent_span_id → children 树组装正确 |
| `test_trace_decisions_aggregated` | decision 汇总字段正确 |
| `test_trace_not_found` | 未知 trace_id → 404 |

#### TestPlan — 2/2 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_plan_summary` | plan 聚合：user / orchestrator / tasks / summary 字段存在且正确 |
| `test_plan_not_found` | 未知 plan_id → 404 |

#### TestStats — 3/3 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_stats_structure` | 响应包含 period / requests / authz_decisions / tokens_issued 等字段 |
| `test_all_valid_windows` | 1h / 24h / 7d 三个时间窗口均返回 200 |
| `test_invalid_window_returns_400` | 非法 window 参数 → 400 |

#### TestHealthz — 1/1 ✅

| 测试用例 | 验证行为 |
|----------|----------|
| `test_healthz_ok` | DB 在线时 /healthz 返回 200 + status=ok |

#### TestSSE — 3/3 ✅

| 测试用例 | 状态 | 验证行为 |
|----------|------|----------|
| `test_sse_no_token_rejected` | ✅ | 无 token → 401（非流式响应，ASGITransport 可测） |
| `test_sse_connected_event_received` | ✅ | 用合法 token 连接 SSE 端点，服务端返回 200 |
| `test_sse_admin_token_accepted` | ✅    | 用合法 token 连接 SSE 端点，服务端返回 200 |
