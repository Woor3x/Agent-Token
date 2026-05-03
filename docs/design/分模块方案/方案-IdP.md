# IdP (身份提供者) — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。全动态授权、无长期 authz token、一次性 delegated token、Client Assertion (RFC 7523) + Token Exchange (RFC 8693)。

## 1. 组件职责

IdP = 信任根 + 授权决定源。**不持有业务资源凭证**。

- **身份注册**: `/agents/register` 生成 Agent 密钥对，公钥入库，私钥一次性下发
- **用户登录**: 本地 OIDC Authorization Code + PKCE，颁发 User Access Token
- **Client Assertion 验证** (RFC 7523): 每次换 token 现验，jti 防重放
- **Token Exchange** (RFC 8693): `user.permissions ∩ callee.capabilities ∩ requested` 动态交集 → 签发一次性短 TTL delegated token (≤ 120s)
- **DAG 预审** `/plan/validate`: 调 OPA `plan_allow` 批量决策，不颁 token
- **撤销** `/revoke`: 6 粒度 (jti/sub/agent/trace/plan/chain) 写 Redis + Pub/Sub 广播
- **JWKS 分发** + 密钥轮换 (双公钥期)
- **审计**: 每次颁发/拒发/撤销/注册/轮换写 SQLite

职责边界:
| 归 IdP 管 | 不归 IdP 管 |
|---|---|
| Agent 注册、密钥对生成/轮换 | Agent 业务逻辑 |
| User OIDC 登录 | User 密码校验 (委托下游或本地 bcrypt) |
| Client Assertion 验证 | 调用真实资源 (Gateway + Executor 负责) |
| 动态交集 + 一次性 token 签发 | Token 使用状态 (Gateway 销毁) |
| 撤销事件颁发 | 异常检测规则 (Anomaly Detector 负责) |
| JWKS 分发 | OPA 策略维护 (独立仓库) |

## 2. 组件架构

```
┌─────────────────────────── IdP Process (FastAPI) ──────────────────┐
│                                                                    │
│  HTTP Layer                                                        │
│   /.well-known  /jwks  /healthz  /metrics                          │
│   /oidc/authorize  /oidc/token  /oidc/userinfo  /oidc/refresh      │
│   /token/exchange       ← 核心端点                                 │
│   /plan/validate                                                   │
│   /revoke   /revoke/status                                         │
│   /agents/register  /agents/{id}/rotate-key  /agents               │
│   /admin/reload                                                    │
│       │                                                            │
│       ▼                                                            │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │ OIDC Module     │  │ Token Exchange   │  │ Revocation       │   │
│  │ - authorize     │  │ (10-phase)       │  │ - jti/sub/agent  │   │
│  │ - /token        │  │ - assertion verify│ │   trace/plan/chain│  │
│  │ - userinfo      │  │ - intent parse   │  │ - Pub/Sub 广播   │   │
│  │ - PKCE          │  │ - exec map       │  │ - bloom hint     │   │
│  │ - refresh       │  │ - intersect scope│  └────────┬─────────┘   │
│  └────────┬────────┘  │ - context rules  │           │             │
│           │           │ - one-time sign  │           │             │
│           │           └────────┬─────────┘           │             │
│           ▼                    ▼                     ▼             │
│  ┌───────────────────────────────────────────────────────────┐     │
│  │              Core Services                                │     │
│  │  ┌────────┐ ┌─────────┐ ┌──────────┐ ┌────────────────┐  │     │
│  │  │KMS     │ │JWKS     │ │Capability│ │OPA Client      │  │     │
│  │  │(age)   │ │cache    │ │Loader    │ │(plan validate) │  │     │
│  │  └────────┘ └─────────┘ └──────────┘ └────────────────┘  │     │
│  │  ┌────────┐ ┌─────────┐ ┌──────────┐ ┌────────────────┐  │     │
│  │  │Assertion│ │Audit    │ │Rate      │ │DPoP Validator  │  │     │
│  │  │Verifier │ │Writer   │ │Limiter   │ │(thumbprint)    │  │     │
│  │  └────────┘ └─────────┘ └──────────┘ └────────────────┘  │     │
│  └───────────────────────────────────────────────────────────┘     │
│           │                    │                     │             │
└───────────┼────────────────────┼─────────────────────┼─────────────┘
            ▼                    ▼                     ▼
      ┌──────────┐         ┌─────────┐          ┌────────────┐
      │ KMS store│         │ Redis   │          │ SQLite     │
      │ (age enc │         │ assert. │          │ agents +   │
      │ / Vault) │         │ jti,    │          │ users +    │
      │          │         │ revoked,│          │ jwks_hist +│
      │          │         │ pubsub) │          │ audit      │
      └──────────┘         └─────────┘          └────────────┘
```

