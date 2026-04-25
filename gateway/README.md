# Gateway (PEP) — M2

Policy Enforcement Point for all A2A calls. Stateless, horizontally scalable.

## 快速启动

```bash
cd service/gateway
uv venv .venv && uv pip install -r requirements.txt
source .venv/bin/activate

# 配置环境变量（见 .env.example）
cp .env.example .env

uvicorn main:app --host 0.0.0.0 --port 8080
```

## 中间件链

```
Request
  → trace_middleware       # W3C traceparent 生成/传播
  → authn_middleware       # JWT 验签 + 4 维撤销 + DPoP 校验
  → rate_limit_middleware  # Token bucket per agent+action
  → (路由处理器)
      → Intent 解析        # JSON Schema 严格校验 / NL LLM tool-calling
      → Delegation 验证    # act 链递归 + 环检测 + max_depth
      → OPA authz          # HTTP 5ms timeout, fail-closed
      → One-Shot Consume   # Redis SETNX jti:used
      → Trace 注入         # traceparent + baggage
      → Router             # registry.yaml → upstream URL
      → Circuit Breaker    # per upstream closed/open/half-open
      → Upstream Call      # httpx AsyncClient + mTLS
      → Response Sanitize  # 剥敏感 header
      → Audit Write        # asyncio.Queue → SQLite
```

## API 接口

| Method | Path | 说明 |
|--------|------|------|
| POST | `/a2a/invoke` | 单次 A2A 调用（P0） |
| POST | `/a2a/nl` | 自然语言入口（P1） |
| POST | `/a2a/plan/submit` | DAG 编排提交（P1） |
| GET  | `/a2a/plan/{plan_id}/status` | DAG 状态查询（P2） |
| GET  | `/healthz` | 健康检查 |
| GET  | `/metrics` | Prometheus 指标 |
| POST | `/admin/reload` | 热加载 registry.yaml（需 admin token）|

## 错误码

| HTTP | code | 含义 |
|------|------|------|
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
| 409 | `IDEMPOTENCY_CONFLICT` | idempotency_key 冲突 |
| 429 | `RATE_LIMITED` | token bucket 耗尽 |
| 502 | `UPSTREAM_FAIL` | 上游异常 |
| 503 | `CIRCUIT_OPEN` | 熔断 |
| 503 | `SERVER_ERROR` | OPA/Redis 不可达 |
| 504 | `UPSTREAM_TIMEOUT` | 上游超时 |

统一 error body:
```json
{
  "error": {
    "code": "AUTHZ_SCOPE_EXCEEDED",
    "message": "intent exceeds granted scope",
    "trace_id": "01HXYZ...",
    "audit_id": "evt_...",
    "policy_version": "v1.0.0"
  }
}
```

## 文件结构

```
gateway/
├── main.py              # FastAPI app + lifespan + route mount
├── config.py            # 所有配置项（env: GW_*）
├── errors.py            # 错误码 + 统一异常处理器
├── registry.yaml        # agent_id → upstream 映射
├── bench.py             # 性能压测脚本
├── middleware/
│   ├── authn.py         # JWT + DPoP + 4 维撤销
│   ├── rate_limit.py    # Token bucket (Lua + Redis)
│   ├── trace.py         # W3C traceparent
│   └── audit.py         # asyncio.Queue → SQLite
├── intent/
│   ├── schema.py        # JSON Schema + validator
│   ├── parser_structured.py
│   └── parser_nl.py     # Anthropic tool-calling + injection 防御
├── authz/
│   ├── opa_client.py    # OPA HTTP client, fail-closed
│   ├── delegation.py    # act 链 + 环检测
│   └── one_shot.py      # SETNX 销毁
├── token/
│   ├── jwks_cache.py    # LRU + 10min 刷新 + 多 kid
│   └── dpop.py          # DPoP proof 验证
├── routing/
│   ├── registry.py      # YAML 热加载
│   ├── circuit_breaker.py
│   └── upstream_client.py  # httpx + mTLS + sanitizer
├── revoke/
│   ├── bloom.py         # Bloom filter (pre-check)
│   └── subscriber.py    # Redis Pub/Sub → bloom.add
├── routes/
│   ├── invoke.py        # POST /a2a/invoke
│   ├── nl.py            # POST /a2a/nl
│   ├── plan.py          # POST /a2a/plan/submit + GET status
│   └── admin.py         # POST /admin/reload
└── tests/
    ├── conftest.py
    ├── test_authn.py
    ├── test_authz.py
    ├── test_intent.py
    ├── test_routing.py
    └── test_one_shot.py
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GW_IDP_JWKS_URL` | `https://idp.local:8443/jwks` | IdP JWKS 端点 |
| `GW_IDP_ISSUER` | `https://idp.local` | JWT iss 验证 |
| `GW_OPA_URL` | `http://opa.local:8181/v1/data/agent/authz` | OPA 地址 |
| `GW_REDIS_URL` | `redis://redis.local:6379/0` | Redis 连接 |
| `GW_ADMIN_TOKEN` | `changeme` | `/admin/reload` 鉴权 |
| `GW_MTLS_ENABLED` | `false` | 是否启用 mTLS |
| `GW_ANTHROPIC_API_KEY` | `""` | NL parser 使用 |
| `GW_DELEGATION_MAX_DEPTH` | `4` | 最大委托链深度 |
| `GW_AUDIT_DB_PATH` | `audit.db` | SQLite 路径 |

## 运行测试

```bash
pytest tests/ -v
```

## 性能压测

```bash
python bench.py --url http://localhost:8080 --qps 500 --duration 30 --token <token>
```

目标: p99 < 50ms @ 500 QPS（不含 upstream 延迟）。

## 依赖关系

- **M1 (IdP)**: JWKS 端点、Pub/Sub 撤销广播
- **M3 (OPA)**: `/v1/data/agent/authz/allow` 决策端点
- **M3 (Redis)**: 撤销集合 (`revoked:jtis` 等)、DPoP jti set、rate limit bucket
