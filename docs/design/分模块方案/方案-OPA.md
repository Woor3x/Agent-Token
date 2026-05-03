# OPA (PDP 策略决策点) — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。10 条 Rego 规则，含 one_time / executor_map / delegation / depth / not_revoked / dpop_bound / context_ok。**纯白名单**，无 reject_from。

## 1. 组件职责

OPA = 授权决策引擎，与业务逻辑完全解耦:
- 接 Gateway / IdP 的 `input` (token + intent + target + context)
- 执行 Rego 策略返回 `{allow, reasons, policy_version}`
- 数据 (executor_map.json / agents.json / users.json / revoked.json) 由 bundle 或 Data API 热加载
- 提供单次决策 `allow` + DAG 批量决策 `plan_allow` 两端点

OPA 不直接访问 Redis；撤销状态由 Gateway AuthN 层查后注入 input (二次确认)。

## 2. 架构定位

```
Gateway (PEP)                                 IdP (Plan Validate)
    │                                              │
    │  POST /v1/data/agent/authz/allow             │  POST /v1/data/agent/authz/plan_allow
    │  { input: { token, intent, target, context }}│  { input: { orch_token, user, plan, context }}
    ▼                                              ▼
┌──────────────────────────────────────────────────────┐
│                    OPA Server                         │
│  ┌───────────────────────────────────────────┐       │
│  │  Rego 策略 (policies/)                    │       │
│  │  ├─ a2a.rego          (主规则, 10 条)     │       │
│  │  ├─ delegation.rego   (act 链/深度/环)   │       │
│  │  ├─ plan.rego         (DAG 批量)         │       │
│  │  └─ helpers.rego      (glob/regex)       │       │
│  └───────────────────────────────────────────┘       │
│  ┌───────────────────────────────────────────┐       │
│  │  Data (data/)                             │       │
│  │  ├─ executor_map.json                     │       │
│  │  ├─ agents.json                           │       │
│  │  ├─ users.json                            │       │
│  │  └─ revoked.json (可选同步)               │       │
│  └───────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────┘
    │
    │  { result: { allow, reasons, policy_version } }
    ▼
Gateway / IdP
```

## 3. HTTP API

### 3.1 `POST /v1/data/agent/authz/allow` (P0, 核心)

单次 A2A 鉴权决策。

Body:
```json
{
  "input": {
    "token": {
      "iss":"https://idp.local",
      "sub":"user:alice",
      "act":{"sub":"doc_assistant","act":null},
      "aud":"agent:data_agent",
      "scope":["feishu.bitable.read:app_token:.../table:tbl_q1"],
      "purpose":"...",
      "plan_id":"...", "task_id":"...",
      "trace_id":"...",
      "jti":"tok-uuid","exp":1714000120,"nbf":1714000000,
      "cnf":{"jkt":"..."},
      "one_time":true,
      "policy_version":"v1.2.0"
    },
    "intent": { "action":"feishu.bitable.read", "resource":"app_token:.../table:tbl_q1" },
    "target_agent": "data_agent",
    "context": { "time":1714000000, "source_ip":"10.0.0.1", "recent_calls":3, "delegation_depth":1 }
  }
}
```

响应 200 (allow):
```json
{ "result": { "allow":true, "reasons":[], "policy_version":"v1.2.0" } }
```

响应 200 (deny):
```json
{ "result": { "allow":false, "reasons":["executor_mismatch","scope_exceeded"], "policy_version":"v1.2.0" } }
```

### 3.2 `POST /v1/data/agent/authz/plan_allow` (P1)

DAG 批量决策 (IdP `/plan/validate` 调用)。

Body:
```json
{
  "input": {
    "orchestrator": { "agent_id":"doc_assistant", "caps":[...] },
    "user": { "sub":"user:alice", "perms":[...] },
    "plan": [
      { "id":"t1","agent":"data_agent","action":"feishu.bitable.read","resource":"app_token:.../table:tbl_q1" },
      { "id":"t2","agent":"web_agent","action":"web.search","resource":"*" },
      { "id":"t3","agent":"doc_assistant","action":"feishu.doc.write","resource":"doc_token:weekly","deps":["t1","t2"] }
    ],
    "context": { "time":..., "delegation_depth":1 }
  }
}
```

