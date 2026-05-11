# Audit API

集中式审计事件存储服务。接收来自 Gateway、IdP、异常检测模块的结构化事件，提供查询、聚合、SSE 实时推送接口，供 Web UI 管理面板和告警系统消费。

---

## 主要功能

- **批量写入**：BatchWriter 缓冲事件，每 100ms 或积累 50 条时批量落库（SQLite），削减写压力
- **多维查询**：按 event_type / trace_id / plan_id / decision / caller_agent / callee_agent / 时间范围过滤
- **Trace 树组装**：`/audit/traces/{trace_id}` 按 parent_span_id 重建 span 树
- **Plan 视图**：`/audit/plans/{plan_id}` 聚合一次编排调用的全部任务事件
- **SSE 实时流**：`/audit/stream` 订阅实时事件推送，支持按 event_type / decision / caller_agent / callee_agent 过滤
- **统计聚合**：`/audit/stats` 提供 1h / 24h / 7d 三个时间窗口的请求量、决策分布、token 计数
- **Prometheus 指标**：`/metrics` 暴露队列深度、SSE 订阅数等 Gauge

---

## 接口信息

### 认证方式

所有接口通过 `Authorization: Bearer <token>` 头认证：

| token 类型 | 来源 | 可访问接口 |
|-----------|------|-----------|
| service token | `AUDIT_SERVICE_TOKENS` 逗号列表 | `POST /audit/events`、`GET /audit/stream` |
| admin token | `AUDIT_ADMIN_TOKEN` | 所有读接口（`GET /audit/*`）、`GET /audit/stream` |

---

### POST /audit/events

批量写入审计事件（service token）。

**Request body**
```json
{
  "events": [
    {
      "event_type": "authz_decision",
      "trace_id": "01KRB5...",
      "plan_id": "plan_abc",
      "decision": "allow",
      "caller_agent": "doc_assistant",
      "callee_agent": "data_agent",
      "action": "feishu.bitable.read",
      "resource": "app_token:foo/table:bar"
    }
  ]
}
```

合法 `event_type` 枚举：`authz_decision` / `token_issued` / `token_consumed` / `revoke_issued` / `anomaly` / `agent_registered` / `key_rotated` / `plan_validated`

`event_id` 和 `timestamp` 字段如缺失将自动填充。

**Response 200** — 全部成功
```json
{ "accepted": 3, "failed": 0, "errors": [] }
```

**Response 207** — 部分失败
```json
{
  "accepted": 2,
  "failed": 1,
  "errors": [{ "event_id": "evt_...", "reason": "unknown event_type: bad_type" }]
}
```

---

### GET /audit/events

分页列表查询（admin token）。

**Query params**

| 参数 | 说明 | 示例 |
|------|------|------|
| `limit` | 每页条数，最大 100，默认 50 | `limit=20` |
| `offset` | 分页偏移 | `offset=40` |
| `event_type` | 精确匹配 | `event_type=authz_decision` |
| `decision` | `allow` / `deny` | `decision=deny` |
| `trace_id` | 精确匹配 | `trace_id=01KRB5...` |
| `plan_id` | 精确匹配 | `plan_id=plan_abc` |
| `caller_agent` | 精确匹配 | `caller_agent=doc_assistant` |
| `callee_agent` | 精确匹配 | `callee_agent=data_agent` |
| `deny_reason` | LIKE 匹配 | `deny_reason=scope` |
| `purpose` | LIKE 匹配 | |
| `since` / `until` | ISO8601 时间范围 | `since=2026-05-01T00:00:00Z` |

**Response 200**
```json
{
  "total": 184,
  "events": [ { "event_id": "evt_...", "timestamp": "2026-05-11T...", ... } ],
  "next_offset": 50
}
```

---

### GET /audit/events/{event_id}

单条事件查询（admin token）。返回事件完整字段，未找到返回 404。

---

### GET /audit/traces/{trace_id}

返回 trace 下所有 span 组成的树（admin token）。

**Response 200**
```json
{
  "trace_id": "01KRB5...",
  "spans": [
    {
      "span_id": "abc123",
      "parent_span_id": null,
      "event_type": "authz_decision",
      "decision": "allow",
      "children": [ { "span_id": "def456", ... } ]
    }
  ],
  "decisions": { "allow": 3, "deny": 1 }
}
```

