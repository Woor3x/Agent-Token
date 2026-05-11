# Gateway (PEP)

A2A（Agent-to-Agent）调用的策略执行点。所有 Agent 间请求必须经过 Gateway 鉴权，Gateway 无状态，支持水平扩展。

---

## 主要功能

- **JWT 验签**：RS256，支持多 kid JWKS LRU 缓存（10min TTL），密钥轮换无需重启
- **DPoP 绑定**（RFC 9449）：验证 proof-of-possession，防止 token 被盗用；jti 存 Redis 防重放
- **4 维撤销**（Redis 查询）：jti / sub / trace_id / plan_id 任一维度命中即拒绝
- **OPA 授权**：每次请求转发给 OPA，5ms 超时，fail-closed（OPA 不可达直接拒绝）
- **委托链验证**（RFC 8693 `act`）：递归校验 JWT act 嵌套，环检测 + 最大深度限制
- **一次性 token**：`one_time: true` 的 token 消费后立即标记，二次使用返回 401
- **速率限制**：令牌桶算法（Redis Lua），key 为 `{sub}:{target_agent}`，容量 100，补充 10/s
- **熔断器**：per-upstream closed/open/half-open 状态机，连续 5 次失败打开，30s 后半开探针
- **DAG 编排**：`/a2a/plan/submit` 接收任务 DAG，拓扑排序后并发执行独立任务
- **审计写入**：每次请求（允许/拒绝）写入本地 SQLite，并异步转发给 Audit API
- **W3C Trace**：生成/传播 traceparent，span_id 贯穿中间件链和上游响应头

---

## 请求处理流程

### POST /a2a/invoke

每次调用独立走完整鉴权链：

```
POST /a2a/invoke
  → trace_middleware       # W3C traceparent 生成/传播
  → authn_middleware       # JWT RS256 验签 + DPoP 绑定 + 4 维撤销
  → rate_limit_middleware  # 令牌桶（{sub}:{target_agent}）
  → route handler
      → Intent 解析        # JSON Schema 校验 action/resource
      → Delegation 验证    # act 链递归 + 环检测 + max_depth
      → OPA authz          # 5ms timeout，fail-closed
      → One-Shot 消费      # Redis SETNX jti:used（token 作废）
      → call_upstream      # httpx → agent /invoke，熔断器
      → 审计写入           # asyncio.Queue → SQLite / Audit API
```

### POST /a2a/plan/submit

提交时认证一次，One-Shot token 在提交时消费；各任务在后台异步执行，每个任务复用编排者 claims 独立走 Intent 校验 + OPA 授权：

```
POST /a2a/plan/submit
  → trace_middleware       # 同 invoke
  → authn_middleware       # 同 invoke（验证编排者 token）
  → rate_limit_middleware  # 同 invoke
  → route handler
      → One-Shot 消费      # 提交时消费编排者 jti（唯一一次）
      → 创建 plan 记录     # 内存 _plans[plan_id]
      → asyncio.create_task(_execute_dag)   ← 立即返回 plan_id
      ↓ 后台异步
      → 拓扑排序 + 并发 fan-out（满足 depends_on 的任务并发执行）
      → 每个 task（串行/并行按 DAG）:
          → Intent 解析    # task action + resource 校验
          → Delegation 验证 # act 链递归 + 环检测 + max_depth
          → OPA authz      # 复用编排者 claims，per-task 策略检查
          → 审计写入       # event_type=authz_decision，per-task
          → call_upstream  # httpx → agent /a2a/task，熔断器
      → 审计写入（plan 级别，event_type=orchestrate）
```

> `plan` 与 `invoke` 是**独立路径**，任务不经过 `/a2a/invoke`。上游 Agent 收到的是 `POST /a2a/task`，请求头含 `Content-Type` 和 `traceparent`，无独立 JWT/DPoP（per-task token 由 IdP 颁发超出此设计范围；编排者 one-shot 已在提交时消费）。

---

## 接口信息

### 认证方式

所有 `/a2a/*` 接口需携带：

```
Authorization: DPoP <agent-jwt>
DPoP: <dpop-proof-jwt>
```

`/admin/reload` 使用：
```
Authorization: Bearer <admin-token>
```

`/healthz` 和 `/metrics` 无需认证。

---

### POST /a2a/invoke

