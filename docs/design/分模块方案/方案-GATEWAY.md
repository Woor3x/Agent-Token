# Gateway (PEP) — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。所有 A2A 调用的唯一入口，无状态，承担 AuthN + Intent + AuthZ + **One-Shot 销毁** + Trace + Audit + Routing + Rate Limit。

## 1. 组件职责

- JWT (delegated token) 验签 (JWKS cache 10min)
- DPoP 校验 (cnf.jkt + htu/htm/iat ±60s + jti SETNX)
- **4 维撤销查询** (jtis / subs / traces / plans)，Bloom filter 加速
- Intent 解析 (Structured JSON Schema / NL LLM tool-calling)
- 委托链递归解析 + max_depth
- OPA 决策 (5ms timeout, fail-closed)
- **One-Shot Consume**: OPA allow 后 SETNX `jti:used:<jti>` 立即销毁 token
- W3C Traceparent 注入
- Registry 路由 + Circuit Breaker + mTLS 上游
- 审计异步批量写入

**Gateway 不代 Agent 调 IdP `/token/exchange`**。orchestrator agent 自签 assertion 自调。

## 2. 中间件链

```
Request ─▶ 1. Transport Adapter (HTTP / gRPC / MCP)
       ─▶ 2. Body Parser + Size Limit (256KB)
       ─▶ 3. AuthN Middleware
              ├─ JWT 验签 (JWKS cache 10min, LRU, 多 kid)
              ├─ iss/aud/exp/nbf 校验
              ├─ 撤销查: SISMEMBER revoked:jtis / revoked:subs / revoked:traces / revoked:plans
              │          (Bloom filter 先过，命中再查 Redis)
              └─ DPoP 校验
                 ├─ 签名匹 cnf.jkt
                 ├─ htu/htm 匹请求
                 ├─ iat ±60s
                 └─ jti SETNX dpop:jti:<j> TTL=120s
       ─▶ 4. Rate Limit (token bucket per agent_id+action, Redis)
       ─▶ 5. Intent Parser
              ├─ Structured: JSON Schema 严格 + action enum + resource regex
              └─ NL (/a2a/nl only): LLM tool-calling 强约束 → schema 二次校验
       ─▶ 6. Delegation Chain Verifier (递归解析 act + max_depth + 环检测)
       ─▶ 7. AuthZ Client → OPA
              ├─ POST /v1/data/agent/authz/allow
              ├─ 5ms timeout
              └─ 任何异常 → deny (fail-closed)
       ─▶ 8. **One-Shot Consume**
              ├─ SETNX jti:used:<jti> TTL=exp-now
              └─ 失败 → 401 TOKEN_REPLAYED
       ─▶ 9. Trace Injector (W3C traceparent + baggage)
       ─▶ 10. Router (registry.yaml: agent_id → upstream URL, 热加载)
       ─▶ 11. Circuit Breaker (per upstream, closed/open/half-open)
       ─▶ 12. Upstream Call (httpx AsyncClient + mTLS)
       ─▶ 13. Response Sanitizer (剥敏感 header + 脱敏)
       ─▶ 14. Audit Writer (asyncio.Queue 批量 → SQLite)
       ─▶ Response
```

## 3. HTTP API

### 3.1 `POST /a2a/invoke` (P0, 核心)

单次 A2A 调用入口。

Header:
```
Authorization: DPoP <delegated_token>
DPoP: <proof_jwt>
X-Target-Agent: <callee_id>
X-Plan-Id: <plan_id>
X-Task-Id: <task_id>
Traceparent: 00-<trace>-<parent_span>-01
Content-Type: application/json
```

Body:
```json
{
  "intent": {
    "action": "feishu.bitable.read",
    "resource": "app_token:bascn.../table:tbl_q1",
    "params": { "view_id": "vew...", "page_size": 100 }
  },
  "idempotency_key": "uuid-v4"
}
```

响应 200:
```json
{ "status": "ok", "data": { ... } }
```

响应 Header:
```
X-Trace-Id: 01HXYZ...
X-Audit-Id: evt_...
X-Policy-Version: v1.2.0
```

错误: 见 §4 错误码。