未找到返回 404。

---

### GET /audit/plans/{plan_id}

聚合一次编排调用的全部事件（admin token）。

**Response 200**
```json
{
  "plan_id": "plan_abc",
  "user": "user:alice",
  "orchestrator": "doc_assistant",
  "tasks": [
    { "task_id": "t1", "callee_agent": "data_agent", "decision": "allow" }
  ],
  "summary": { "total": 4, "allow": 3, "deny": 1 }
}
```

---

### GET /audit/stats?window=1h

时间窗口聚合统计（admin token）。`window` 取值：`1h` / `24h` / `7d`，非法值返回 400。

**Response 200**
```json
{
  "period": "1h",
  "requests": 42,
  "authz_decisions": { "allow": 38, "deny": 4 },
  "tokens_issued": 15,
  "tokens_consumed": 15,
  "anomalies": 0
}
```

---

### GET /audit/stream

SSE 实时事件流（service token 或 admin token）。

连接建立后立即发送 `connected` 事件，随后推送实时 `audit_event`，无事件时每 `AUDIT_SSE_HEARTBEAT_SEC` 秒发送一次 `heartbeat`。

**Query params**（可选过滤，与 GET /audit/events 相同字段子集）：`event_type` / `decision` / `caller_agent` / `callee_agent`

**SSE 消息格式**
```
event: connected
data: {"client_id":"9171a9b3"}

event: audit_event
data: {"event_id":"evt_...","event_type":"authz_decision","decision":"allow",...}

event: heartbeat
data: {"ts":"2026-05-11T11:26:42.656Z"}
```

> **注意**：`httpx.ASGITransport` 不支持测试无限流 SSE，测试需真实 uvicorn 服务器：
> ```bash
> LIVE_AUDIT_URL=http://localhost:8090 \
> LIVE_SVC_TOKEN=<service-token> \
> python -m pytest infra/audit-api/tests/ -k sse
> ```

---

### GET /healthz

无需认证。

**Response 200**
```json
{
  "status": "ok",
  "db": "ok",
  "queue_depth": 0,
  "sse_subscribers": 2
}
```

`status` 为 `degraded` 表示 DB 不可达。

---

### GET /metrics

无需认证。返回 Prometheus text format。建议仅在内网暴露。

---

## 错误码与 Error Body

所有错误响应统一格式：

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid token"
  }
}
```

| HTTP | code | 触发场景 |
|------|------|----------|
| 400 | `VALIDATION_ERROR` | `window` 参数非法等 |
| 401 | `UNAUTHORIZED` | 无 Authorization 头、token 不在配置列表中 |
| 403 | `FORBIDDEN` | service token 调用只允许 admin 的接口（或反之） |
| 404 | `NOT_FOUND` | event_id / trace_id / plan_id 不存在 |
| 500 | `SERVER_ERROR` | 未预期异常 |

> `POST /audit/events` 的部分失败通过 HTTP 207 + `errors` 数组返回，不使用上述格式。

---

## 环境变量

前缀 `AUDIT_`，所有字段均有默认值。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AUDIT_HOST` | `0.0.0.0` | 监听地址 |
| `AUDIT_PORT` | `8090` | 监听端口 |
| `AUDIT_LOG_LEVEL` | `info` | 日志级别：debug / info / warning / error |
| `AUDIT_DB_PATH` | `./audit.db` | SQLite 文件路径 |
| `AUDIT_ADMIN_TOKEN` | `change-me-in-production` | 读接口 Bearer token，生产环境必须替换 |
| `AUDIT_SERVICE_TOKENS` | `""` | 逗号分隔的写接口 token 列表，例：`gateway-service-token,idp-service-token` |
| `AUDIT_FLUSH_INTERVAL_MS` | `100` | 批量写入间隔（毫秒） |
| `AUDIT_BATCH_SIZE` | `50` | 批量写入条数阈值 |
| `AUDIT_SSE_HEARTBEAT_SEC` | `30` | SSE 无事件时心跳间隔（秒） |
| `AUDIT_BACKUP_DIR` | `./backup` | BatchWriter 写入失败时的 JSONL 备份目录 |
