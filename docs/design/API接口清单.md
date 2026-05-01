# API 接口清单

> 本文档汇总所有服务的对外 HTTP API、内部契约、共享 schema、Redis 键空间、Pub/Sub 通道、SDK 接口、错误码。开发与联调基准。

## 0. 服务端口与基准 URL (演示环境)

| 服务 | 端口 | 基准 URL | 说明 |
|---|---|---|---|
| IdP | 8080 | `https://idp.local:8080` | 身份与授权 |
| Gateway | 8443 | `https://gateway.local:8443` | 所有 A2A 调用入口 |
| OPA | 8181 | `http://opa.local:8181` | PDP (内网) |
| Audit API | 8090 | `http://audit.local:8090` | 审计查询 |
| Anomaly Detector | 8091 | `http://anomaly.local:8091` | 异常检测 |
| DocAssistant | 8001 | `http://doc-assistant.local:8001` | Orchestrator |
| DataAgent | 8002 | `http://data-agent.local:8002` | Executor (飞书) |
| WebAgent | 8003 | `http://web-agent.local:8003` | Executor (外部) |
| Feishu Mock | 8999 | `http://feishu-mock.local:8999` | 开发用替身 |
| Redis | 6379 | `redis://redis.local:6379` | 状态存储 |
| Web UI | 3000 | `https://web.local:3000` | 前端 |

外部对 Gateway、Web UI、IdP (OIDC 登录) 可见。其他走内网 + mTLS。

所有 API 请求与响应 Content-Type 若无特别说明均为 `application/json`。

---

## 1. IdP (`services/idp`)

### 1.1 `GET /.well-known/openid-configuration` (P0)

**用途**: OIDC discovery。

**认证**: 无。

**响应 200**:
```json
{
  "issuer": "https://idp.local",
  "authorization_endpoint": "https://idp.local/oidc/authorize",
  "token_endpoint": "https://idp.local/oidc/token",
  "userinfo_endpoint": "https://idp.local/oidc/userinfo",
  "jwks_uri": "https://idp.local/jwks",
  "revocation_endpoint": "https://idp.local/revoke",
  "token_exchange_endpoint": "https://idp.local/token/exchange",
  "plan_validate_endpoint": "https://idp.local/plan/validate",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:token-exchange"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["private_key_jwt"],
  "dpop_signing_alg_values_supported": ["RS256", "ES256"]
}
```

### 1.2 `GET /jwks` (P0)

**用途**: 公钥分发。

**认证**: 无。

**响应 200**:
```json
{
  "keys": [
    { "kty": "RSA", "kid": "idp-2026-04-v1", "use": "sig", "alg": "RS256", "n": "...", "e": "AQAB" },
    { "kty": "RSA", "kid": "idp-2026-01-v0", "use": "sig", "alg": "RS256", "n": "...", "e": "AQAB" }
  ]
}
```

**Header**: `Cache-Control: public, max-age=300`

### 1.3 `GET /healthz` (P0)

**响应 200**: `{"status":"ok","version":"1.0.0","policy_version":"v1.2.0"}`

### 1.4 `GET /metrics` (P1)

Prometheus 格式文本。

### 1.5 `GET /oidc/authorize` (P0)

**用途**: 用户登录起点 (Authorization Code + PKCE)。

**Query 参数**:
| 参数 | 必需 | 说明 |
|---|---|---|
| `response_type` | ✓ | 固定 `code` |
| `client_id` | ✓ | 如 `web-ui` |
| `redirect_uri` | ✓ | 注册白名单内 |
| `scope` | ✓ | `openid profile agent:invoke` |
| `state` | ✓ | CSRF 防御 |
| `code_challenge` | ✓ | PKCE |
| `code_challenge_method` | ✓ | `S256` |

**响应**: 302 → 登录页或 `redirect_uri?code=...&state=...`

### 1.6 `POST /oidc/token` (P0)

**用途**: 授权码换 token。

**Body (form)**:
```
grant_type=authorization_code
&code=<code>
&redirect_uri=<uri>
&client_id=web-ui
&code_verifier=<verifier>
```

