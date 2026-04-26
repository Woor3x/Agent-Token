# Audit API (审计日志服务) — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。独立只写+查询服务。接收 Gateway/IdP/Anomaly 三路事件，SQLite WAL 存储，REST 查询 + SSE 推送。事件 schema 包含 plan_id/task_id/one_time/consumed_at/policy_version。

## 1. 组件职责

- 接收异步批量写: Gateway (authz_decision / token_consumed), IdP (token_issued / revoke_issued / agent_registered), Anomaly (anomaly)
- SQLite WAL 持久化；索引 trace_id/plan_id/agent/timestamp/decision
- REST 查询: 分页 / 单条 / 按 trace / 按 plan / 统计
- SSE 实时流给 Anomaly Detector 订阅
- fail-safe: 写失败落 JSONL 备份 + 告警

## 2. 架构

```
┌──────────────────────────────────────────────────────────┐
│                        Audit API                         │
│                                                          │
│  Gateway ────▶ POST /audit/events ─┐                     │
│  IdP ────────▶ POST /audit/events ─┤                     │
│  Anomaly ────▶ POST /audit/events ─┤                     │
│                                    ▼                     │
│              ┌───────────────────────────┐               │
│              │ asyncio.Queue + Batcher    │               │
│              │ flush: 100ms / 50 events   │               │
│              └──────────┬────────────────┘               │
│                         ▼                                │
│              ┌───────────────────────────┐               │
│              │  SQLite (events)   WAL     │               │
│              │  indexes: trace/plan/agent │               │
│              └──────────┬────────────────┘               │
│                         │                                │
│  Web UI ◀── GET /audit/events (pagination)               │
│  Web UI ◀── GET /audit/traces/{trace}                    │
│  Web UI ◀── GET /audit/plans/{plan}                      │
│  Anomaly ◀─ GET /audit/stream (SSE)                      │
└──────────────────────────────────────────────────────────┘
```

## 3. 事件 Schema v2

所有事件公共字段:
```json
{
  "event_id":"evt_ulid",
  "timestamp":"2026-04-24T10:00:00.123Z",
  "event_type":"authz_decision | token_issued | token_consumed | revoke_issued | anomaly | agent_registered",
  "trace_id":"01HXYZ...",
  "span_id":"...",
  "parent_span_id":null,
  "plan_id":"plan_...",
  "task_id":"t1",
  "policy_version":"v1.2.0"
}
```

### 3.1 `authz_decision` (Gateway 写)

```json
{
  "event_id":"evt_...",
  "event_type":"authz_decision",
  "timestamp":"2026-04-24T10:00:00.123Z",
  "trace_id":"...","span_id":"...","parent_span_id":"...",
  "plan_id":"plan_...","task_id":"t1",
  "decision":"allow",
  "deny_reasons":[],
  "caller":{
    "agent_id":"doc_assistant",
    "sub":"user:alice",
    "token_jti":"tok-uuid",
    "delegation_chain":["user:alice","doc_assistant"],
    "dpop_jkt":"..."
  },
  "callee":{
    "agent_id":"data_agent",
    "action":"feishu.bitable.read",
    "resource":"app_token:bascn_alice/table:tbl_q1"
  },
  "intent":{
    "raw_prompt":"查询 Q1 销售",
    "parsed":{"action":"feishu.bitable.read","resource":"..."},
    "purpose":"generate_weekly_report"
  },
  "token":{
    "aud":"agent:data_agent",
    "scope":["feishu.bitable.read:app_token:.../table:tbl_q1"],
    "one_time":true,
    "exp":1714000120
  },
  "result":{"status":200,"bytes":4521},
  "latency_ms":143,
  "policy_version":"v1.2.0"
}
```

### 3.2 `token_issued` (IdP 写)

```json
{
  "event_type":"token_issued",
  "jti":"tok-uuid","sub":"user:alice",
  "actor":"doc_assistant",
  "audience":"agent:data_agent",
  "scope":["feishu.bitable.read:app_token:.../table:tbl_q1"],
  "purpose":"generate_weekly_report",
  "one_time":true,
  "exp":1714000120,"nbf":1714000000,
  "cnf_jkt":"...",
  "plan_id":"...","task_id":"...","trace_id":"...",
  "policy_version":"v1.2.0"
}
```

