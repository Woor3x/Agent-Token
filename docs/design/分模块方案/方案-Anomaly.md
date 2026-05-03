# Anomaly Detector (异常检测器) — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。5 条规则 + 调 IdP `/revoke` (6 粒度) + 写回 Audit。订阅 Audit API SSE，Redis ZSET 滑窗，命中即撤销 + 广播。

## 1. 组件职责

- 订阅 Audit SSE `/audit/stream`
- 5 条规则滑窗统计
- 命中规则 → IdP `/revoke` (选择合适粒度: jti/sub/agent/trace/plan/chain)
- 写 `event_type=anomaly` 事件回 Audit
- 对外暴露规则配置 / 告警查询 API

## 2. 架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Anomaly Detector                          │
│                                                              │
│  Audit API SSE ──▶ Event Consumer                            │
│  (GET /audit/stream)       │                                 │
│                            ▼                                 │
│                    ┌───────────────┐                         │
│                    │  Rule Engine  │                         │
│                    │               │                         │
│                    │  consecutive_deny                       │
│                    │  rate_spike                             │
│                    │  resource_drift                         │
│                    │  trace_loop                             │
│                    │  capability_probe                       │
│                    └───────┬───────┘                         │
│             Redis ZSET 滑窗│                                 │
│                            ▼                                 │
│                    ┌───────────────┐                         │
│                    │ Action Engine │                         │
│                    │ POST /revoke  │                         │
│                    │ POST /audit   │                         │
│                    └───────────────┘                         │
└──────────────┬──────────────────────┬────────────────────────┘
               ▼                      ▼
      IdP /revoke (6 粒度)    Audit API /audit/events
      → Pub/Sub 广播          → event_type=anomaly
      → 所有 Gateway 热更新
```

## 3. 规则表

| 规则 | 触发条件 | 撤销粒度 | 默认参数 |
|---|---|---|---|
| `consecutive_deny` | 同 sub/agent 连续 N 次 deny | `sub` (若 agent 则 `agent`) | N=5, 窗口=60s |
| `rate_spike` | 单 agent 窗口内调用数爆涨 | 仅告警 | 100/10s |
| `resource_drift` | purpose 绑定的 resource prefix 漂移 | `jti` | TTL=300s |
| `trace_loop` | 同 trace 内某 agent 出现次数超 max_depth | `trace` | max_depth=3 |
| `capability_probe` | 越界 deny (executor_mismatch/delegation_rejected/scope_exceeded) 超 N 次 | `agent` | N=3/300s |

## 4. HTTP API

所有端点认证: admin token (内网 service token)。

### 4.1 `GET /healthz` (P0)

```json
{ "status":"ok","rules_loaded":5,"redis":"ok","sse":"connected" }
```

### 4.2 `GET /anomaly/rules` (P1)

列出当前规则配置。

```json
{
  "rules": {
    "consecutive_deny":{"enabled":true,"n":5,"window_sec":60},
    "rate_spike":{"enabled":true,"limit":100,"window_sec":10},
    "resource_drift":{"enabled":true,"ttl_sec":300},
    "trace_loop":{"enabled":true,"max_depth":3},
    "capability_probe":{"enabled":true,"n":3,"window_sec":300}
  },
  "version": "v1.2.0"
}
```

### 4.3 `POST /anomaly/admin/reload` (P1)

重载 `rules_config.yaml`。

```json
{ "reloaded":true, "rules_count":5 }
```

### 4.4 `GET /anomaly/alerts` (P2)

查询近期告警 (从 Audit API 代理)。

Query: `?since=2026-04-24T00:00:00Z&rule=consecutive_deny&limit=50`

```json
{
  "alerts":[
    { "event_id":"evt_...","rule":"consecutive_deny","severity":"critical",
      "revoke_type":"agent","revoke_value":"web_agent",
      "timestamp":"2026-04-24T10:00:12Z","trace_id":"..." }
  ]
}
```

### 4.5 `GET /metrics` (P1)

Prometheus: `anomaly_events_consumed_total`, `anomaly_rules_fired_total{rule=}`, `anomaly_revoke_issued_total{type=}`, `anomaly_sse_reconnect_total`.

## 5. 规则实现

### 5.1 `rules/consecutive_deny.py`

```python
# 同 agent_id 在 window_sec 内连续 N 次 deny (allow 重置)
class ConsecutiveDenyRule:
    KEY    = "anomaly:cdeny:{agent}"
    N      = 5
    WINDOW = 60

    async def process(self, event, redis):
        agent = event.get("caller_agent")
        if not agent: return None
        if event.get("decision") == "allow":
            await redis.delete(self.KEY.format(agent=agent))
            return None
        if event.get("decision") != "deny":
            return None
        key = self.KEY.format(agent=agent)
        ts  = time.time()
        await redis.zadd(key, {event["event_id"]: ts})
        await redis.zremrangebyscore(key, 0, ts - self.WINDOW)
        await redis.expire(key, self.WINDOW * 2)
        if await redis.zcard(key) >= self.N:
            await redis.delete(key)
            return RuleResult(rule="consecutive_deny",
                              revoke_type="agent", revoke_value=agent,
                              severity="critical",
                              evidence={"count":self.N,"window_sec":self.WINDOW})
        return None