**响应 200**:
```json
{
  "access_token": "<jwt>",
  "id_token": "<jwt>",
  "refresh_token": "<opaque>",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

**错误**: `invalid_request | invalid_grant | unauthorized_client`

### 1.7 `GET /oidc/userinfo` (P0)

**认证**: `Authorization: Bearer <user_access_token>`

**响应 200**:
```json
{
  "sub": "user:alice",
  "name": "Alice",
  "email": "alice@example.com",
  "permissions_hash": "sha256:..."   // 前端用于判断是否需要 refresh
}
```

### 1.8 `POST /oidc/refresh` (P1)

**Body**:
```json
{ "grant_type": "refresh_token", "refresh_token": "..." }
```

**响应**: 同 `/oidc/token`

### 1.9 `POST /token/exchange` (P0, 核心)

**用途**: Agent 用 client assertion + 用户 token 换一次性 delegated token (RFC 8693)。

**Header**:
```
Content-Type: application/x-www-form-urlencoded
DPoP: <proof_jwt>
```

**Body (form)**:
| 字段 | 必需 | 说明 |
|---|---|---|
| `grant_type` | ✓ | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `client_assertion_type` | ✓ | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `client_assertion` | ✓ | Agent 私钥签的 JWT (RFC 7523) |
| `subject_token` | ✓ | 用户 access token |
| `subject_token_type` | ✓ | `urn:ietf:params:oauth:token-type:access_token` |
| `requested_token_type` | ✓ | `urn:ietf:params:oauth:token-type:jwt` |
| `audience` | ✓ | `agent:<callee_id>`，如 `agent:data_agent` |
| `scope` | ✓ | `<action>:<resource>`，如 `feishu.bitable.read:app_token:.../table:tbl_q1` |
| `resource` | ✓ | Gateway URL，如 `https://gateway.local/a2a/invoke` |
| `purpose` | ✓ | 本次意图摘要 |
| `plan_id` | ✓ | DAG id |
| `task_id` | ✓ | DAG 节点 id |
| `trace_id` | ✓ | W3C trace_id |
| `parent_span` | - | W3C parent span |

**响应 200**:
```json
{
  "access_token": "<delegated_jwt>",
  "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
  "token_type": "DPoP",
  "expires_in": 120,
  "jti": "tok-uuid-v4",
  "policy_version": "v1.2.0",
  "audit_id": "evt_..."
}
```

**错误**: 见 §9 错误码表。

### 1.10 `POST /plan/validate` (P1)

**用途**: DAG 预审，不颁 token，批量决策。

**Header**: `DPoP: <proof>`

**Body**:
```json
{
  "client_assertion": "<orchestrator_jwt>",
  "subject_token": "<user_token>",
  "plan": {
    "plan_id": "plan_01HXYZ",
    "tasks": [
      { "id": "t1", "agent": "data_agent", "action": "feishu.bitable.read", "resource": "app_token:.../table:tbl_q1", "deps": [] },
      { "id": "t2", "agent": "web_agent",  "action": "web.search",          "resource": "*", "deps": [] },
      { "id": "t3", "agent": "doc_assistant", "action": "feishu.doc.write", "resource": "doc_token:weekly", "deps": ["t1","t2"] }
    ]
  }
}
```

**响应 200**:
```json
{
  "plan_id": "plan_01HXYZ",
  "overall": "allow",
  "tasks": [
    { "id": "t1", "decision": "allow", "reasons": [] },
    { "id": "t2", "decision": "allow", "reasons": [] },
    { "id": "t3", "decision": "allow", "reasons": [] }
  ],
  "policy_version": "v1.2.0",
  "audit_id": "evt_..."
}
```

任一 deny → `overall="deny"`，orchestrator 不执行。

### 1.11 `POST /revoke` (P0)

**用途**: 撤销 token / 主体 / 链路。

**认证**: `Authorization: Bearer <admin_or_service_token>`

**Body**:
```json
{
  "type": "jti",                    // jti | sub | agent | trace | plan | chain
  "value": "tok-uuid",
  "reason": "anomaly:consecutive_deny=5",
  "ttl_sec": 3600                   // 可选
}
```

**响应 200**:
```json
{ "revoked": true, "event_id": "evt_...", "effective_at": "2026-04-24T10:00:00Z" }
```

**错误**: `invalid_request | unauthorized | internal_error`

### 1.12 `GET /revoke/status` (P2)

**Query**: `?type=jti&value=tok-uuid`

**响应 200**: `{ "revoked": true, "reason": "...", "revoked_at": "..." }`

### 1.13 `POST /agents/register` (P0)

**用途**: 注册 Agent 生成密钥对。

**认证**: `Authorization: Bearer <admin_token>`

**Body**:
```json
{
  "agent_id": "data_agent",
  "role": "executor",
  "display_name": "企业数据 Agent",
  "contact": "team@example.com",
  "desired_key_alg": "RS256",
  "capabilities_yaml": "<base64 of capabilities/data_agent.yaml>"
}
```