单次 A2A 调用。请求经过完整鉴权后转发给目标 Agent 的 `/invoke` 端点。

**必要请求头**

| 头 | 说明 |
|----|------|
| `X-Target-Agent` | 目标 agent_id，须在 `registry.yaml` 中注册 |
| `Authorization` | `DPoP <agent-jwt>` |
| `DPoP` | DPoP proof JWT |

**Request body**（JSON）

```json
{
  "intent": {
    "action": "feishu.bitable.read",
    "resource": "app_token:foo/table:bar",
    "params": {}
  }
}
```

合法 `action` 枚举：`feishu.bitable.read` / `feishu.bitable.read_all` / `feishu.contact.read` / `feishu.calendar.read` / `feishu.docx.read` / `feishu.doc.write` / `web.search` / `web.fetch` / `a2a.invoke` / `orchestrate`

`resource` 最长 512 字符，仅允许 URL-safe RFC3986 字符（禁止空格和控制字符）。

**Response**：透传目标 Agent 的响应，附加以下响应头：

```
X-Trace-Id: 01KRB5...
X-Policy-Version: v1.2.0
```

---

### POST /a2a/plan/submit

提交 DAG 编排计划，异步执行。

**Request body**

```json
{
  "plan_id": "plan_abc123",
  "tasks": [
    {
      "id": "t1",
      "agent_id": "data_agent",
      "depends_on": [],
      "payload": {}
    },
    {
      "id": "t2",
      "agent_id": "web_agent",
      "depends_on": ["t1"],
      "payload": {}
    }
  ],
  "context": {}
}
```

`plan_id` 如缺省则自动生成。

**Response 200**
```json
{ "plan_id": "plan_abc123", "trace_id": "01KRB5...", "status": "running" }
```

---

### GET /a2a/plan/{plan_id}/status

查询 DAG 执行状态。

**Response 200**
```json
{
  "plan_id": "plan_abc123",
  "status": "completed",
  "tasks": [
    { "id": "t1", "status": "completed", "result_ref": "t1" },
    { "id": "t2", "status": "failed" }
  ]
}
```

`status` 取值：`running` / `completed` / `failed`

未找到 plan_id 返回 404。

---

### GET /healthz

无需认证。

**Response 200**
```json
{
  "status": "ok",
  "upstreams": { "redis": "ok", "idp": "ok", "opa": "ok" },
  "circuit_breakers": { "data_agent": "closed", "web_agent": "closed" }
}
```

`status` 为 `degraded` 表示至少一个依赖不健康。

---

### GET /metrics

无需认证。返回 Prometheus text format。建议仅在内网暴露。

---

### POST /admin/reload

热加载 `registry.yaml`，无需重启服务（admin token）。

**Response 200**
```json
{ "status": "ok", "agents": ["doc_assistant", "data_agent", "web_agent"] }
```

---

## 错误码与 Error Body

所有错误响应统一格式：

```json
{
  "error": {
    "code": "AUTHZ_SCOPE_EXCEEDED",
    "message": "policy denied: scope too broad",
    "trace_id": "01KRB5...",
    "audit_id": "evt_abc123",
    "policy_version": "v1.2.0"
  }
}
```

| HTTP | code | 触发场景 |
|------|------|----------|
| 400 | `INTENT_INVALID` | intent 字段缺失、action 不在枚举、resource 格式/长度非法、额外字段 |
| 401 | `AUTHN_TOKEN_INVALID` | JWT 验签失败、exp 过期、iss 不匹配、缺少 `one_time` 或 `cnf.jkt` 字段、kid 未知 |
| 401 | `AUTHN_DPOP_INVALID` | 缺少 DPoP 头、htm/htu 不匹配、jkt thumbprint 不匹配、jti 重放 |
| 401 | `AUTHN_REVOKED` | jti / sub / trace_id / plan_id 在撤销集合中 |
| 401 | `TOKEN_REPLAYED` | `one_time` token 已被消费 |
| 403 | `AUTHZ_AUDIENCE_MISMATCH` | token aud ≠ target agent |
| 403 | `AUTHZ_SCOPE_EXCEEDED` | OPA 拒绝（intent 超出授权 scope） |
| 403 | `AUTHZ_EXECUTOR_MISMATCH` | action 要求特定执行者但不匹配 |
| 403 | `AUTHZ_DELEGATION_REJECTED` | 委托链不在 OPA 白名单 |
| 403 | `AUTHZ_DEPTH_EXCEEDED` | act 链深度超过 `GW_DELEGATION_MAX_DEPTH` |
| 409 | `IDEMPOTENCY_CONFLICT` | idempotency_key 冲突 |
| 429 | `RATE_LIMITED` | 令牌桶耗尽 |
| 502 | `UPSTREAM_FAIL` | 上游 Agent 返回 5xx 或网络错误 |
| 503 | `CIRCUIT_OPEN` | 熔断器打开，上游暂时不可用 |
| 503 | `SERVER_ERROR` | OPA / Redis 不可达（fail-closed） |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时 |
| 500 | `SERVER_ERROR` | 未预期异常 |