```

### 5.2 `rules/resource_drift.py`

```python
# token purpose 绑 jti 的首次 resource prefix，后续漂移即告警 (防 Prompt Injection 改目标)
class ResourceDriftRule:
    KEY = "anomaly:rdrift:{jti}"
    TTL = 300   # 覆盖 token 最长 TTL

    async def process(self, event, redis):
        if event.get("decision") != "allow": return None
        jti = event.get("caller_jti")
        res = event.get("callee_resource")
        if not jti or not res: return None
        prefix = res.split("/")[0]   # "app_token:bascn_alice"
        key = self.KEY.format(jti=jti)
        expected = await redis.get(key)
        if expected is None:
            await redis.set(key, prefix, ex=self.TTL)
            return None
        if expected.decode() != prefix:
            return RuleResult(rule="resource_drift",
                              revoke_type="jti", revoke_value=jti,
                              severity="critical",
                              evidence={"expected":expected.decode(),"actual":prefix})
        return None
```

### 5.3 `rules/trace_loop.py`

```python
# 同 trace 内某 agent 被调次数超 max_depth → 撤销整个 trace
class TraceLoopRule:
    KEY       = "anomaly:tloop:{trace}"
    MAX_DEPTH = 3

    async def process(self, event, redis):
        trace = event.get("trace_id")
        agent = event.get("callee_agent")
        if not trace or not agent: return None
        key = self.KEY.format(trace=trace)
        c = await redis.hincrby(key, agent, 1)
        await redis.expire(key, 3600)
        if c > self.MAX_DEPTH:
            return RuleResult(rule="trace_loop",
                              revoke_type="trace", revoke_value=trace,
                              severity="critical",
                              evidence={"agent":agent,"count":c})
        return None
```

### 5.4 `rules/capability_probe.py`

```python
# 越界 deny 次数超限 → 撤销 agent (可能被劫持)
PROBE_REASONS = {"executor_mismatch","delegation_rejected","scope_exceeded","audience_mismatch"}

class CapabilityProbeRule:
    KEY    = "anomaly:cprobe:{agent}"
    N      = 3
    WINDOW = 300

    async def process(self, event, redis):
        reasons = set(event.get("deny_reasons",[]))
        if not (reasons & PROBE_REASONS): return None
        agent = event.get("caller_agent")
        if not agent: return None
        key = self.KEY.format(agent=agent)
        ts  = time.time()
        await redis.zadd(key, {event["event_id"]:ts})
        await redis.zremrangebyscore(key, 0, ts - self.WINDOW)
        await redis.expire(key, self.WINDOW * 2)
        if await redis.zcard(key) >= self.N:
            await redis.delete(key)
            return RuleResult(rule="capability_probe",
                              revoke_type="agent", revoke_value=agent,
                              severity="high",
                              evidence={"probes":self.N,"window_sec":self.WINDOW})
        return None
```

### 5.5 `rules/rate_spike.py`

```python
# 窗口内调用爆涨，仅告警不撤销 (rate_limit 已节流)
class RateSpikeRule:
    KEY    = "anomaly:rspike:{agent}"
    LIMIT  = 100
    WINDOW = 10

    async def process(self, event, redis):
        agent = event.get("caller_agent")
        if not agent: return None
        key = self.KEY.format(agent=agent)
        ts  = time.time()
        await redis.zadd(key, {f"{event['event_id']}:{ts}":ts})
        await redis.zremrangebyscore(key, 0, ts - self.WINDOW)
        await redis.expire(key, self.WINDOW * 2)
        c = await redis.zcard(key)
        if c >= self.LIMIT:
            return RuleResult(rule="rate_spike",
                              revoke_type=None, revoke_value=None,
                              severity="warning",
                              evidence={"count":c,"window_sec":self.WINDOW})
        return None
```

## 6. Action Engine

```python
# actions.py
IDP_REVOKE_URL  = "http://idp.local:8000/revoke"
AUDIT_EVENT_URL = "http://audit-api.local:8004/audit/events"
SERVICE_TOKEN   = os.environ["ANOMALY_SERVICE_TOKEN"]

async def handle_result(result, triggering_event):
    if result.revoke_type:
        await call_revoke(result, triggering_event)
    await write_alert(result, triggering_event)

async def call_revoke(result, event):
    # IdP /revoke v2 body: {type, value, reason, ttl_sec}
    # type ∈ {jti, sub, agent, trace, plan, chain}
    async with httpx.AsyncClient(timeout=2.0) as c:
        r = await c.post(IDP_REVOKE_URL,
            headers={"Authorization":f"Bearer {SERVICE_TOKEN}"},
            json={
                "type":  result.revoke_type,
                "value": result.revoke_value,
                "reason":f"anomaly:{result.rule}",
                "ttl_sec": 3600,
                "trigger_trace_id": event.get("trace_id"),
            })
        r.raise_for_status()