**响应 200**:
```json
{
  "agent_id": "data_agent",
  "kid": "agent-data-2026-04-v1",
  "private_key_pem": "-----BEGIN PRIVATE KEY-----...",
  "delivery": "one-time-download",
  "warning": "IdP does not retain this private key; store securely"
}
```

**错误**: `sod_violation | agent_exists | invalid_capabilities`

### 1.14 `POST /agents/{agent_id}/rotate-key` (P1)

**认证**: admin token

**响应 200**: 新 kid + 新 private_key_pem。旧 kid 宽限期内仍可用。

### 1.15 `GET /agents` (P2)

**认证**: admin token

**响应 200**: agent 列表 + 状态。

### 1.16 `POST /admin/reload` (P1)

**用途**: 热加载 policy 数据 (capabilities/users/executor_map)。

**认证**: admin token

**响应 200**: `{"reloaded": true, "policy_version": "v1.2.1"}`

---

## 2. Gateway (`services/gateway`)

### 2.1 `POST /a2a/invoke` (P0, 核心)

**用途**: 单次 A2A 调用入口。

**Header**:
```
Authorization: DPoP <delegated_token>
DPoP: <proof_jwt>
X-Target-Agent: <callee_id>
X-Plan-Id: <plan_id>
X-Task-Id: <task_id>
Traceparent: 00-<trace>-<span>-01
Content-Type: application/json
```

**Body**:
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

**响应 200**:
```json
{ "status": "ok", "data": { ... } }
```

**响应 Header**:
```
X-Trace-Id: 01HXYZ...
X-Audit-Id: evt_...
X-Policy-Version: v1.2.0
```

**错误**: 见 §9。

### 2.2 `POST /a2a/nl` (P1)

**用途**: Orchestrator 自然语言入口 (Web UI → DocAssistant 时也可用此端点)。

**Header**: 同 2.1

**Body**:
```json
{
  "prompt": "生成 Q1 销售周报",
  "context": { "user_tz": "Asia/Shanghai" }
}
```

**响应 200** (流式 SSE 可选):
```json
{
  "plan_id": "plan_...",
  "trace_id": "...",
  "status": "completed",
  "result": { "doc_url": "...", "summary": "..." }
}
```

### 2.3 `POST /a2a/plan/submit` (P1)

**用途**: Orchestrator 提交完整 DAG，Gateway 串起编排 (可选由 Orchestrator 自调度)。

**Body**:
```json
{
  "plan_id": "plan_...",
  "tasks": [...],
  "context": { ... }
}
```

**响应 200**: 同 2.2

### 2.4 `GET /a2a/plan/{plan_id}/status` (P2)

**响应 200**:
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

### 2.5 `GET /healthz` (P0)

### 2.6 `GET /metrics` (P1)

### 2.7 `POST /admin/reload` (P2)

**用途**: 热加载 registry.yaml

---

## 3. Agents

### 3.1 DocAssistant (Orchestrator, `agents/doc_assistant`)

DocAssistant 通过 Gateway 被调。不直接暴露给外部 (Web UI 调时经 Gateway)。

#### 3.1.1 `POST /invoke` (P0)

**调用者**: Gateway (转发 `/a2a/nl` 或 `/a2a/invoke`)

**Header**: Gateway 注入的 `Authorization: DPoP <token>` + trace

**Body**:
```json
{
  "intent": { "action": "orchestrate", "resource": "user_request", "params": { "prompt": "..." } }
}
```

**响应 200**:
```json
{ "status": "ok", "result": { "plan_id": "...", "doc_url": "...", "summary": "..." } }
```

#### 3.1.2 `GET /healthz` (P0)

### 3.2 DataAgent (`agents/data_agent`)

#### 3.2.1 `POST /invoke` (P0)

**调用者**: Gateway (orchestrator 委托)

**Body**:
```json
{
  "intent": {
    "action": "feishu.bitable.read",
    "resource": "app_token:bascn.../table:tbl_q1",
    "params": { "view_id": "vew...", "filter": "...", "page_size": 100 }
  }
}
```

**响应 200**:
```json
{ "status": "ok", "data": { "items": [...], "total": 42, "has_more": false } }
```

**支持的 action**:
- `feishu.bitable.read`
- `feishu.contact.read`
- `feishu.calendar.read`

#### 3.2.2 `GET /healthz` (P0)

### 3.3 WebAgent (`agents/web_agent`)

#### 3.3.1 `POST /invoke` (P0)

**Body**:
```json
{
  "intent": {
    "action": "web.search",
    "resource": "*",
    "params": { "query": "竞品 Q1 财报", "top_k": 10 }
  }
}
```