## 3. HTTP API

### 3.1 `GET /.well-known/openid-configuration` (P0)

认证: 无。

响应 200:
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
  "grant_types_supported": ["authorization_code","refresh_token","urn:ietf:params:oauth:grant-type:token-exchange"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["private_key_jwt"],
  "dpop_signing_alg_values_supported": ["RS256","ES256"]
}
```

### 3.2 `GET /jwks` (P0)

响应 200 (`Cache-Control: public, max-age=300`):
```json
{
  "keys": [
    { "kty":"RSA","kid":"idp-2026-04-v1","use":"sig","alg":"RS256","n":"...","e":"AQAB" },
    { "kty":"RSA","kid":"idp-2026-01-v0","use":"sig","alg":"RS256","n":"...","e":"AQAB" }
  ]
}
```

轮换期双公钥并存。

### 3.3 `GET /healthz` (P0)

`{"status":"ok","version":"1.0.0","policy_version":"v1.2.0"}`

### 3.4 `GET /metrics` (P1)

Prometheus 文本。指标: `idp_token_exchange_total{decision=}`, `idp_revoke_total{type=}`, `idp_assertion_replay_total`, `idp_key_rotation_total`。

### 3.5 `GET /oidc/authorize` (P0)

用户登录起点 (Authorization Code + PKCE)。

Query:
| 参数 | 必需 | 说明 |
|---|---|---|
| `response_type` | ✓ | 固定 `code` |
| `client_id` | ✓ | `web-ui` |
| `redirect_uri` | ✓ | 白名单内 |
| `scope` | ✓ | `openid profile agent:invoke` |
| `state` | ✓ | CSRF |
| `code_challenge` | ✓ | PKCE |
| `code_challenge_method` | ✓ | `S256` |

响应: 302 → 登录页 or `redirect_uri?code=...&state=...`

### 3.6 `POST /oidc/token` (P0)

Body (form):
```
grant_type=authorization_code
&code=<code>
&redirect_uri=<uri>
&client_id=web-ui
&code_verifier=<verifier>
```

响应 200:
```json
{
  "access_token":"<jwt>",
  "id_token":"<jwt>",
  "refresh_token":"<opaque>",
  "token_type":"Bearer",
  "expires_in":3600
}
```

Access Token claims:
```json
{
  "iss":"https://idp.local",
  "sub":"user:alice",
  "aud":"web-ui",
  "scope":["openid","profile","agent:invoke"],
  "exp":...,"iat":...,"jti":"..."
}
```

错误: `invalid_request | invalid_grant | unauthorized_client`

### 3.7 `GET /oidc/userinfo` (P0)

认证: `Authorization: Bearer <user_access_token>`

响应 200:
```json
{
  "sub":"user:alice",
  "name":"Alice",
  "email":"alice@example.com",
  "permissions_hash":"sha256:..."
}
```

### 3.8 `POST /oidc/refresh` (P1)

Body: `{"grant_type":"refresh_token","refresh_token":"..."}`

响应同 3.6。

### 3.9 `POST /token/exchange` (P0, **核心**)

Agent 用 client assertion + user token 换一次性 delegated token。

Header:
```
Content-Type: application/x-www-form-urlencoded
DPoP: <proof_jwt>
```

Body (form):
| 字段 | 必需 | 说明 |
|---|---|---|
| `grant_type` | ✓ | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `client_assertion_type` | ✓ | `urn:ietf:params:oauth:client-assertion-type:jwt-bearer` |
| `client_assertion` | ✓ | Agent 私钥签 JWT (RFC 7523) |
| `subject_token` | ✓ | user access token |
| `subject_token_type` | ✓ | `urn:ietf:params:oauth:token-type:access_token` |
| `requested_token_type` | ✓ | `urn:ietf:params:oauth:token-type:jwt` |
| `audience` | ✓ | `agent:<callee_id>` |
| `scope` | ✓ | `<action>:<resource>` 单项 |
| `resource` | ✓ | Gateway URL |
| `purpose` | ✓ | 本次意图摘要 |
| `plan_id` | ✓ | DAG id |
| `task_id` | ✓ | DAG 节点 id |
| `trace_id` | ✓ | W3C |
| `parent_span` | - | W3C parent |

响应 200:
```json
{
  "access_token":"<delegated_jwt>",
  "issued_token_type":"urn:ietf:params:oauth:token-type:jwt",
  "token_type":"DPoP",
  "expires_in":120,
  "jti":"tok-uuid-v4",
  "policy_version":"v1.2.0",
  "audit_id":"evt_..."
}
```

Client Assertion claims (RFC 7523):
```json
{
  "iss":"agent:doc_assistant",
  "sub":"agent:doc_assistant",
  "aud":"https://idp.local/token/exchange",
  "iat":1714000000,"exp":1714000060,
  "jti":"assert-uuid"
}
```

Delegated Token claims (签发结果):
```json
{
  "iss":"https://idp.local",
  "sub":"user:alice",
  "act":{"sub":"agent:doc_assistant","act":null},
  "aud":"agent:data_agent",
  "scope":["feishu.bitable.read:app_token:.../table:tbl_q1"],
  "purpose":"generate_weekly_report",
  "plan_id":"plan_01HXYZ","task_id":"t1",
  "trace_id":"01HXYZ-root","parent_span":"...",
  "iat":1714000000,"nbf":1714000000,"exp":1714000120,
  "jti":"tok-uuid-v4",
  "cnf":{"jkt":"<caller-DPoP-thumbprint>"},
  "one_time":true,
  "policy_version":"v1.2.0"
}
```

错误 (统一 §4 错误码):
`invalid_request | unsupported_grant_type | invalid_client | invalid_grant | assertion_replay | dpop_invalid | delegation_not_allowed | callee_rejects_caller | executor_mismatch | empty_effective_scope | context_denied | agent_revoked | rate_limited | server_error`

### 3.10 `POST /plan/validate` (P1)

DAG 预审，不颁 token。

Header: `DPoP: <proof>`

Body:
```json
{
  "client_assertion":"<orchestrator_jwt>",
  "subject_token":"<user_token>",
  "plan":{
    "plan_id":"plan_01HXYZ",
    "tasks":[
      {"id":"t1","agent":"data_agent","action":"feishu.bitable.read","resource":"app_token:.../table:tbl_q1","deps":[]},
      {"id":"t2","agent":"web_agent","action":"web.search","resource":"*","deps":[]},
      {"id":"t3","agent":"doc_assistant","action":"feishu.doc.write","resource":"doc_token:weekly","deps":["t1","t2"]}
    ]
  }
}
```

响应 200:
```json
{
  "plan_id":"plan_01HXYZ",
  "overall":"allow",
  "tasks":[
    {"id":"t1","decision":"allow","reasons":[]},
    {"id":"t2","decision":"allow","reasons":[]},
    {"id":"t3","decision":"allow","reasons":[]}
  ],
  "policy_version":"v1.2.0",
  "audit_id":"evt_..."
}
```

任一 deny → `overall=deny`，orchestrator 不执行。

### 3.11 `POST /revoke` (P0)

认证: `Authorization: Bearer <admin_or_service_token>`

Body:
```json
{
  "type":"jti",
  "value":"tok-uuid",
  "reason":"anomaly:consecutive_deny=5",
  "ttl_sec":3600
}
```

`type` ∈ `{jti, sub, agent, trace, plan, chain}` — **6 粒度**。

响应 200:
```json
{ "revoked":true,"event_id":"evt_...","effective_at":"2026-04-24T10:00:00Z" }
```

处理:
1. SADD `revoked:<plural>` + 成员级 TTL
2. PUBLISH `revoke` 通道广播给 Gateway/Anomaly/Audit
3. 写审计 `token.revoke`

错误: `invalid_request | unauthorized | internal_error`

### 3.12 `GET /revoke/status` (P2)

Query: `?type=jti&value=tok-uuid`

响应 200: `{"revoked":true,"reason":"...","revoked_at":"..."}`

### 3.13 `POST /agents/register` (P0)

认证: `Authorization: Bearer <admin_token>`

Body:
```json
{
  "agent_id":"data_agent",
  "role":"executor",
  "display_name":"企业数据 Agent",
  "contact":"team@example.com",
  "desired_key_alg":"RS256",
  "capabilities_yaml":"<base64 of capabilities/data_agent.yaml>"
}
```

处理:
1. 解 yaml，校验 schema
2. **SoD 静态校验**: orchestrator capability ∩ 所有 executor capability = ∅
3. 生成密钥对 (RSA-2048 / Ed25519)，`kid = sha256(pub_der)[:12]`
4. 写 `agents` 表 (状态 active)
5. 审计 `agent.register`
6. 私钥**一次性**返回

响应 200:
```json
{
  "agent_id":"data_agent",
  "kid":"agent-data-2026-04-v1",
  "private_key_pem":"-----BEGIN PRIVATE KEY-----...",
  "delivery":"one-time-download",
  "warning":"IdP does not retain this private key; store securely"
}
```

错误: `sod_violation | agent_exists | invalid_capabilities | unauthorized`

### 3.14 `POST /agents/{agent_id}/rotate-key` (P1)

认证: admin token

响应 200:
```json
{
  "agent_id":"data_agent",
  "new_kid":"agent-data-2026-10-v2",
  "old_kid":"agent-data-2026-04-v1",
  "grace_until":"2026-11-01T00:00:00Z",
  "private_key_pem":"..."
}
```

宽限期内双 kid 并存。Pub/Sub 广播 `agent_rotate`。

### 3.15 `GET /agents` (P2)

认证: admin token

响应 200:
```json
{
  "agents":[
    {"agent_id":"doc_assistant","role":"orchestrator","status":"active","kid":"...","registered_at":"..."},
    {"agent_id":"data_agent","role":"executor","status":"active","kid":"...","registered_at":"..."}
  ]
}
```

### 3.16 `POST /admin/reload` (P1)

认证: admin token

用途: 热加载 capabilities/*.yaml、users/*.yaml、executor_map。

响应 200:
```json
{ "reloaded":true,"policy_version":"v1.2.1" }
```

同时 PUBLISH `policy_reload` 通道。

## 4. 错误码

统一 body:
```json
{
  "error":{
    "code":"<BUSINESS_CODE>",
    "message":"<human readable>",
    "trace_id":"...",
    "audit_id":"evt_...",
    "policy_version":"v1.2.0"
  }
}
```

| HTTP | code | 含义 |
|---|---|---|
| 400 | `INVALID_REQUEST` | 缺字段/格式错 |
| 400 | `UNSUPPORTED_GRANT_TYPE` | grant 非 token-exchange |
| 400 | `INVALID_CAPABILITIES` | 注册 yaml 不合法 |
| 400 | `SOD_VIOLATION` | orchestrator ∩ executor ≠ ∅ |
| 400 | `AGENT_EXISTS` | agent_id 已存在 |
| 401 | `INVALID_CLIENT` | assertion 验签失败/kid 未知 |
| 401 | `INVALID_GRANT` | subject_token 失效 |
| 401 | `ASSERTION_REPLAY` | assertion jti 复用 |
| 401 | `AUTHN_DPOP_INVALID` | DPoP proof 不合法 |
| 403 | `AUTHZ_DELEGATION_REJECTED` | 白名单拒 |
| 403 | `AUTHZ_EXECUTOR_MISMATCH` | executor_map 不匹 |
| 403 | `AUTHZ_DEPTH_EXCEEDED` | 委托链过深 |
| 403 | `EMPTY_EFFECTIVE_SCOPE` | 交集为空 |
| 403 | `CONTEXT_DENIED` | 时间/IP/配额 |
| 403 | `AGENT_REVOKED` | agent 全局封禁 |
| 403 | `UNAUTHORIZED` | admin 无权 |
| 429 | `RATE_LIMITED` | IdP 限流 |
| 500 | `INTERNAL_ERROR` | 服务器错误 |
| 503 | `SERVER_ERROR` | KMS/DB/OPA 不可用 (fail-closed) |

## 5. Token Exchange 10-Phase 算法

```python
async def token_exchange(req: TokenExchangeRequest, dpop_header: str) -> dict:
    # Phase 1: 身份验证
    orchestrator = await verify_client_assertion(req.client_assertion)
    user         = await verify_subject_token(req.subject_token)

    # Phase 2: DPoP 绑定
    dpop_claims = verify_dpop_proof(
        dpop_header, htu="https://idp.local/token/exchange",
        htm="POST", max_skew=60
    )
    dpop_jkt = dpop_claims["jkt"]

    # Phase 3: 意图解析 (白名单 ENUM + REGEX)
    action, resource = parse_scope(req.scope)
    if action not in ACTION_ENUM:
        raise Forbidden("unknown_action")
    if not RESOURCE_REGEX[action].match(resource):
        raise Forbidden("resource_format")

    # Phase 4: 委托合法性
    orch_caps = await load_capabilities(orchestrator.agent_id)
    if not whitelist_match(orch_caps, "a2a.invoke", f"agent:{req.target_agent}"):
        raise Forbidden("delegation_not_allowed")

    callee_meta = await load_agent(req.target_agent)
    if orchestrator.agent_id not in callee_meta.delegation.accept_from:
        raise Forbidden("callee_rejects_caller")

    # Phase 5: 单执行者校验 (executor_map)
    if EXECUTOR_MAP[action] != req.target_agent:
        raise Forbidden("executor_mismatch")

    # Phase 6: 动态交集
    callee_caps = await load_capabilities(req.target_agent)
    user_perms  = await load_permissions(user.sub)
    effective   = intersect(callee_caps, user_perms, [(action, resource)])
    if not effective:
        raise Forbidden("empty_effective_scope")

    # Phase 7: 上下文约束
    effective = apply_context(effective, {
        "time": now(), "user": user.sub,
        "trace_id": req.trace_id, "client_ip": req.client_ip,
        "plan_id": req.plan_id,
    })
    if not effective:
        raise Forbidden("context_denied")

    # Phase 8: 配额
    if not await rate_limit_ok(orchestrator.agent_id, action):
        raise RateLimited()

    # Phase 9: 签发一次性 delegated token
    now_s = int(time.time())
    claims = {
        "iss":"https://idp.local", "sub":user.sub,
        "act":{"sub":orchestrator.agent_id,"act":None},
        "aud":f"agent:{req.target_agent}",
        "scope":effective, "purpose":req.purpose,
        "plan_id":req.plan_id, "task_id":req.task_id,
        "trace_id":req.trace_id, "parent_span":req.parent_span,
        "iat":now_s, "nbf":now_s, "exp":now_s+120,
        "jti":str(uuid.uuid4()),
        "cnf":{"jkt":dpop_jkt},
        "one_time":True,
        "policy_version":POLICY_VERSION,
    }
    sk = kms.get_active_signing_key()
    token = jwt.encode(claims, sk.private_pem, algorithm="RS256",
                       headers={"kid":sk.kid})

    # Phase 10: 审计
    await audit.write({
        "event_type":"token.issue",
        "jti":claims["jti"], "sub":user.sub,
        "act":orchestrator.agent_id, "aud":claims["aud"],
        "scope":effective, "purpose":req.purpose,
        "plan_id":req.plan_id, "task_id":req.task_id,
        "trace_id":req.trace_id, "policy_version":POLICY_VERSION,
        "dpop_jkt":dpop_jkt, "ip":req.client_ip, "ts":now_s,
    })

    return {"access_token":token, "issued_token_type":"urn:ietf:params:oauth:token-type:jwt",
            "token_type":"DPoP", "expires_in":120, "jti":claims["jti"],
            "policy_version":POLICY_VERSION, "audit_id":claims.get("audit_id")}