### 3.3 `token_consumed` (Gateway 写，One-Shot 销毁时)

```json
{
  "event_type":"token_consumed",
  "jti":"tok-uuid",
  "consumed_at":"2026-04-24T10:00:00.125Z",
  "consumed_by":"gateway-1",
  "trace_id":"...","plan_id":"...","task_id":"..."
}
```

### 3.4 `revoke_issued` (IdP 写)

```json
{
  "event_type":"revoke_issued",
  "revoke_type":"agent",
  "revoke_value":"web_agent",
  "reason":"anomaly:consecutive_deny",
  "ttl_sec":3600,
  "issued_by":"service:anomaly",
  "trigger_trace_id":"..."
}
```

### 3.5 `anomaly` (Anomaly 写)

见 `方案-Anomaly.md` §9。

### 3.6 `agent_registered` (IdP 写)

```json
{
  "event_type":"agent_registered",
  "agent_id":"data_agent",
  "role":"executor",
  "public_key_kid":"data_agent-2025-q1",
  "capabilities_hash":"sha256:..."
}
```

## 4. HTTP API

### 4.1 `POST /audit/events` (P0)

认证: service token (写端各自一把)。

Body:
```json
{ "events": [ { ...event... }, ... ] }
```

响应 200:
```json
{ "accepted":50, "failed":0 }
```

响应 207 (部分失败):
```json
{ "accepted":48, "failed":2, "errors":[{"event_id":"evt_x","reason":"schema"},...] }
```

### 4.2 `GET /audit/events` (P0, Web UI 分页)

认证: admin token。

Query:
| 参数 | 说明 |
|---|---|
| `event_type` | `authz_decision|token_issued|token_consumed|revoke_issued|anomaly|agent_registered` |
| `decision` | `allow|deny` |
| `deny_reason` | 单个 reason 字符串 |
| `caller_agent` | 调用方 agent_id |
| `callee_agent` | 被调方 agent_id |
| `sub` | user 或 agent sub |
| `trace_id` | trace 过滤 |
| `plan_id` | plan 过滤 |
| `from` / `to` | ISO 8601 时间窗 |
| `purpose` | purpose 关键词 |
| `limit` | ≤100, 默认 50 |
| `offset` | 分页 |

响应 200:
```json
{
  "total":142,
  "events":[ {...}, ... ],
  "next_offset":50
}
```

### 4.3 `GET /audit/events/{event_id}` (P0)

单条查询。响应: 单个事件 JSON。

### 4.4 `GET /audit/traces/{trace_id}` (P0)

按 trace 组装 span 树。

响应:
```json
{
  "trace_id":"01HXYZ...",
  "started_at":"2026-04-24T10:00:00Z",
  "ended_at":"2026-04-24T10:00:00.500Z",
  "total_spans":7,
  "decisions":{"allow":6,"deny":1},
  "spans":[
    {
      "span_id":"s1","parent_span_id":null,
      "caller":"user:alice","callee":"doc_assistant",
      "decision":"allow","latency_ms":500,
      "event_id":"evt_s1",
      "children":[
        {"span_id":"s2","caller":"doc_assistant","callee":"data_agent",
         "decision":"allow","latency_ms":143,"children":[]},
        {"span_id":"s3","caller":"doc_assistant","callee":"web_agent",
         "decision":"allow","latency_ms":2100,"children":[]}
      ]
    }
  ]
}
```

### 4.5 `GET /audit/plans/{plan_id}` (P1)

按 plan 查所有相关事件 (token_issued / authz_decision / token_consumed)。

响应:
```json
{
  "plan_id":"plan_...",
  "user":"user:alice",
  "orchestrator":"doc_assistant",
  "tasks":[
    { "task_id":"t1","agent":"data_agent","action":"feishu.bitable.read",
      "jti":"tok1","issued_at":"...","consumed_at":"...",
      "decision":"allow","latency_ms":143 },
    { "task_id":"t2","agent":"web_agent","action":"web.search",
      "jti":"tok2","issued_at":"...","consumed_at":"...",
      "decision":"allow","latency_ms":2100 }
  ],
  "summary":{"total":2,"allow":2,"deny":0}
}
```