### 3.2 `POST /a2a/nl` (P1)

Orchestrator 自然语言入口 (Web UI → DocAssistant 也可用)。

Header: 同 3.1

Body:
```json
{
  "prompt": "生成 Q1 销售周报",
  "context": { "user_tz": "Asia/Shanghai" }
}
```

响应 200 (可选 SSE):
```json
{
  "plan_id": "plan_...",
  "trace_id": "...",
  "status": "completed",
  "result": { "doc_url": "...", "summary": "..." }
}
```

### 3.3 `POST /a2a/plan/submit` (P1)

Orchestrator 提交完整 DAG，Gateway 串起编排 (可选由 Orchestrator 自调度)。

Body:
```json
{
  "plan_id": "plan_...",
  "tasks": [...],
  "context": { ... }
}
```

响应 200: 同 3.2

### 3.4 `GET /a2a/plan/{plan_id}/status` (P2)

响应 200:
```json
{
  "plan_id": "...",
  "status": "running | completed | failed",
  "tasks": [
    { "id": "t1", "status": "completed", "result_ref": "..." },
    { "id": "t2", "status": "running" }
  ]
}
```

### 3.5 `GET /healthz` (P0)

`{"status":"ok","upstreams":{"idp":"ok","opa":"ok","redis":"ok"}}`

### 3.6 `GET /metrics` (P1)

Prometheus 指标: `gw_request_total{path=,status=}`, `gw_authn_deny_total{reason=}`, `gw_authz_deny_total{reason=}`, `gw_token_replay_total`, `gw_upstream_latency_ms`, `gw_circuit_state{upstream=,state=}`。

### 3.7 `POST /admin/reload` (P2)

认证: admin token

热加载 `registry.yaml`。响应 200: `{"reloaded":true}`。

## 4. 错误码

统一 body:
```json
{
  "error": {
    "code": "<BUSINESS_CODE>",
    "message": "<human readable>",
    "trace_id": "01HXYZ...",
    "audit_id": "evt_...",
    "policy_version": "v1.2.0"
  }
}
```

| HTTP | code | 含义 |
|---|---|---|
| 400 | `INTENT_INVALID` | schema/enum 不匹 |
| 401 | `AUTHN_TOKEN_INVALID` | JWT 验签/exp/nbf |
| 401 | `AUTHN_DPOP_INVALID` | DPoP 指纹/时间窗/重放 |
| 401 | `AUTHN_REVOKED` | jti/sub/trace/plan 命中黑名单 |
| 401 | `TOKEN_REPLAYED` | 一次性 token 已 used |
| 403 | `AUTHZ_AUDIENCE_MISMATCH` | aud ≠ target |
| 403 | `AUTHZ_SCOPE_EXCEEDED` | intent 超 scope |
| 403 | `AUTHZ_EXECUTOR_MISMATCH` | action 唯一执行者不匹 |
| 403 | `AUTHZ_DELEGATION_REJECTED` | 白名单拒 |
| 403 | `AUTHZ_DEPTH_EXCEEDED` | 委托链过深 |
| 409 | `IDEMPOTENCY_CONFLICT` | idempotency_key 冲突但参数不同 |
| 429 | `RATE_LIMITED` | token bucket 耗尽 |
| 502 | `UPSTREAM_FAIL` | 上游异常 |
| 503 | `CIRCUIT_OPEN` | 熔断 |
| 503 | `SERVER_ERROR` | OPA/Redis 不可达 (fail-closed) |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时 |

## 5. AuthN 中间件细节

