# M2 Gateway 联调报告

> 范围：把上游 M2 (`services/gateway`) 真网关并入 M1 IdP + OPA + Redis + M4 agents + M5 mocks 全栈，替换原 M5 `gateway-mock`，完成端到端联调。
>
> 日期：2026-04-25
> 分支：`master`
> 上游来源：https://github.com/Woor3x/Agent-Token (M2 子目录)

---

## 1. 集成动作

### 1.1 代码合入
- 拉取 M2 仓库 → 复制 `services/gateway/` 完整目录到本仓
- M2 自带的 OPA 策略（`services/opa/policies/{a2a,delegation}.rego`）保留 **本仓 M1 已修复版本**（M2 仍含 `_actor_chain` 自递归，OPA 0.63 拒绝；M1 已用 `walk()` 改写）
- M2 自带 `docker-compose.yml` 不并入 —— 改为在主 compose 中加 `gateway` service

### 1.2 容器编排
`docker-compose.yml` 关键变更：
| 项 | 变更 |
|---|---|
| `x-agent-env.GATEWAY_URL` | `http://gateway-mock:9200` → `http://gateway:9200` |
| `gateway-mock` 服务 | 删除 |
| `gateway` 服务 | 新增。`build: ./services/gateway`，全量 `GW_*` 环境变量 |
| `volumes` | 新增 `gateway_data`（审计 SQLite 持久化） |
| `gateway` 依赖 | `idp/redis/opa healthy + 三个 agent started` |

`services/gateway/Dockerfile` 新建（M2 仓未提供 Dockerfile）：
- `python:3.11-slim`，`pip install -r requirements.txt`
- `ENV PYTHONPATH=/app`（关键，见 §2.1）
- `CMD uvicorn main:app --host 0.0.0.0 --port 9200`

---

## 2. 调试纪要

### 2.1 `import token` 与 stdlib 冲突
**症状**：容器启 uvicorn 即崩
```
File "/usr/local/lib/python3.11/tokenize.py", line 36, in <module>
    from token import EXACT_TOKEN_TYPES
ImportError: cannot import name 'EXACT_TOKEN_TYPES' from 'token' (/app/token/__init__.py)
```
**根因**：M2 把 DPoP / JWKS 模块放在 `services/gateway/token/` 包；`PYTHONPATH=/app` 后 stdlib `tokenize` → `token` 解析到本地包，找不到 `EXACT_TOKEN_TYPES`。
**修复**：包整体改名 `token/` → `jwttoken/`；改两个 import (`main.py`、`middleware/authn.py`)。

### 2.2 OPA 调用 URL 多余 `/allow`
**症状**：`opa unavailable: 'bool' object has no attribute 'get' — fail-closed`
**根因**：`authz/opa_client.py` 用 `_OPA_URL = f"{settings.opa_url}/allow"`，OPA `/v1/data/agent/authz/allow` 仅返回 `{result: true|false}`，代码 `result.get("allow")` 失败。
**修复**：去掉 `/allow` 后缀，查包根路径 → 返回 `{result: {allow, reasons}}`。

### 2.3 OPA 策略 vs M1 IdP 契约不一致
M2 的 Rego 假设的契约和 M1 已部署的不一致：

| 维度 | M2 假设 | M1 实际 | 修复点 |
|---|---|---|---|
| `aud` 形态 | `agent:{id}` 前缀 | bare `{id}` | `a2a.rego/audience_match` 改 bare 比较 |
| `scope` 类型 | array | RFC 6749 空格分隔字符串 | `scope_covers` 内 `split(scope, " ")` 后再迭代 |
| `executor_map` | 仅泛化 action | 缺 `orchestrate`、`a2a.invoke` 多目标 | 加 `orchestrate→doc_assistant`；新增 `executor_valid` 第二条规则：`a2a.invoke` 时从 `intent.resource = agent:<id>` 中抽 target |

`services/opa/data/agents.json` 同步：
- `doc_assistant.delegation.accept_from` 加 `web_ui`、`max_depth=2`
- 三 agent 均加 `a2a.invoke` capability (`resource_pattern: agent:*`)

### 2.4 Gateway 路径重写
M2 网关 `/a2a/invoke` 直接转给上游同名路径，但 M4 agent 接收端是 `/invoke`。
**修复**：`routing/upstream_client.py` 加 path 重写 —— `/a2a/invoke` → `/invoke`。

### 2.5 端口残留
旧 `gateway-mock` 容器占着 0.0.0.0:9200，新 gateway 启不来。
**修复**：`docker rm -f agent-token-gateway-mock-1`。

---

## 3. 验证结果

### 3.1 容器状态
```
SERVICE         STATUS
redis           Up (healthy)
opa             Up (healthy)
idp             Up (healthy)
gateway         Up
doc-assistant   Up
data-agent      Up
web-agent       Up
feishu-mock     Up
```