---

## 环境变量

前缀 `GW_`。

### 服务器

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_HOST` | `0.0.0.0` | 监听地址 |
| `GW_PORT` | `8080` | 监听端口（docker-compose 映射为 9200） |
| `GW_LOG_LEVEL` | `info` | 日志级别 |
| `GW_POLICY_VERSION` | `v1.0.0` | 策略版本号，写入响应头和审计日志 |

### 身份认证（IdP / JWT）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_IDP_JWKS_URL` | `https://idp.local/.well-known/jwks.json` | JWKS 拉取地址 |
| `GW_IDP_ISSUER` | `https://idp.local` | JWT `iss` 期望值 |
| `GW_JWKS_CACHE_TTL` | `600` | 公钥缓存有效期（秒） |
| `GW_JWKS_MAX_KEYS` | `10` | 公钥 LRU 最大条目数 |

### OPA

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_OPA_URL` | `http://opa.local:8181/v1/data/agent/authz` | OPA 授权接口地址 |
| `GW_OPA_TIMEOUT_MS` | `5` | OPA 请求超时（毫秒），超时即 fail-closed |

### Redis

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_REDIS_URL` | `redis://localhost:6379/0` | 连接地址，支持 redis:// 和 rediss:// |
| `GW_REDIS_PASSWORD` | `""` | Redis 认证密码 |

### DPoP

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_DPOP_MAX_IAT_SKEW` | `60` | DPoP `iat` 允许的时钟偏差（秒） |
| `GW_DPOP_JTI_TTL` | `120` | DPoP jti 在 Redis 中保留时间（秒），防重放窗口 |

### 速率限制

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_RATE_LIMIT_CAPACITY` | `100` | 令牌桶容量（最大突发） |
| `GW_RATE_LIMIT_REFILL_RATE` | `10.0` | 令牌补充速率（每秒） |

### 熔断器

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_CB_FAILURE_THRESHOLD` | `5` | 连续失败次数阈值，超过后打开熔断器 |
| `GW_CB_OPEN_DURATION` | `30` | 熔断器打开后等待多少秒进入半开状态 |
| `GW_CB_HALF_OPEN_PROBES` | `1` | 半开状态允许通过的探针请求数 |

### 委托链

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_DELEGATION_MAX_DEPTH` | `4` | JWT act 链允许的最大嵌套深度 |

### mTLS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_MTLS_ENABLED` | `false` | 是否启用与 Agent 之间的双向 TLS |
| `GW_MTLS_CERT` | `/certs/gw.crt` | Gateway 客户端证书路径 |
| `GW_MTLS_KEY` | `/certs/gw.key` | 配套私钥路径 |
| `GW_MTLS_CA` | `/certs/ca.crt` | 根 CA 证书路径（验证 Agent 服务端证书） |

### 管理接口

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_ADMIN_TOKEN` | `change-me-in-production` | `/admin/reload` Bearer token，生产必须替换 |
| `GW_REGISTRY_PATH` | `registry.yaml` | Agent 注册表文件路径 |

### 审计

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_AUDIT_DB_PATH` | `./audit.db` | 本地 SQLite 审计数据库路径 |
| `GW_AUDIT_FLUSH_INTERVAL_MS` | `100` | 批量写入间隔（毫秒） |
| `GW_AUDIT_BATCH_SIZE` | `50` | 批量写入条数阈值 |
| `GW_AUDIT_API_URL` | `""` | Audit API 地址，留空则不转发。例：`http://audit-api:8090` |
| `GW_AUDIT_API_TOKEN` | `""` | 发往 Audit API 的 service token |