响应 200:
```json
{
  "result": {
    "overall":"allow",
    "per_task":[
      { "id":"t1","allow":true,"reasons":[] },
      { "id":"t2","allow":true,"reasons":[] },
      { "id":"t3","allow":true,"reasons":[] }
    ],
    "policy_version":"v1.2.0"
  }
}
```

### 3.3 `PUT /v1/data/executor_map` (P0)

Body: `executor_map.json` 完整内容

响应 204: 热更新。

### 3.4 `PUT /v1/data/agents` (P0)

Body: `agents.json`

响应 204。

### 3.5 `PUT /v1/data/users` (P0)

Body: `users.json`

响应 204。

### 3.6 `PUT /v1/data/revoked` (P0)

Body: `{ "jtis":[...], "subs":[...], "agents":[...], "traces":[...], "plans":[...], "chains":[...] }`

响应 204。由 IdP 撤销时同步推 (也可 Gateway 先查 Redis 注入 input)。

### 3.7 `GET /healthz` (P0)

OPA 内置端点。

## 4. Reason 映射

| reason (OPA 输出) | HTTP code (Gateway/IdP) |
|---|---|
| `token_invalid` | `AUTHN_TOKEN_INVALID` |
| `audience_mismatch` | `AUTHZ_AUDIENCE_MISMATCH` |
| `scope_exceeded` | `AUTHZ_SCOPE_EXCEEDED` |
| `executor_mismatch` | `AUTHZ_EXECUTOR_MISMATCH` |
| `delegation_rejected` | `AUTHZ_DELEGATION_REJECTED` |
| `depth_exceeded` | `AUTHZ_DEPTH_EXCEEDED` |
| `revoked` | `AUTHN_REVOKED` |
| `context_denied` | `CONTEXT_DENIED` |
| `not_one_time` | `AUTHN_TOKEN_INVALID` |
| `dpop_unbound` | `AUTHN_DPOP_INVALID` |

## 5. Rego 策略

### 5.1 主策略 `policies/a2a.rego`

```rego
package agent.authz

import future.keywords.if
import future.keywords.in

default allow := false
default reasons := []
policy_version := "v1.2.0"

# 1. Token 语义 (IdP 已验签，OPA 验时间 + 声明)
token_valid if {
    input.token.iss == "https://idp.local"
    input.token.exp > time.now_ns() / 1e9
    input.token.nbf <= time.now_ns() / 1e9
}

# 2. 一次性声明 (Gateway 负责 SETNX 销毁，这里只查声明位)
one_time_declared if {
    input.token.one_time == true
    input.token.jti != ""
}

# 3. Audience 绑定
audience_match if {
    input.token.aud == sprintf("agent:%s", [input.target_agent])
}

# 4. Scope 覆盖意图
scope_covers if {
    required := sprintf("%s:%s", [input.intent.action, input.intent.resource])
    some s in input.token.scope
    glob_match_scope(s, required)
}

glob_match_scope(granted, requested) if {
    glob.match(granted, [":","/","*"], requested)
}

# 5. 单执行者 (executor_map)
executor_valid if {
    data.executor_map[input.intent.action] == input.target_agent
}

# 6. 委托白名单 (纯白名单 accept_from)
delegation_accepted if {
    caller := input.token.act.sub
    data.agents[input.target_agent].delegation.accept_from[_] == caller
}

# 7. 委托链深度
delegation_depth_ok if {
    chain := actor_chain(input.token.act)
    count(chain) <= data.agents[input.target_agent].delegation.max_depth
}

actor_chain(act) := chain if {
    act == null
    chain := []
} else := chain if {
    act != null
    sub_chain := actor_chain(act.act)
    chain := array.concat([act.sub], sub_chain)
}

# 8. 未撤销 (二次确认；Gateway 先 Redis 查)
not_revoked if {
    not data.revoked.jtis[input.token.jti]
    not data.revoked.subs[input.token.sub]
    not data.revoked.traces[input.token.trace_id]
    not data.revoked.plans[input.token.plan_id]
    not data.revoked.agents[input.target_agent]
}

# 9. DPoP 绑定 (Gateway 已签名校验，OPA 确认 cnf 存在)
dpop_bound if {
    input.token.cnf.jkt != ""
}

# 10. 上下文约束
context_ok if {
    not rate_limit_exceeded
    not out_of_hours_write
}

rate_limit_exceeded if {
    max_rpm := data.agents[input.target_agent].capabilities[_].constraints.max_calls_per_minute
    input.context.recent_calls > max_rpm
}

out_of_hours_write if {
    startswith(input.intent.action, "feishu.doc.write")
    hour := time.clock(time.now_ns())[0]
    hour < 6
}

# 综合
allow if {
    token_valid
    one_time_declared
    audience_match
    scope_covers
    executor_valid
    delegation_accepted
    delegation_depth_ok
    not_revoked
    dpop_bound
    context_ok
}

# Deny 原因收集
reasons contains "token_invalid"       if not token_valid
reasons contains "not_one_time"        if not one_time_declared
reasons contains "audience_mismatch"   if not audience_match
reasons contains "scope_exceeded"      if not scope_covers
reasons contains "executor_mismatch"   if not executor_valid
reasons contains "delegation_rejected" if not delegation_accepted
reasons contains "depth_exceeded"      if not delegation_depth_ok
reasons contains "revoked"             if not not_revoked
reasons contains "dpop_unbound"        if not dpop_bound
reasons contains "context_denied"      if not context_ok
```