```

交集算法:
```python
def intersect(callee_caps, user_perms, requested):
    result = []
    for (action, resource) in requested:
        callee_ok = any(cap_match(c, action, resource) for c in callee_caps)
        user_ok   = any(cap_match(p, action, resource) for p in user_perms)
        if callee_ok and user_ok:
            result.append(f"{action}:{resource}")
    return result

def cap_match(cap, action, resource):
    return cap.action == action and glob.fnmatch(resource, cap.resource_pattern)
```

上下文约束:
```python
def apply_context(scope, ctx):
    if is_write_action_any(scope) and ctx["time"].hour < 6: return []
    if recent_count(ctx["user"], window=60) > 200: return []
    if ctx["client_ip"] not in ALLOWED_SOURCE_NETS: return []
    return scope
```

## 6. Client Assertion 验证 (RFC 7523)

```python
async def verify_client_assertion(assertion_jwt: str) -> AgentIdentity:
    # 1. header → kid
    header = jwt.get_unverified_header(assertion_jwt)
    kid = header["kid"]

    # 2. load Agent pubkey
    agent_row = await db.get_agent_by_kid(kid)
    if not agent_row: raise Invalid("unknown_kid")
    if agent_row.status != "active": raise Invalid("agent_disabled")

    # 3. verify signature + claims
    claims = jwt.decode(
        assertion_jwt, key=agent_row.public_jwk,
        algorithms=[agent_row.alg],
        audience="https://idp.local/token/exchange",
        issuer=agent_row.agent_id,
        leeway=30,
    )

    # 4. exp-iat ≤ 60s
    if claims["exp"] - claims["iat"] > 60:
        raise Invalid("assertion_too_long")

    # 5. sub == iss (self-issued)
    if claims["sub"] != claims["iss"]:
        raise Invalid("sub_iss_mismatch")

    # 6. 防重放
    if await redis.set(f"assertion:jti:{claims['jti']}", 1, nx=True, ex=120) is None:
        raise Invalid("assertion_replay")

    # 7. 撤销检查
    if await redis.sismember("revoked:agents", agent_row.agent_id):
        raise Invalid("agent_revoked")

    return AgentIdentity(agent_id=agent_row.agent_id, kid=kid, jti=claims["jti"])