### 4.6 `GET /audit/stream` (P0, SSE)

认证: service token。

Query: `?event_type=authz_decision&decision=deny` (可选过滤)

响应:
```
HTTP/1.1 200 OK
Content-Type: text/event-stream

event: connected
data: {"client_id":"..."}

event: audit_event
data: { ...event JSON... }

event: heartbeat
data: {"ts":"2026-04-24T10:00:05Z"}
```

Anomaly Detector 订阅此流，无轮询。

### 4.7 `GET /audit/stats` (P1)

Query: `?window=1h|24h|7d`

响应:
```json
{
  "window":"1h",
  "total":8432,
  "by_decision":{"allow":8300,"deny":132},
  "by_agent":{
    "doc_assistant":{"allow":4000,"deny":5},
  "data_agent":{"allow":3500,"deny":2},
  "web_agent":{"allow":800,"deny":125}
  },
  "by_reason":{
    "delegation_rejected":120,"scope_exceeded":10,"executor_mismatch":2
  },
  "tokens_issued":5000,
  "tokens_consumed":4998,
  "revoke_events":3,
  "anomaly_events":1
}
```

### 4.8 `GET /healthz` (P0)

```json
{ "status":"ok","db":"ok","queue_depth":12,"sse_subscribers":2 }
```

### 4.9 `GET /metrics` (P1)

Prometheus: `audit_events_written_total{type=}`, `audit_events_failed_total`, `audit_queue_depth`, `audit_write_latency_ms`, `audit_sse_subscribers`.

## 5. 数据库 Schema

```sql
CREATE TABLE events (
  event_id        TEXT PRIMARY KEY,
  timestamp       TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  trace_id        TEXT,
  span_id         TEXT,
  parent_span_id  TEXT,
  plan_id         TEXT,
  task_id         TEXT,

  decision        TEXT,                    -- allow/deny/null
  deny_reasons    TEXT,                    -- JSON array
  caller_agent    TEXT,
  caller_sub      TEXT,
  caller_jti      TEXT,
  delegation_chain TEXT,                   -- JSON
  dpop_jkt        TEXT,

  callee_agent    TEXT,
  callee_action   TEXT,
  callee_resource TEXT,

  raw_prompt      TEXT,
  purpose         TEXT,

  token_aud       TEXT,
  token_scope     TEXT,                    -- JSON
  token_one_time  INTEGER,                 -- 0/1
  token_exp       INTEGER,
  consumed_at     TEXT,
  consumed_by     TEXT,

  revoke_type     TEXT,
  revoke_value    TEXT,
  revoke_reason   TEXT,

  anomaly_rule    TEXT,
  severity        TEXT,

  result_status   INTEGER,
  result_bytes    INTEGER,
  latency_ms      INTEGER,
  policy_version  TEXT,

  extra           TEXT                     -- JSON 扩展
);

CREATE INDEX idx_timestamp     ON events(timestamp);
CREATE INDEX idx_trace_id      ON events(trace_id);
CREATE INDEX idx_plan_id       ON events(plan_id);
CREATE INDEX idx_caller_agent  ON events(caller_agent);
CREATE INDEX idx_callee_agent  ON events(callee_agent);
CREATE INDEX idx_event_type    ON events(event_type);
CREATE INDEX idx_decision      ON events(decision);
CREATE INDEX idx_trace_ts      ON events(trace_id, timestamp);
CREATE INDEX idx_plan_ts       ON events(plan_id, timestamp);
CREATE INDEX idx_jti_consumed  ON events(caller_jti, consumed_at);
```