**响应 200**:
```json
{ "status": "ok", "data": { "results": [{"title":"...","url":"...","snippet":"..."}] } }
```

**支持的 action**:
- `web.search`
- `web.fetch` (`params.url`)

#### 3.3.2 `GET /healthz` (P0)

---

## 4. Audit API (`services/audit-api`)

### 4.1 `GET /audit/events` (P0)

**Query**:
| 参数 | 说明 |
|---|---|
| `trace_id` | 精确 |
| `plan_id` | 精确 |
| `agent_id` | caller 或 callee |
| `decision` | `allow \| deny` |
| `event_type` | `authz.decision \| token.issue \| token.revoke \| ...` |
| `from`, `to` | ISO8601 时间范围 |
| `page`, `size` | 分页 |

**响应 200**:
```json
{
  "total": 1234,
  "page": 1, "size": 50,
  "events": [ { ...see audit schema... } ]
}
```

### 4.2 `GET /audit/events/{event_id}` (P0)

**响应 200**: 单条审计记录详情。

### 4.3 `GET /audit/trace/{trace_id}` (P1)

**用途**: 一次完整调用链的所有事件 (按 span 树排序)。

**响应 200**:
```json
{
  "trace_id": "...",
  "root_span": "...",
  "duration_ms": 543,
  "events": [...],
  "tree": { "span": "root", "children": [...] }
}
```

### 4.4 `GET /audit/plan/{plan_id}` (P1)

**响应 200**: DAG + 每节点状态 + token 颁发历史。

### 4.5 `GET /audit/stream` (P1, SSE)

**Header**: `Accept: text/event-stream`

**响应**: 持续推送 audit event (JSON 每行)，供 Web UI 实时展示 + anomaly detector 订阅。

### 4.6 `GET /audit/export` (P2)

**Query**: 同 4.1 + `format=csv|json`

**响应 200**: 文件下载。

### 4.7 `GET /healthz` (P0)

---

## 5. Anomaly Detector (`services/anomaly`)

对外 API 少，主要消费 `/audit/stream` + 调 IdP `/revoke`。但暴露少量管理端点:

### 5.1 `GET /anomaly/rules` (P2)

**响应 200**:
```json
{
  "rules": [
    { "id": "consecutive_deny", "enabled": true, "threshold": 5, "window_sec": 60 },
    { "id": "rate_spike",       "enabled": true, "threshold": 100, "window_sec": 10 },
    { "id": "resource_drift",   "enabled": true },
    { "id": "trace_loop",       "enabled": true, "max_depth": 3 },
    { "id": "capability_probe", "enabled": true, "threshold": 3 }
  ]
}
```

### 5.2 `POST /anomaly/rules/{rule_id}` (P2)

**认证**: admin token

**Body**: `{ "enabled": true, "threshold": 10 }`

**响应 200**: 已更新配置。

### 5.3 `GET /anomaly/recent` (P2)

**响应 200**: 最近 N 条触发事件。

### 5.4 `GET /healthz` (P0)

---

## 6. OPA 调用契约 (`services/opa`)

Gateway 与 IdP 调 OPA 用 Data API。

### 6.1 `POST /v1/data/agent/authz/allow` (P0)

**用途**: Gateway 单次调用鉴权决策。

**Body**:
```json
{
  "input": {
    "token": {
      "iss": "https://idp.local",
      "sub": "user:alice",
      "act": { "sub": "doc_assistant", "act": null },
      "aud": "agent:data_agent",
      "scope": ["feishu.bitable.read:app_token:.../table:tbl_q1"],
      "purpose": "...",
      "plan_id": "...", "task_id": "...",
      "trace_id": "...",
      "jti": "...", "exp": ..., "nbf": ...,
      "cnf": { "jkt": "..." },
      "one_time": true
    },
    "intent": { "action": "feishu.bitable.read", "resource": "app_token:.../table:tbl_q1" },
    "target_agent": "data_agent",
    "context": { "time": 1714000000, "source_ip": "10.0.0.1", "recent_calls": 3 }
  }
}
```

**响应 200**:
```json
{
  "result": {
    "allow": true,
    "reasons": [],
    "policy_version": "v1.2.0"
  }
}
```

若 deny:
```json
{ "result": { "allow": false, "reasons": ["executor_mismatch","scope_exceeded"], "policy_version":"v1.2.0" } }
```

### 6.2 `POST /v1/data/agent/authz/plan_allow` (P1)

**Body**:
```json
{
  "input": {
    "orchestrator_token": { ... },
    "user": "alice",
    "plan": [ { "id":"t1", "agent":"data_agent", "action":"...", "resource":"..." } ],
    "context": { ... }
  }
}
```