```

## 7. 撤销机制实现

```python
KEY_MAP = {
    "jti":"revoked:jtis", "sub":"revoked:subs",
    "agent":"revoked:agents", "trace":"revoked:traces",
    "plan":"revoked:plans", "chain":"revoked:chains",
}

async def revoke(req):
    check_admin_or_service_auth(req)
    key = KEY_MAP[req.type]
    ttl = req.ttl_sec or default_ttl(req.value)
    await redis.sadd(key, req.value)
    await redis.expire_member(key, req.value, ttl)  # Redis 7 per-member TTL
    await redis.publish("revoke", json.dumps({
        "type":req.type, "value":req.value, "ttl":ttl,
        "event_id":new_ulid(),
    }))
    await audit.write({"event_type":"token.revoke", **req.__dict__})
    return {"revoked":True, "effective_at":now()}
```

## 8. 密钥管理 (KMS)

三类密钥:
| 密钥 | 用途 | 算法 | 轮换 |
|---|---|---|---|
| IdP 签名密钥 | 签 delegated token / user access token | RS256 (RSA-2048) | 90 天 |
| Agent 私钥 | 签 client assertion | RS256 / EdDSA | 180 天 |
| IdP TLS 证书 | mTLS | 自签 CA | 365 天 |

轮换:
```
t=0    : key_v1 active, JWKS=[v1]
t=rotate: gen key_v2 → JWKS=[v1,v2] (双公钥期，verifier 10min 内缓存刷新)
t=+15m  : 新 token 签 v2; 旧 token 仍可验
t=+TTL_max (120s): v1 再无活跃 token → JWKS=[v2]，归档 v1
```

存储:
- **演示**: `services/idp/kms/keys/` 每文件 `age` 加密，key 来自 `IDP_KMS_PASSPHRASE`
- **生产**: HashiCorp Vault Transit Engine

目录:
```
services/idp/kms/
├── keys/
│   ├── idp_sign/
│   │   ├── current/ (enc with age)
│   │   └── previous/
│   └── agent_pub/    # 公钥缓存 (SQLite 真源)
├── rotator.py
└── store.py
```

## 9. 存储模型

| 存储 | 内容 | 访问 |
|---|---|---|
| **KMS (文件+age / Vault)** | Agent 私钥？否。仅 IdP 签名密钥 current+previous | 仅 IdP 进程 |
| **Redis** | `assertion:jti:<j>` TTL 120s, `revoked:*` set 成员 TTL, `rate:idp:<agent>:<path>` bucket | 毫秒级 |
| **SQLite (WAL)** | `agents` (id,role,kid,public_jwk,status,registered_at), `users` (permissions), `jwks_rotation` (历史), `audit` (全事件) | 写多读少 |

SQLite schema 关键表:
```sql
CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  role TEXT CHECK(role IN ('orchestrator','executor')),
  kid TEXT UNIQUE,
  public_jwk JSON,
  alg TEXT,
  status TEXT DEFAULT 'active',
  registered_at TEXT,
  registered_by TEXT
);