```python
async def authn(request):
    # 1. parse Authorization: DPoP <jwt>
    token = parse_dpop_bearer(request.headers["Authorization"])

    # 2. JWKS 验签 (LRU cache 10min，多 kid)
    header = jwt.get_unverified_header(token)
    key = jwks_cache.get(header["kid"])
    claims = jwt.decode(
        token, key, algorithms=["RS256"],
        issuer="https://idp.local",
        audience=f"agent:{request.headers['X-Target-Agent']}",
        leeway=30,
    )
    if not claims.get("one_time"):
        raise AuthnError("AUTHN_TOKEN_INVALID", "not_one_time")

    # 3. 撤销 4 维查
    if bloom.might_contain(claims["jti"]):
        if await redis.sismember("revoked:jtis", claims["jti"]):
            raise AuthnError("AUTHN_REVOKED","jti")
    if await redis.sismember("revoked:subs",   claims["sub"]):       raise AuthnError("AUTHN_REVOKED","sub")
    if await redis.sismember("revoked:traces", claims["trace_id"]):  raise AuthnError("AUTHN_REVOKED","trace")
    if await redis.sismember("revoked:plans",  claims["plan_id"]):   raise AuthnError("AUTHN_REVOKED","plan")

    # 4. DPoP 校验
    dpop = request.headers["DPoP"]
    dpop_claims = verify_dpop(
        dpop,
        expected_jkt=claims["cnf"]["jkt"],
        expected_htu=str(request.url),
        expected_htm=request.method,
        max_iat_skew=60,
    )
    if not await redis.set(f"dpop:jti:{dpop_claims['jti']}", 1, nx=True, ex=120):
        raise AuthnError("AUTHN_DPOP_INVALID","replay")

    request.state.token_claims = claims
    request.state.dpop_claims = dpop_claims
```

## 6. Intent Parser

### 6.1 Structured 路径

JSON Schema 严格校验 (`schemas/intent.json`):
```json
{
  "type": "object",
  "required": ["action","resource"],
  "properties": {
    "action":   { "type":"string", "enum":["feishu.bitable.read","feishu.contact.read","feishu.calendar.read","feishu.doc.write","web.search","web.fetch","a2a.invoke","orchestrate"] },
    "resource": { "type":"string", "maxLength":256, "pattern":"^[a-zA-Z0-9._:/*@-]+$" },
    "params":   { "type":"object" }
  },
  "additionalProperties": false
}
```

### 6.2 NL 路径

LLM tool-calling 模式:
```python
async def parse_nl(prompt: str, user_ctx: dict) -> dict:
    resp = await llm.chat(
        system=SYSTEM_PROMPT_FIXED,         # 固定，含边界
        user=f"<user_input>{prompt}</user_input>",
        tools=[INTENT_TOOL_SCHEMA],          # action enum 硬编码
        tool_choice="required",
    )
    intent = json.loads(resp.tool_calls[0].arguments)
    validate_schema(intent, INTENT_SCHEMA)   # 二次强制
    return intent
```

Prompt Injection 防御:
1. System prompt 固定 + `<user_input>` 定界
2. LLM 输出必过 JSON Schema，失败 `INTENT_INVALID`
3. `action` enum + `resource` regex，越狱也不能伪造非白名单 action
4. 原始 prompt 进审计 `intent.raw_prompt`
5. **数据不驱动权限** — 工具返回内容不参与后续 scope 计算

## 7. 委托链 Verifier

```python
def verify_delegation(claims, max_depth):
    chain = []
    act = claims.get("act")
    while act:
        chain.append(act["sub"])
        act = act.get("act")
    if len(chain) > max_depth:
        raise AuthzError("AUTHZ_DEPTH_EXCEEDED")
    # 环检测
    if len(set(chain)) != len(chain):
        raise AuthzError("AUTHZ_DELEGATION_REJECTED","cycle")
    return chain
```

## 8. OPA 调用

```python
OPA = "http://opa.local:8181/v1/data/agent/authz"

async def authz(claims, intent, target_agent, context) -> tuple[bool,list[str]]:
    payload = {"input":{
        "token":claims, "intent":intent,
        "target_agent":target_agent, "context":context,
    }}
    try:
        async with httpx.AsyncClient(timeout=0.005) as c:
            r = await c.post(f"{OPA}/allow", json=payload)
            r.raise_for_status()
            res = r.json()["result"]
            return res["allow"], res.get("reasons", [])
    except Exception:
        return False, ["opa_unavailable"]   # fail-closed
```

Context 填充:
```json
{
  "time": 1714000000,
  "source_ip": "10.0.0.1",
  "trace_id": "...",
  "recent_calls": 3,
  "delegation_depth": 1
}
```