WAL + NORMAL 提升并发:
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA cache_size=-64000")     # 64MB
conn.execute("PRAGMA mmap_size=268435456")   # 256MB
```

## 6. 批量写入实现

```python
# services/audit-api/writer.py
class BatchWriter:
    def __init__(self, db, batch_size=50, flush_interval=0.1):
        self.queue = asyncio.Queue(maxsize=10000)
        self.db = db; self.batch_size = batch_size; self.flush_interval = flush_interval

    async def enqueue(self, events):
        for e in events:
            try: self.queue.put_nowait(e)
            except asyncio.QueueFull:
                _backup_to_file([e])     # 溢出直备份
                metrics.inc("audit_events_failed_total", 1)

    async def run(self):
        buf, last = [], time.monotonic()
        while True:
            try:
                e = await asyncio.wait_for(self.queue.get(),
                                           timeout=max(0.001, self.flush_interval - (time.monotonic()-last)))
                buf.append(e)
            except asyncio.TimeoutError:
                pass
            if len(buf) >= self.batch_size or (buf and time.monotonic()-last >= self.flush_interval):
                await self._flush(buf); buf = []; last = time.monotonic()

    async def _flush(self, events):
        try:
            await self.db.executemany(INSERT_SQL, [_row(e) for e in events])
            await self.db.commit()
            await sse_broadcaster.broadcast(events)
            metrics.inc("audit_events_written_total", len(events))
        except Exception as exc:
            _backup_to_file(events)
            logger.error("audit flush failed: %s", exc)
            metrics.inc("audit_events_failed_total", len(events))
```

## 7. SSE 广播

```python
# services/audit-api/sse.py
class SSEBroadcaster:
    def __init__(self): self._subs: list[asyncio.Queue] = []
    def subscribe(self, filter_fn):
        q = asyncio.Queue(maxsize=1000); self._subs.append((q, filter_fn)); return q
    def unsubscribe(self, q):
        self._subs = [(x,f) for (x,f) in self._subs if x is not q]
    async def broadcast(self, events):
        for q, f in self._subs:
            for e in events:
                if f(e):
                    try: q.put_nowait(e)
                    except asyncio.QueueFull: pass      # 慢消费者丢弃

async def stream(req):
    filter_fn = make_filter(req.query_params)
    q = sse_broadcaster.subscribe(filter_fn)
    try:
        yield "event: connected\ndata: {}\n\n"
        while True:
            try:
                e = await asyncio.wait_for(q.get(), timeout=30)
                yield f"event: audit_event\ndata: {json.dumps(e)}\n\n"
            except asyncio.TimeoutError:
                yield f"event: heartbeat\ndata: {{\"ts\":\"{utcnow_iso()}\"}}\n\n"
    finally:
        sse_broadcaster.unsubscribe(q)
```

## 8. fail-safe 备份

写入失败 → 落 JSONL:
```
services/audit-api/backup/
├── audit_backup_2026-04-24T10-00.jsonl
└── audit_backup_2026-04-24T11-00.jsonl
```

运维脚本 `replay_backup.py` 可补录。

## 9. 模块文件映射

```
services/audit-api/
├── main.py              # FastAPI app + 路由
├── config.py            # DB_PATH, SERVICE_TOKENS, admin_token
├── auth.py              # Bearer 校验 (service / admin 两类)
├── writer.py            # BatchWriter
├── sse.py               # SSEBroadcaster
├── db.py                # SQLite 连接 + Schema 初始化 + WAL
├── queries.py           # 分页 / trace / plan / stats
├── models.py            # Pydantic 事件 Schema (6 类型)
├── filters.py           # SSE / 查询 filter
└── backup/              # JSONL 备份
```

## 10. 性能目标

| 指标 | 目标 |
|---|---|
| 批量写吞吐 | ≥ 5000 events/s |
| 队列背压阈值 | 10000, 溢出直备份 |
| 单条查询 p99 | < 20ms |
| trace 树查询 (7 span) p99 | < 50ms |
| SSE 广播延迟 | < 200ms |
| DB 文件 (100w events) | ~500MB WAL |
| 索引重建 (停机) | < 30s |

## 11. 契约

| 调用方 → Audit | 认证 |
|---|---|
| Gateway `/audit/events` | gateway service token |
| IdP `/audit/events` | idp service token |
| Anomaly `/audit/events` | anomaly service token |
| Anomaly `/audit/stream` (SSE) | anomaly service token |
| Web UI `/audit/events`, `/traces`, `/plans`, `/stats` | admin token |
| Prometheus `/metrics` | 内网 |

| Audit → 外部 | 说明 |
|---|---|
| (无) | Audit 不主动出站 |

## 12. 保留策略 (演示简化)

- 默认保留 30 天
- 超期事件归档至 `archive/events_YYYY-MM.jsonl.gz`
- Anomaly / revoke_issued 事件永久保留