CREATE TABLE users (
  user_id TEXT PRIMARY KEY,
  permissions JSON,
  updated_at TEXT
);

CREATE TABLE audit (
  event_id TEXT PRIMARY KEY,
  event_type TEXT,
  trace_id TEXT,
  plan_id TEXT,
  task_id TEXT,
  sub TEXT, act TEXT, aud TEXT,
  decision TEXT,
  deny_reasons JSON,
  payload JSON,
  ts INTEGER
);
CREATE INDEX idx_audit_trace ON audit(trace_id);
CREATE INDEX idx_audit_plan  ON audit(plan_id);
CREATE INDEX idx_audit_ts    ON audit(ts);
```

## 10. 审计事件 (IdP 专属)

| event_type | payload 关键字段 |
|---|---|
| `agent.register` | agent_id, kid, admin, sod_check_ok |
| `agent.rotate` | agent_id, new_kid, old_kid |
| `agent.revoke` | agent_id, reason |
| `user.login` | user, ip, ua |
| `token.issue` | jti, sub, act, aud, scope, plan_id, task_id, trace_id, dpop_jkt |
| `token.issue.deny` | reason, request |
| `plan.validate` | plan_id, overall, per_task |
| `token.revoke` | type, value, reason, triggered_by |
| `key.rotate` | new_kid, old_kid |
| `assertion.replay_blocked` | agent, jti |

所有事件串 `audit_id` + `trace_id` 可跨组件追溯。

## 11. 性能目标

| 操作 | p50 | p99 |
|---|---|---|
| Token Exchange | < 15ms | < 50ms |
| Plan Validate (≤ 10 tasks) | < 50ms | < 150ms |
| Revoke | < 10ms | < 30ms |
| JWKS (缓存) | < 2ms | < 5ms |
| Assertion verify | < 5ms | < 15ms |

## 12. 安全加固

| 威胁 | 防御 |
|---|---|
| IdP 签名密钥泄漏 | KMS age 加密 + 进程内存驻留 / Vault；定期轮换；审计密钥使用 |
| assertion 劫持重放 | jti SETNX + TTL ≤ 60s + DPoP 绑定 |
| DPoP proof 重放 | jti SETNX + htu/htm/iat ±60s |
| user token 盗用 | user token 本身可选 DPoP；短 TTL；撤销 |
| Agent 注册滥发 | admin token + 审计 + SoD CI 校验 |
| `/revoke` 滥用 | admin/service token；所有撤销审计 |
| Scope 爆炸 | 动态交集 + 白名单 action/resource 严格 |
| OPA 不可达 | fail-closed 503 |
| 时钟偏移 | NTP + leeway 30s |
| SQL 注入/path traversal | 参数化查询 + pydantic 校验 |
| TLS 降级 | 强制 HTTPS + HSTS；内网 mTLS |

## 13. 模块文件映射

```
services/idp/
├── main.py                           # FastAPI app + middleware
├── config.py
├── oidc/
│   ├── authorize.py                  # /oidc/authorize (PKCE)
│   ├── token.py                      # /oidc/token
│   ├── userinfo.py
│   ├── refresh.py
│   └── session.py
├── token_exchange/
│   ├── handler.py                    # HTTP 入口
│   ├── assertion.py                  # verify_client_assertion
│   ├── subject_token.py
│   ├── intent.py                     # parse_scope + ENUM/REGEX
│   ├── delegation.py                 # whitelist / accept_from
│   ├── executor.py                   # executor_map 校验
│   ├── intersect.py
│   ├── context.py                    # apply_context
│   └── signer.py                     # RS256 签
├── plan/
│   ├── validate.py
│   └── opa_client.py
├── revoke/
│   ├── handler.py
│   └── pubsub.py
├── kms/
│   ├── store.py                      # age / Vault adapter
│   ├── rotator.py
│   └── keys/
├── jwks/
│   ├── handler.py
│   └── cache.py
├── dpop/
│   └── validator.py
├── agents/
│   ├── register.py
│   ├── rotate.py
│   ├── loader.py                     # 读 capabilities/*.yaml
│   └── sod_check.py                  # 交集 = ∅ 校验
├── users/
│   ├── loader.py
│   └── perms.py
├── audit/
│   ├── writer.py                     # async batch → SQLite
│   └── schema.sql
├── storage/
│   ├── redis.py
│   └── sqlite.py
└── errors.py
```

## 14. 启动与初始化

`start.sh` IdP 部分:
1. 生成 IdP 根 CA (若不存在)
2. 生成 IdP 签名密钥 v1
3. 读 `capabilities/*.yaml` → SoD check，失败即退
4. 为每 Agent 生成密钥对，输出 `secrets/<agent>.pem`
5. 公钥写 SQLite `agents` 表
6. 启动 FastAPI (uvicorn)
7. `/healthz` 就绪后返回

## 15. 与其他模块契约

- Gateway: Gateway 调 `/jwks` 缓存公钥；**不**代 Agent 调 `/token/exchange` (由 orchestrator agent 自签 assertion 调)
- OPA: IdP 在 `/plan/validate` 调 `POST /v1/data/agent/authz/plan_allow`
- Anomaly: Anomaly 服务 token 调 `/revoke`
- Admin CLI: 调 `/agents/register`、`/agents/{id}/rotate-key`、`/admin/reload`、`/revoke`
- Redis Pub/Sub: 通道 `revoke`、`policy_reload`、`agent_register`、`agent_rotate`