## 9. One-Shot Consume (销毁)

OPA allow 后立即:
```python
ttl = claims["exp"] - int(time.time())
ok = await redis.set(f"jti:used:{claims['jti']}", 1, nx=True, ex=ttl)
if not ok:
    raise AuthnError("TOKEN_REPLAYED")
# 转发到 upstream
```

SETNX 原子：并发请求同一 token 仅一成，其他全拒。Redis 单实例保证原子。

## 10. Routing + Circuit Breaker

`services/gateway/registry.yaml`:
```yaml
agents:
  doc_assistant:
    upstream: https://doc-assistant.local:8001
    transport: http
    timeout_ms: 30000
    retry: { max: 0 }            # 写操作不重试
    mtls:
      cert: /certs/gw.crt
      key:  /certs/gw.key
      ca:   /certs/ca.crt
  data_agent:
    upstream: https://data-agent.local:8002
    timeout_ms: 10000
    retry: { max: 2, backoff_ms: 200 }
  web_agent:
    upstream: https://web-agent.local:8003
    timeout_ms: 15000
    retry: { max: 1 }
```

熔断: 连续 5 失败 → open 30s → half-open 允许 1 探针。

## 11. 审计写入

- 每次决策 (allow / deny) + 每次 token consume + 响应结束写
- `asyncio.Queue` → 每 100ms / 50 条 flush
- 审计写失败不阻塞主流程；本地落盘备份 + 告警

事件 schema 见 `方案-AuditAPI.md` §3。

## 12. Gateway 自身安全

- Gateway ↔ Agents: mTLS (自签 CA)
- Gateway ↔ OPA: Unix socket 或内网 HTTP + shared secret
- Gateway ↔ IdP: 仅拉 JWKS + 订阅 Pub/Sub，不代签
- Gateway ↔ Redis: ACL password + TLS (生产)
- `/admin/reload` 受 admin token 保护
- 无状态，水平扩展

## 13. 性能目标

| 指标 | 目标 |
|---|---|
| p50 延迟 (不含 upstream) | < 15ms |
| p99 延迟 | < 50ms |
| 单实例 QPS | ≥ 500 |
| JWKS 缓存命中率 | > 99% |
| 4 维撤销查 (Bloom miss) | < 2ms |
| OPA 决策 | p99 < 10ms |

## 14. 模块文件映射

```
services/gateway/
├── main.py                    # FastAPI app + 路由
├── config.py
├── middleware/
│   ├── authn.py               # JWT + DPoP + 4-dim 撤销查
│   ├── rate_limit.py
│   ├── trace.py               # W3C traceparent
│   └── audit.py               # 异步写
├── intent/
│   ├── schema.py              # JSON Schema
│   ├── parser_structured.py
│   └── parser_nl.py           # LLM tool-calling + injection 防御
├── authz/
│   ├── opa_client.py
│   ├── delegation.py          # act 链 + 环检测
│   └── one_shot.py            # SETNX 销毁
├── token/
│   ├── jwks_cache.py
│   └── dpop.py
├── routing/
│   ├── registry.py
│   ├── circuit_breaker.py
│   └── upstream_client.py     # httpx + mTLS
├── revoke/
│   ├── subscriber.py          # Pub/Sub 订阅
│   └── bloom.py               # Bloom filter
└── errors.py
```

## 15. 契约

| 调用方 → Gateway | 认证 |
|---|---|
| Web UI → `/a2a/nl` | User access token (Bearer, 可选 DPoP) |
| Agent SDK → `/a2a/invoke` | Delegated Token (DPoP 绑定) + DPoP proof |
| Admin CLI → `/admin/reload` | Admin token + mTLS |

| Gateway → 下游 | 认证 |
|---|---|
| Gateway → IdP `/jwks` | 无 (公开) |
| Gateway → IdP Pub/Sub (`revoke`, `policy_reload`) | Redis ACL |
| Gateway → OPA | 内网 + shared secret |
| Gateway → Executor Agent | mTLS + 转发 delegated token (upstream 仍可验，一次性已 used) |
| Gateway → Audit API (通过 SQLite 共享 volume) | 文件权限 |