**响应 200**:
```json
{ "result": { "overall": "allow", "per_task": [ { "id":"t1","allow":true } ] } }
```

### 6.3 `PUT /v1/data/executor_map` (P0)

**Body**: `executor_map.json` 内容

**响应 204**: 热更新。

### 6.4 `PUT /v1/data/agents` / `/v1/data/users` / `/v1/data/revoked` (P0)

同 6.3，分别热更对应数据。

---

## 7. 飞书 OpenAPI 代理层 (DataAgent 内部)

DataAgent 作为唯一持有飞书 tenant_token 的 Agent。它消费飞书 API:

### 7.1 `GET https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records` (上游)
### 7.2 `GET https://open.feishu.cn/open-apis/contact/v3/users` (上游)
### 7.3 `POST https://open.feishu.cn/open-apis/docx/v1/documents/{doc_id}/blocks` (上游, DocAssistant 调用)
### 7.4 `POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal` (获取凭证)

DataAgent/DocAssistant 细化 params 遵循飞书官方文档。Mock Server 端点镜像真实飞书的路径与响应 schema。

### 7.5 Feishu Mock (`services/feishu-mock`)

| Method | Path | 说明 |
|---|---|---|
| POST | `/open-apis/auth/v3/tenant_access_token/internal` | 返回假 token |
| GET  | `/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records` | 返回种子数据 |
| GET  | `/open-apis/contact/v3/users` | 通讯录 |
| POST | `/open-apis/docx/v1/documents/{doc_id}/blocks` | 写入 (存本地文件) |
| GET  | `/mock/reset` | 重置种子 (仅开发) |

---

## 8. SDK 接口 (`sdk/agent_token_sdk`)

### 8.1 `AgentClient.__init__`

```python
AgentClient(
    agent_id: str,
    private_key_path: str,
    kid: str,
    idp_url: str,
    gateway_url: str,
    algorithm: str = "RS256",
    dpop_key_path: str | None = None,
    timeout_sec: int = 10,
    retries: int = 2,
)
```

### 8.2 `async AgentClient.invoke(...)` (P0)

```python
async def invoke(
    target: str,
    intent: dict,
    on_behalf_of: str,                    # 用户 access token
    purpose: str,
    plan_id: str,
    task_id: str,
    trace_id: str | None = None,
    parent_span: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
```

内部流程:
1. 签 client assertion (`iss=sub=agent_id`, `aud=idp/token`, `exp ≤ 60s`, 自签)
2. 签 DPoP proof (for IdP endpoint)
3. 调 IdP `/token/exchange` → 拿 delegated_token
4. 签 DPoP proof (for Gateway endpoint)
5. 调 Gateway `/a2a/invoke` (Authorization: DPoP + DPoP header)
6. 返回 `data`

抛异常:
- `AgentTokenDenyError` (403)
- `AgentTokenExpiredError` (401)
- `AgentTokenReplayError` (401)
- `AgentTokenRateLimitError` (429)
- `AgentTokenUpstreamError` (5xx)

### 8.3 `async AgentClient.validate_plan(plan)` (P1)

```python
async def validate_plan(plan: dict, on_behalf_of: str) -> dict:
```

调 IdP `/plan/validate`。

### 8.4 `async AgentClient.revoke(type, value, reason)` (P1)

仅持有 admin/service token 的客户端可用。

### 8.5 `AgentServer` (P1)

SDK 提供给 Executor Agent 的 server helper:

```python
server = AgentServer(
    agent_id="data_agent",
    capabilities_yaml="capabilities/data_agent.yaml",
    idp_jwks_url="https://idp.local/jwks",
)

@server.handler(action="feishu.bitable.read")
async def read_bitable(intent, context):
    ...
    return { "items": [...] }

server.run(port=8002)
```

自动校验 Gateway 注入的 trace_id / 入站 token 基础形态。

### 8.6 transport 抽象 (P2)

```python
class Transport(Protocol):
    async def post(url, headers, body) -> Response: ...

class HttpTransport(Transport): ...
class GrpcTransport(Transport): ...
class McpTransport(Transport): ...
```

---

## 9. 统一错误码表 (跨服务)

HTTP 状态 + 业务 code 双值。Body 统一:

```json
{
  "error": {
    "code": "<BUSINESS_CODE>",
    "message": "<human readable>",
    "trace_id": "...",
    "audit_id": "evt_...",
    "policy_version": "v1.2.0"
  }
}
```