### 5.2 委托链辅助 `policies/delegation.rego`

```rego
package agent.delegation

import future.keywords.in

# 环检测
has_cycle(act) if {
    ids := collect_ids(act)
    count(ids) != count({x | x := ids[_]})
}

collect_ids(act) := [] if { act == null }
collect_ids(act) := ids if {
    act != null
    sub := collect_ids(act.act)
    ids := array.concat([act.sub], sub)
}
```

### 5.3 DAG 批量 `policies/plan.rego`

```rego
package agent.authz

# 对 plan 中每 task 模拟 allow (不带 token，只查 executor_map + caps + perms)
plan_allow := result if {
    tasks := input.plan
    per_task := [ {"id": t.id, "allow": task_ok(t), "reasons": task_reasons(t)} | t := tasks[_] ]
    overall := "allow"
    count([1 | p := per_task[_]; not p.allow]) == 0
    result := {"overall": overall, "per_task": per_task, "policy_version": policy_version}
}

plan_allow := result if {
    tasks := input.plan
    per_task := [ {"id": t.id, "allow": task_ok(t), "reasons": task_reasons(t)} | t := tasks[_] ]
    count([1 | p := per_task[_]; not p.allow]) > 0
    result := {"overall":"deny","per_task":per_task,"policy_version":policy_version}
}

task_ok(t) if {
    data.executor_map[t.action] == t.agent
    some cap in data.agents[t.agent].capabilities
    cap.action == t.action
    glob.match(cap.resource_pattern, ["/",":"], t.resource)
    some p in data.users[input.user.sub].permissions
    p.action == t.action
    glob.match(p.resource_pattern, ["/",":"], t.resource)
}

task_reasons(t) := rs if {
    rs := array.concat(
        [ "executor_mismatch" | data.executor_map[t.action] != t.agent ],
        array.concat(
            [ "scope_exceeded" | not any_cap_covers(t) ],
            [ "user_denied" | not any_user_perm_covers(t) ]
        )
    )
}

any_cap_covers(t) if {
    some cap in data.agents[t.agent].capabilities
    cap.action == t.action
    glob.match(cap.resource_pattern, ["/",":"], t.resource)
}

any_user_perm_covers(t) if {
    some p in data.users[input.user.sub].permissions
    p.action == t.action
    glob.match(p.resource_pattern, ["/",":"], t.resource)
}
```

### 5.4 辅助 `policies/helpers.rego`

```rego
package agent.helpers

scope_covers(granted, requested) if {
    glob.match(granted, [":","/","*"], requested)
}

resource_prefix_match(pattern, resource) if {
    startswith(resource, trim_suffix(pattern, "*"))
}
```

## 6. 数据文件

### 6.1 `data/executor_map.json`

```json
{
  "executor_map": {
    "feishu.bitable.read":   "data_agent",
    "feishu.contact.read":   "data_agent",
    "feishu.calendar.read":  "data_agent",
    "feishu.doc.write":      "doc_assistant",
    "web.search":            "web_agent",
    "web.fetch":             "web_agent"
  }
}
```