### 3.2 健康检查
```bash
$ curl -s http://localhost:9200/healthz
{"status":"ok","upstreams":{"redis":"ok","idp":"ok","opa":"ok"},"circuit_breakers":{}}
```

### 3.3 端到端 demo
`scripts/e2e_demo.py` 完整跑通：
1. OIDC PKCE → alice 拿 user_token (RS256)
2. Web UI orchestrator 注册（首跑生成 RSA 私钥）
3. SDK `AgentClient.invoke(target=doc_assistant, action=a2a.invoke, resource=agent:doc_assistant)`
4. SDK 走 `/token/exchange` → IdP 颁发 delegated token (sub=alice, act={sub: web_ui}, aud=doc_assistant, cnf.jkt=…)
5. SDK 签 DPoP → POST `gateway:/a2a/invoke`
6. **真 M2 网关**：JWT verify (JWKS) → 4 维撤销 → DPoP 验证 + jti replay → OPA 10 条策略 → one-shot consume → 路由 doc_assistant
7. doc_assistant 解析意图 → fan-out data_agent / web_agent / feishu-mock（各自走一遍 token-exchange + 真网关）
8. 返回 `doc_c66263bf5e4d`，latency ≈ 1.3 s

### 3.4 单元测试
`pytest tests/`：**66 passed**，0 fail。

---

## 4. M2 网关引入后的端到端链路

```
alice (browser)
   │ OIDC PKCE
   ▼
IdP (8000) ── RS256 user_token
   │
web_ui (SDK) ──┐
   │            │ token-exchange (RFC 8693, DPoP-bound)
   ▼            │
IdP (8000) ── delegated_token (one-shot, cnf.jkt)
   │
   │ POST /a2a/invoke + DPoP
   ▼
M2 Gateway (9200)
  ├ trace_middleware  (W3C traceparent)
  ├ authn_middleware  (JWKS verify → 4-dim revoke → DPoP verify → jti SETNX)
  ├ rate_limit_middleware
  ├ POST /a2a/invoke
  │   ├ delegation chain check (≤ depth 4)
  │   ├ OPA POST /v1/data/agent/authz  → {allow, reasons[]}
  │   ├ one-shot consume (Redis SADD revoked:jtis)
  │   └ upstream call (httpx) → /invoke
  └ audit (SQLite WAL)
   ▼
doc_assistant (8100) ── LangGraph orchestrate
   │
   ├─ AgentClient → IdP /token/exchange → Gateway → data_agent (8101)
   ├─ AgentClient → IdP /token/exchange → Gateway → web_agent  (8102)
   └─ AgentClient → IdP /token/exchange → Gateway → feishu-mock (9000)
```

---

## 5. 修改文件清单

新增：
- `services/gateway/`（整目录从 M2 复制）
- `services/gateway/Dockerfile`
- `docs/M2_INTEGRATION_REPORT.md`（本文件）

改名：
- `services/gateway/token/` → `services/gateway/jwttoken/`

修改：
- `docker-compose.yml`（gateway-mock → gateway）
- `services/gateway/main.py`（import 路径）
- `services/gateway/middleware/authn.py`（import 路径 + bare aud）
- `services/gateway/authz/opa_client.py`（去 `/allow` 后缀）
- `services/gateway/routing/upstream_client.py`（`/a2a/invoke` → `/invoke`）
- `services/gateway/registry.yaml`（compose 内网 URL）
- `services/opa/policies/a2a.rego`（bare aud + scope split + a2a.invoke executor）
- `services/opa/data/agents.json`（capability + accept_from）
- `services/opa/data/executor_map.json`（加 `orchestrate`）

---

## 6. 已知遗留 / 可后续优化

| # | 项 | 说明 |
|---|---|---|
| 1 | Gateway 内置 `token` 包名 | 改名 `jwttoken` 后，与 M2 后续合并需注意冲突 |
| 2 | `executor_map.json` 与 capability YAML 双源 | OPA `data/` 与 `capabilities/*.yaml` 信息重复，未来宜统一 |
| 3 | OPA 启动后修改 `data/` 需 `docker compose restart opa`，因 OPA 仅 `--watch` 文件下挂载层；可改 bundle/HTTP discovery |
| 4 | gateway 健康检查 dockerfile 未配置；compose 也无 healthcheck，仅 depends_on `service_started` |
| 5 | `audit.db` 仅落 WAL，未提供查询 CLI |
| 6 | 浏览器/UI demo 缺位（仅 CLI demo）|

---

## 7. 复现步骤
```bash
docker compose up -d --build
sleep 15
curl http://localhost:9200/healthz       # gateway
curl http://localhost:8000/healthz       # idp
python scripts/e2e_demo.py               # 端到端
pytest tests/                            # 66 passed
```