| HTTP | code | 来源 | 含义 |
|---|---|---|---|
| 400 | `INTENT_INVALID` | Gateway | 意图 schema/enum 不匹配 |
| 400 | `INVALID_REQUEST` | IdP | 缺字段 |
| 400 | `UNSUPPORTED_GRANT_TYPE` | IdP | grant 非法 |
| 400 | `INVALID_CAPABILITIES` | IdP | 注册时 yaml 不合法 |
| 400 | `SOD_VIOLATION` | IdP | orchestrator ∩ executor ≠ ∅ |
| 400 | `AGENT_EXISTS` | IdP | agent_id 重复注册 |
| 401 | `AUTHN_TOKEN_INVALID` | Gateway | JWT 验签/exp |
| 401 | `AUTHN_DPOP_INVALID` | Gateway/IdP | DPoP 不合法 |
| 401 | `AUTHN_REVOKED` | Gateway | 黑名单命中 |
| 401 | `TOKEN_REPLAYED` | Gateway | 一次性 token 已 used |
| 401 | `INVALID_CLIENT` | IdP | assertion 验签失败 |
| 401 | `INVALID_GRANT` | IdP | subject_token 失效 |
| 401 | `ASSERTION_REPLAY` | IdP | assertion jti 重放 |
| 403 | `AUTHZ_AUDIENCE_MISMATCH` | Gateway | aud ≠ target |
| 403 | `AUTHZ_SCOPE_EXCEEDED` | Gateway | intent 超 scope |
| 403 | `AUTHZ_EXECUTOR_MISMATCH` | Gateway/IdP | 单执行者违反 |
| 403 | `AUTHZ_DELEGATION_REJECTED` | Gateway/IdP | 白名单拒 |
| 403 | `AUTHZ_DEPTH_EXCEEDED` | Gateway/IdP | 委托链过深 |
| 403 | `AUTHZ_USER_DENIED` | IdP | user 无权限 |
| 403 | `EMPTY_EFFECTIVE_SCOPE` | IdP | 交集空 |
| 403 | `CONTEXT_DENIED` | IdP | 时间/IP/配额 |
| 403 | `AGENT_REVOKED` | IdP | agent 全局封禁 |
| 403 | `UNAUTHORIZED` | 多 | admin 接口无权 |
| 409 | `IDEMPOTENCY_CONFLICT` | Gateway | idempotency_key 冲突 |
| 429 | `RATE_LIMITED` | Gateway/IdP | 限流 |
| 500 | `INTERNAL_ERROR` | 任 | 服务器错误 |
| 502 | `UPSTREAM_FAIL` | Gateway | 上游错误 |
| 503 | `CIRCUIT_OPEN` | Gateway | 熔断开 |
| 503 | `SERVER_ERROR` | 任 | 暂不可用 (fail-closed) |
| 504 | `UPSTREAM_TIMEOUT` | Gateway | 上游超时 |

---

## 10. 共享 JSON Schema (`schemas/`)