### 6.2 `data/agents.json` (纯白名单)

```json
{
  "agents": {
    "doc_assistant": {
      "role": "orchestrator",
      "capabilities": [
        { "action":"feishu.doc.write","resource_pattern":"doc_token:*" },
        { "action":"a2a.invoke","resource_pattern":"agent:data_agent|agent:web_agent" }
      ],
      "delegation": { "accept_from":["user"], "max_depth":1 }
    },
    "data_agent": {
      "role": "executor",
      "capabilities": [
        { "action":"feishu.bitable.read","resource_pattern":"app_token:*/table:*","constraints":{"max_rows_per_call":1000,"max_calls_per_minute":60} },
        { "action":"feishu.contact.read","resource_pattern":"department:*" },
        { "action":"feishu.calendar.read","resource_pattern":"calendar:*" }
      ],
      "delegation": { "accept_from":["doc_assistant"], "max_depth":3 }
    },
    "web_agent": {
      "role": "executor",
      "capabilities": [
        { "action":"web.search","resource_pattern":"*" },
        { "action":"web.fetch","resource_pattern":"https://*" }
      ],
      "delegation": { "accept_from":["doc_assistant"], "max_depth":2 }
    }
  }
}
```

**注**: 无 `reject_from` 字段。

### 6.3 `data/users.json`

```json
{
  "users": {
    "user:alice": {
      "name":"Alice","department":"sales",
      "permissions": [
        { "action":"feishu.doc.write",    "resource_pattern":"doc_token:*" },
        { "action":"feishu.bitable.read", "resource_pattern":"app_token:bascn_alice/*" },
        { "action":"feishu.contact.read", "resource_pattern":"department:sales" },
        { "action":"web.search",          "resource_pattern":"*" }
      ]
    }
  }
}
```

### 6.4 `data/revoked.json` (可选)

```json
{
  "revoked": {
    "jtis": {}, "subs": {}, "agents": {}, "traces": {}, "plans": {}, "chains": {}
  }
}
```

> 推荐 Gateway 先 Redis 查后注入 input，OPA data 仅作二次确认。IdP `/revoke` 同时同步到 OPA (Pub/Sub 订阅者 push PUT)。

## 7. 部署

```yaml
# docker-compose.yml
opa:
  image: openpolicyagent/opa:0.63.0
  command:
    - run
    - --server
    - --log-level=info
    - --config-file=/config/config.yaml
    - /policies
    - /data
  volumes:
    - ./services/opa/policies:/policies
    - ./services/opa/data:/data
    - ./services/opa/config.yaml:/config/config.yaml
  ports:
    - "8181:8181"
```

`config.yaml`:
```yaml
services:
  - name: gateway
    url: http://gateway.local:8000

bundles:
  authz:
    resource: /bundles/authz.tar.gz
    polling:
      min_delay_seconds: 10
      max_delay_seconds: 30

decision_logs:
  console: false
```

## 8. Gateway / IdP 调 OPA (代码)

```python
# services/gateway/authz/opa_client.py
import httpx

OPA_URL = "http://opa.local:8181/v1/data/agent/authz"

async def decide(token, intent, target, context):
    payload = {"input":{
        "token":token,"intent":intent,
        "target_agent":target,"context":context,
    }}
    try:
        async with httpx.AsyncClient(timeout=0.005) as c:
            r = await c.post(f"{OPA_URL}/allow", json=payload)
            r.raise_for_status()
            res = r.json()["result"]
            return res["allow"], res.get("reasons",[]), res.get("policy_version","")
    except Exception:
        return False, ["opa_unavailable"], ""
```

IdP DAG 预审:
```python
# services/idp/plan/opa_client.py
async def plan_decide(orch, user, plan, context):
    payload = {"input":{
        "orchestrator":orch, "user":user,
        "plan":plan, "context":context,
    }}
    async with httpx.AsyncClient(timeout=0.050) as c:
        r = await c.post(f"{OPA_URL}/plan_allow", json=payload)
        return r.json()["result"]
```

## 9. 热加载

策略文件: 修改 `policies/*.rego` 后 OPA bundle 轮询 (10-30s) 自动重载。