async def write_alert(result, event):
    alert = {
        "event_id": ulid(),
        "timestamp": utcnow_iso(),
        "event_type": "anomaly",
        "rule": result.rule,
        "severity": result.severity,
        "revoke_type": result.revoke_type,
        "revoke_value": result.revoke_value,
        "evidence": result.evidence,
        "triggering_event_id": event["event_id"],
        "trace_id": event.get("trace_id"),
        "plan_id":  event.get("plan_id"),
        "policy_version": event.get("policy_version","v1.2.0"),
    }
    async with httpx.AsyncClient(timeout=2.0) as c:
        await c.post(AUDIT_EVENT_URL,
            headers={"Authorization":f"Bearer {SERVICE_TOKEN}"},
            json={"events":[alert]})
```

## 7. Event Consumer (主循环)

```python
RULES = [ConsecutiveDenyRule(), RateSpikeRule(), ResourceDriftRule(),
         TraceLoopRule(), CapabilityProbeRule()]

async def consume():
    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as c:
                async with c.stream("GET", "http://audit-api.local:8004/audit/stream",
                                    headers={"Authorization":f"Bearer {SERVICE_TOKEN}"}) as r:
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"): continue
                        ev = json.loads(line[5:].strip())
                        await process_event(ev)
        except Exception as e:
            logger.warning("SSE reconnect: %s", e)
            await asyncio.sleep(3)

async def process_event(ev):
    for rule in RULES:
        try:
            res = await rule.process(ev, redis)
        except Exception as e:
            logger.error("rule %s error: %s", rule.__class__.__name__, e)
            continue
        if res:
            await handle_result(res, ev)
```

## 8. 规则配置

```yaml
# services/anomaly/rules_config.yaml
consecutive_deny:  { enabled:true, n:5, window_sec:60 }
rate_spike:        { enabled:true, limit:100, window_sec:10 }
resource_drift:    { enabled:true, ttl_sec:300 }
trace_loop:        { enabled:true, max_depth:3 }
capability_probe:  { enabled:true, n:3, window_sec:300 }
```

`POST /anomaly/admin/reload` 热加载。

## 9. 告警事件 Schema (写回 Audit)

```json
{
  "event_id": "evt_...",
  "timestamp": "2026-04-24T10:00:12Z",
  "event_type": "anomaly",
  "rule": "consecutive_deny",
  "severity": "critical",
  "revoke_type": "agent",
  "revoke_value": "web_agent",
  "evidence": { "count":5,"window_sec":60 },
  "triggering_event_id": "evt_prev",
  "trace_id": "01HXYZ...",
  "plan_id": "plan_...",
  "policy_version": "v1.2.0"
}
```

## 10. 演示场景 E 时序

```
t=0s   WebAgent 被 Prompt Injection，尝试越权 invoke DataAgent
t=1s   Gateway AuthZ → AUTHZ_DELEGATION_REJECTED (deny #1) → Audit
t=3s   重试 → deny #2
...
t=10s  deny #5 到 Audit → SSE 推 Anomaly
t=10s  ConsecutiveDenyRule 命中 (count=5)
t=10s  Detector → POST /revoke {type:"agent",value:"web_agent",reason:"anomaly:consecutive_deny"}
t=10s  IdP → Redis SADD revoked:agents + Pub/Sub `revoke` 广播
t=11s  Gateway bloom filter 热更新
t=12s  WebAgent 再次请求 → Gateway → 401 AUTHN_REVOKED
t=12s  Audit 新增 deny (deny_reasons=["revoked"])
t=12s  前端撤销面板实时显示 web_agent 已撤销
```

## 11. 模块文件映射

```
services/anomaly/
├── main.py                  # FastAPI: /healthz /metrics /anomaly/rules /anomaly/admin/reload /anomaly/alerts
├── detector.py              # asyncio SSE 消费主循环
├── actions.py               # call_revoke + write_alert
├── models.py                # RuleResult dataclass
├── rules_config.yaml
├── config.py
└── rules/
    ├── base.py
    ├── consecutive_deny.py
    ├── rate_spike.py
    ├── resource_drift.py
    ├── trace_loop.py
    └── capability_probe.py
```

## 12. 性能目标

| 指标 | 目标 |
|---|---|
| SSE 单事件处理延迟 | < 5ms |
| 命中 → 全部 Gateway 生效 | < 50ms |
| Redis ZSET 操作 p99 | < 2ms |
| 5 规则并行评估 | < 10ms |
| 内存占用 | < 50MB |
| SSE 断线重连 | < 3s |

## 13. 契约

| Anomaly → 外部 | 认证 |
|---|---|
| Audit API `/audit/stream` (SSE) | service token |
| IdP `/revoke` | service token |
| Audit API `/audit/events` | service token |
| Redis | ACL password |

| 外部 → Anomaly | 认证 |
|---|---|
| Admin → `/anomaly/admin/reload` | admin token |
| Web UI → `/anomaly/alerts` | admin token |
| Prometheus → `/metrics` | 内网 |