### 10.1 Intent Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["action","resource"],
  "properties": {
    "action":   { "type": "string", "enum": ["feishu.bitable.read","feishu.contact.read","feishu.calendar.read","feishu.doc.write","web.search","web.fetch","a2a.invoke","orchestrate"] },
    "resource": { "type": "string", "maxLength": 256, "pattern": "^[a-zA-Z0-9._:/*@-]+$" },
    "params":   { "type": "object" }
  },
  "additionalProperties": false
}
```

### 10.2 Plan Schema

```json
{
  "type": "object",
  "required": ["plan_id","tasks"],
  "properties": {
    "plan_id": { "type": "string", "pattern": "^plan_[A-Za-z0-9]+$" },
    "raw_prompt": { "type": "string", "maxLength": 4096 },
    "tasks": {
      "type": "array",
      "maxItems": 20,
      "items": {
        "type": "object",
        "required": ["id","agent","action","resource"],
        "properties": {
          "id":       { "type": "string" },
          "agent":    { "type": "string" },
          "action":   { "type": "string" },
          "resource": { "type": "string" },
          "params":   { "type": "object" },
          "deps":     { "type": "array", "items": { "type": "string" } }
        }
      }
    }
  }
}
```

### 10.3 Delegated Token Claims

```json
{
  "iss":"https://idp.local",
  "sub":"user:alice",
  "act":{"sub":"doc_assistant","act":null},
  "aud":"agent:data_agent",
  "scope":["feishu.bitable.read:app_token:.../table:tbl_q1"],
  "purpose":"generate_weekly_report",
  "plan_id":"plan_...", "task_id":"t1",
  "trace_id":"...", "parent_span":"...",
  "iat":1714000000, "nbf":1714000000, "exp":1714000120,
  "jti":"tok-uuid-v4",
  "cnf":{"jkt":"<thumbprint>"},
  "one_time":true,
  "policy_version":"v1.2.0"
}
```

### 10.4 Client Assertion Claims

```json
{
  "iss":"agent:doc_assistant",
  "sub":"agent:doc_assistant",
  "aud":"https://idp.local/token/exchange",
  "iat":1714000000, "exp":1714000060,
  "jti":"assert-uuid-v4"
}
```

### 10.5 DPoP Proof Claims

```json
{
  "htu":"https://gateway.local/a2a/invoke",
  "htm":"POST",
  "iat":1714000000,
  "jti":"dpop-uuid-v4",
  "nonce":"<server-issued>"
}
```

Header:
```json
{"typ":"dpop+jwt","alg":"RS256","jwk":{"kty":"RSA","n":"...","e":"AQAB"}}
```

### 10.6 Audit Event

```json
{
  "event_id":"evt_...",
  "event_type":"authz.decision",
  "timestamp":"2026-04-24T10:00:00.123Z",
  "trace_id":"...", "span_id":"...", "parent_span_id":"...",
  "plan_id":"...", "task_id":"...",
  "decision":"allow",
  "deny_reasons":[],
  "caller":{"agent_id":"doc_assistant","delegation_chain":["user:alice","doc_assistant"]},
  "callee":{"agent_id":"data_agent","action":"feishu.bitable.read","resource":"..."},
  "intent":{"raw_prompt":"...","parsed":{...},"purpose":"..."},
  "token":{"jti":"...","exp":...,"scope":[...],"one_time":true,"consumed_at":"..."},
  "result":{"status":200,"bytes":4521},
  "policy_version":"v1.2.0",
  "latency_ms":143,
  "source_ip":"10.0.0.1"
}
```

### 10.7 Capability Yaml (Agent)

```yaml
agent_id: string
display_name: string
role: orchestrator | executor
public_key_jwk: { kty, n, e, kid, alg }
capabilities:
  - action: string            # enum
    resource_pattern: string  # glob
    constraints:
      max_rows_per_call: int
      max_calls_per_minute: int
delegation:
  accept_from: [ agent_id, ... ]    # 白名单
  max_depth: int
underlying_credentials: [ string ]  # Vault path
```

### 10.8 User Yaml

```yaml
user_id: string
permissions:
  - action: string
    resource_pattern: string