数据文件: 三种途径之一:
1. 直接 `PUT /v1/data/<path>` (admin token)
2. IdP `/admin/reload` 触发 → IdP 推到 OPA
3. bundle 重新打包 → OPA 下次轮询拉

撤销同步: IdP Pub/Sub `revoke` 通道 → OPA sidecar 订阅 → `PUT /v1/data/revoked/<type>/<value>`。

## 10. 单元测试

```rego
# policies/a2a_test.rego
package agent.authz_test

import data.agent.authz

test_allow_doc_to_data {
    authz.allow with input as {
        "token": {
            "iss":"https://idp.local","sub":"user:alice",
            "act":{"sub":"doc_assistant","act":null},
            "aud":"agent:data_agent",
            "scope":["feishu.bitable.read:app_token:bascn/table:tbl_q1"],
            "plan_id":"p1","task_id":"t1","trace_id":"tr1",
            "jti":"tok1","exp":9999999999,"nbf":0,
            "cnf":{"jkt":"abc"},"one_time":true
        },
        "intent":{"action":"feishu.bitable.read","resource":"app_token:bascn/table:tbl_q1"},
        "target_agent":"data_agent",
        "context":{"recent_calls":0}
    }
    with data.executor_map as {"feishu.bitable.read":"data_agent"}
    with data.agents as {
        "data_agent":{
            "delegation":{"accept_from":["doc_assistant"],"max_depth":3},
            "capabilities":[{"action":"feishu.bitable.read","resource_pattern":"app_token:*/table:*","constraints":{"max_calls_per_minute":60}}]
        }
    }
    with data.revoked as {"jtis":{},"subs":{},"agents":{},"traces":{},"plans":{}}
}

test_deny_executor_mismatch {
    not authz.allow with input as {
        "token":{"iss":"https://idp.local","act":{"sub":"doc_assistant","act":null},
                 "aud":"agent:web_agent","scope":["feishu.bitable.read:x"],
                 "jti":"t","exp":9e9,"nbf":0,"cnf":{"jkt":"a"},"one_time":true},
        "intent":{"action":"feishu.bitable.read","resource":"x"},
        "target_agent":"web_agent",
        "context":{}
    }
    with data.executor_map as {"feishu.bitable.read":"data_agent"}
    # ...
}

test_deny_delegation_rejected {
    not authz.allow with input as {
        "token":{"act":{"sub":"web_agent","act":null},"aud":"agent:data_agent",...},
        "target_agent":"data_agent",...
    }
    with data.agents as {"data_agent":{"delegation":{"accept_from":["doc_assistant"],"max_depth":3},"capabilities":[...]}}
}

test_deny_depth_exceeded {
    not authz.allow with input as {
        "token":{"act":{"sub":"a1","act":{"sub":"a2","act":{"sub":"a3","act":{"sub":"a4","act":null}}}}, ...},
        "target_agent":"data_agent",...
    }
    with data.agents as {"data_agent":{"delegation":{"accept_from":["a1"],"max_depth":3},...}}
}
```

运行: `opa test services/opa/policies/ -v`

## 11. 模块文件映射

```
services/opa/
├── config.yaml
├── policies/
│   ├── a2a.rego              # 10 核心规则
│   ├── delegation.rego       # 环检测 / 深度
│   ├── plan.rego             # DAG 批量
│   ├── helpers.rego
│   └── *_test.rego           # 单元测试
└── data/
    ├── executor_map.json
    ├── agents.json
    ├── users.json
    └── revoked.json
```

## 12. 性能目标

| 指标 | 目标 |
|---|---|
| 单次决策 p50 | < 1ms |
| 单次决策 p99 | < 5ms |
| plan_allow (10 tasks) p99 | < 30ms |
| OPA QPS | ≥ 5000 |
| 数据热加载延迟 | ≤ 30s (bundle) / 秒级 (Data API) |

## 13. 契约

| 调用方 → OPA | 端点 | 认证 |
|---|---|---|
| Gateway | `/v1/data/agent/authz/allow` | 内网 + shared secret |
| IdP | `/v1/data/agent/authz/plan_allow` | 同 |
| Admin / IdP 同步器 | `PUT /v1/data/*` | admin token |

| OPA → 外部 | 说明 |
|---|---|
| (无) | OPA 不主动出站 |