```

---

## 11. Redis 键空间 (内部状态)

| Key Pattern | 类型 | TTL | 用途 |
|---|---|---|---|
| `assertion:jti:<j>` | string | 120s | Client assertion 防重放 |
| `jti:used:<j>` | string | = token exp 剩余 | 一次性 token 销毁 |
| `dpop:jti:<j>` | string | 120s | DPoP proof 防重放 |
| `revoked:jtis` | set | 无限 (成员级 TTL) | 已撤销 token jti |
| `revoked:subs` | set | 同上 | 已封禁用户 sub |
| `revoked:agents` | set | 同上 | 已封禁 agent |
| `revoked:traces` | set | 同上 | 已终止 trace |
| `revoked:plans` | set | 同上 | 已终止 plan |
| `revoked:chains` | set | 同上 | 已撤销委托链 |
| `rate:gw:<agent>:<action>` | token bucket | - | Gateway 限流 |
| `rate:idp:<agent>:<path>` | token bucket | - | IdP 限流 |
| `anomaly:deny:<agent>` | zset (timestamp) | 60s 滑窗 | 连续 deny 计数 |
| `anomaly:calls:<agent>` | zset | 10s 滑窗 | 调用频次 |
| `jwks:cache` | string (JSON) | 600s | Gateway 本地 JWKS 缓存 (可选 Redis) |
| `pubsub:revoke` | channel | - | 撤销广播 |
| `pubsub:policy_reload` | channel | - | 策略热更广播 |

---

## 12. Pub/Sub 通道

| 通道 | 发布者 | 订阅者 | 消息 schema |
|---|---|---|---|
| `revoke` | IdP | Gateway, Anomaly, Audit | `{ "type":"jti","value":"...","reason":"...","ts":... }` |
| `policy_reload` | IdP/Admin | Gateway, OPA | `{ "policy_version":"v1.2.1","ts":... }` |
| `audit_stream` | Audit API | Anomaly, Web UI | 同 §10.6 |
| `agent_register` | IdP | Gateway, Web UI | `{ "agent_id":"...","kid":"...","ts":... }` |
| `agent_rotate` | IdP | Gateway | `{ "agent_id":"...","new_kid":"...","old_kid":"...","grace_until":... }` |

---

## 13. 后台任务 / 定时器

| 任务 | 所属服务 | 频率 | 说明 |
|---|---|---|---|
| JWKS 缓存刷新 | Gateway | 10min | 从 IdP `/jwks` 拉 |
| Bloom filter 重建 | Gateway | 5min | 从 Redis revoked:* 重构 |
| Audit 批量 flush | Gateway/IdP | 100ms | asyncio queue → SQLite |
| 密钥轮换检测 | IdP | 每日 | 到期即 rotate |
| 过期 jti 清理 | IdP | 小时 | Redis 自动 TTL |
| Anomaly 规则扫描 | Anomaly | 1s | 消费 audit stream |
| 健康探针 | 全 | 10s | docker healthcheck |
| Prom 指标采集 | Prometheus | 15s | scrape |

---

## 14. 认证矩阵 (谁能调谁)

| 调用方 → 被调方 | 认证方式 |
|---|---|
| Browser → Web UI | Session Cookie (SameSite=Strict) |
| Browser → IdP (`/oidc/*`) | OIDC PKCE |
| Web UI → Gateway (`/a2a/nl`) | User access token (Bearer, 可选 DPoP) |
| Agent → IdP (`/token/exchange`) | Client Assertion (RFC 7523) + user token + DPoP |
| Agent → Gateway (`/a2a/invoke`) | Delegated Token (DPoP 绑定) + DPoP proof |
| Gateway → Agent (upstream) | mTLS + Gateway 注入的 Traceparent + 转发的 delegated token |
| Gateway → OPA | 内网 HTTP + shared secret / Unix socket |
| IdP → OPA | 同 Gateway → OPA |
| Anomaly → IdP (`/revoke`) | Service token (admin scope) |
| Admin CLI → IdP (`/agents/register`, `/revoke`) | Admin token + mTLS |
| 所有服务 → Redis | ACL (密码) + TLS (生产) |
| 所有服务 → SQLite | 文件权限 + 挂载 volume |

---

## 15. 完整性自检清单

IdP 端点 (16 个):
- [x] `/.well-known/openid-configuration`
- [x] `/jwks`
- [x] `/healthz`
- [x] `/metrics`
- [x] `/oidc/authorize`
- [x] `/oidc/token`
- [x] `/oidc/userinfo`
- [x] `/oidc/refresh`
- [x] `/token/exchange`
- [x] `/plan/validate`
- [x] `/revoke`
- [x] `/revoke/status`
- [x] `/agents/register`
- [x] `/agents/{id}/rotate-key`
- [x] `/agents`
- [x] `/admin/reload`

Gateway 端点 (7 个):
- [x] `/a2a/invoke`
- [x] `/a2a/nl`
- [x] `/a2a/plan/submit`
- [x] `/a2a/plan/{id}/status`
- [x] `/healthz`
- [x] `/metrics`
- [x] `/admin/reload`

Agents (3 × 2 端点):
- [x] DocAssistant `/invoke`, `/healthz`
- [x] DataAgent `/invoke`, `/healthz`
- [x] WebAgent `/invoke`, `/healthz`

Audit API (7 个):
- [x] `/audit/events`
- [x] `/audit/events/{id}`
- [x] `/audit/trace/{trace_id}`
- [x] `/audit/plan/{plan_id}`
- [x] `/audit/stream`
- [x] `/audit/export`
- [x] `/healthz`

Anomaly (4 个):
- [x] `/anomaly/rules` GET
- [x] `/anomaly/rules/{id}` POST
- [x] `/anomaly/recent`
- [x] `/healthz`

OPA Data API (6 个):
- [x] `/v1/data/agent/authz/allow`
- [x] `/v1/data/agent/authz/plan_allow`
- [x] `/v1/data/executor_map` PUT
- [x] `/v1/data/agents` PUT
- [x] `/v1/data/users` PUT
- [x] `/v1/data/revoked` PUT

Feishu Mock (5 个):
- [x] tenant_access_token
- [x] bitable records
- [x] contact users
- [x] docx blocks
- [x] `/mock/reset`

共享契约:
- [x] 错误码表 (30+ 条)
- [x] Intent schema
- [x] Plan schema
- [x] Delegated token claims
- [x] Client assertion claims
- [x] DPoP proof claims
- [x] Audit event schema
- [x] Capability yaml schema
- [x] User yaml schema

Redis / Pub-Sub / 定时任务 / 认证矩阵 / SDK 接口 — 全覆盖。

## 16. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-04-24 | 初版，覆盖方案 v2 全部接口 |
