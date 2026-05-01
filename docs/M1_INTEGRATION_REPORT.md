# M1 ↔ M4/M5 联调报告

**日期**：2026-04-25
**范围**：拉取 M1 仓（IdP + OPA + Redis + SQLite + bcrypt 用户库）→ 装入本仓 →
与已有 M4 (agents) + M5 (mocks) 联通 → docker compose 编排 → e2e 跑通。

---

## 1. 来源

| 模块 | 来源 | 落地位置 |
|------|------|---------|
| M1 IdP | https://github.com/Woor3x/Agent-Token (main) | `services/idp/` |
| M1 OPA 策略 | 同上 | `services/opa/policies/` |
| M1 OPA 数据 | 同上 | `services/opa/data/` |
| 用户/能力 YAML | 同上 | `users/`, `capabilities/` |
| M4 agents | 既有 | `agents/{doc,data,web}_agent` |
| M4 SDK | 既有 | `sdk/agent_token_sdk` |
| M5 mocks | 既有 | `services/feishu_mock`, `services/gateway_mock` |

旧的 `services/idp_mock` 已被 M1 IdP 取代（仍保留作单测 fixture）。

---

## 2. 适配点（差异 + 修复）

| 差异 | 影响 | 修复 |
|------|------|------|
| M1 client_assertion 要求 `iss==sub==<bare agent_id>`（无 `agent:` 前缀） | SDK 老逻辑写 `agent:<id>` 被 IdP 拒签 | `sdk/agent_token_sdk/assertion.py`：sign() 前 strip `agent:` 前缀 |
| M1 client_assertion 校验 `aud == {issuer}/token/exchange`（逻辑 URL） | SDK 之前用真实 http URL 当 aud | `AgentClient` 新增 `assertion_audience` 参数 + `IDP_ASSERTION_AUDIENCE` env，默认 `https://idp.local/token/exchange` |
| M1 签发的 delegated token `aud = <bare target_agent_id>` | agent server 默认期望 `agent:<id>` | `agents/common/server.py`：`MOCK_AUTH=true` 仍用前缀；否则 bare |
| M1 `scope` 字段是空格分隔 string | `VerifiedClaims` 期望 list | `agents/common/auth.py`：兼容 str / list |
| 多跳需把 user_token 透传到下游 agent 才能再做 token-exchange | 否则下游用自己 delegated token 当 subject_token，aud 校验失败 | SDK invoke() 自动加 `X-Subject-Token: <on_behalf_of>`；Gateway forward；agent server 把它塞进 `body.context.subject_token`；handler 优先取它 |
| OPA 0.63 禁止用户规则自递归（`actor_chain(act) := f(actor_chain(act.act))`） | M1 policy 直接启动失败 | `delegation.rego` / `a2a.rego`：用 `walk()` 内置代替递归 |
| OPA 0.63 的 `default reasons := set()` 与 `reasons contains ...` 冲突 | OPA 启动失败 | 删 default 行（partial set 自动初始化为 `set()`） |
| OPA 镜像无 `wget`，无法做 healthcheck | depends_on 永远 unhealthy | 改用 `/opa eval -f raw 1+1` |
| docker volume 把 `./users:/app/users:ro` 覆盖 IdP 自身 `users` Python 包 | `ModuleNotFoundError: users.loader` | mount 路径改 `/data/users`，env `USERS_DIR=/data/users`；`capabilities` 同理 |
| `passlib[bcrypt]==1.7.4` 与 `bcrypt>=4` 不兼容（detect_wrap_bug 抛异常） | IdP startup 崩 | requirements 加 `bcrypt<4` |
| agent 容器需 RS256 私钥才能跟 M1 谈 | 运行时无密钥 | 写 `scripts/agent_entrypoint.sh` + `agents/common/bootstrap_register.py`：首启时 `POST /agents/register`，私钥落 `/app/keys/<id>/` 卷 |
| doc_assistant 入口改为 `a2a.invoke agent:doc_assistant` | 旧 `orchestrate` 仍要支持 | handler 同时识别两种 action |

---

## 3. 端口与服务（compose）

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| `redis` | redis:7.2-alpine | 6379 | 一次性 jti / 撤销 / 登录 session |
| `opa` | openpolicyagent/opa:0.63.0 | 8181 | 策略决策 |
| `idp` | agent-token-idp（本地构建） | 8000 | OIDC + RFC 7521/8693/9449 + admin |
| `feishu-mock` | agent-token/agent-base | 9000 | 假飞书 |
| `gateway-mock` | 同上 | 9200 | 转发 a2a/invoke |
| `doc-assistant` | 同上 | 8100 | 编排 agent |
| `data-agent` | 同上 | 8101 | 飞书读 |
| `web-agent` | 同上 | 8102 | 联网搜 |

卷：`idp_data`, `idp_kms`, `{doc,data,web}_assistant_keys`。

---

## 4. 关键 env

```
IDP_ISSUER=https://idp.local
IDP_URL=http://idp:8000
IDP_JWKS_URL=http://idp:8000/jwks
IDP_TOKEN_EXCHANGE_URL=http://idp:8000/token/exchange
ADMIN_TOKEN=admin-secret-token       # /agents/register 鉴权
GATEWAY_URL=http://gateway-mock:9200
MOCK_AUTH=false                      # 走真 IdP，签 RS256
VERIFY_DPOP=false                    # 演示便捷；M1 仍接收 DPoP 头
POLICY_VERSION=v1.2.0
LLM_PROVIDER=mock                    # 可切 ark/openai
```

`.env.example` 已含上述项。

---

## 5. 调用链（实测）

```
[user (alice/alice123)]
        │  GET /oidc/authorize  (PKCE S256)
        ▼
[idp:8000]  HTML login → POST /oidc/login → 302 ?code=
        │  POST /oidc/token (code+verifier)
        ▼
user_token (RS256, aud="web-ui", scope="openid profile agent:invoke")

[scripts/e2e_demo.py 充当 web_ui 编排者]
        │  首启：POST /agents/register（admin）→ 私钥落 .demo_keys/web_ui/
        │  AgentClient.invoke(target=doc_assistant, on_behalf_of=user_token)
        ▼
[idp:8000] POST /token/exchange
        ├─ 校验 client_assertion（web_ui 公钥 / kid / aud=issuer+/token/exchange）
        ├─ 校验 subject_token（aud=web-ui, scope contains agent:invoke）
        ├─ OPA 决策（capability ∩ user_perm ∩ requested scope）
        ├─ 签 delegated token (aud=doc_assistant, act=web_ui)
        ▼
delegated_doc_token  (+ DPoP proof bound to gateway URL)

[gateway-mock:9200] POST /a2a/invoke   X-Target-Agent: doc_assistant
        │  转发 Authorization + DPoP + X-Subject-Token + 跟踪头
        ▼
[doc-assistant:8100] POST /invoke
        ├─ verify delegated_doc_token via JWKS
        ├─ planner → DAG: data_agent.read → doc_assistant.feishu.doc.write
        ├─ HttpSdkClient（用自己注册的私钥）→ 同样的 7-步流程到 data_agent
        │       └─ token-exchange 用 X-Subject-Token = user_token 当 subject_token
        ▼
[data-agent:8101] → [feishu-mock:9000] /bitable/v1/.../records  (4 行)
        ▼
[doc-assistant 内 synthesizer] → 生成 blocks
        ▼
[feishu-mock] /docx/v1/documents (新建) + /document_blocks (写入)
        ▼
返回 { document_id: doc_xxx, url: https://feishu.cn/docx/... }
```

实测总耗时（冷启动后）：约 430ms / 次。

---

## 6. 跑测试 / 跑 demo

```bash
# 单测（66 通过）
python -m pytest -q

# 起栈（首次需 build IdP + agent-base）
docker compose up -d --build

# 端口探测
curl http://localhost:8000/healthz   # idp
curl http://localhost:8181/health    # opa
curl http://localhost:9200/healthz   # gateway
curl http://localhost:8100/healthz   # doc-assistant

# e2e 演示
python scripts/e2e_demo.py
```

`.demo_keys/` 会被首启写入；下次跑直接复用同一 web_ui kid。

---

## 7. 测试结果

```
======= 66 passed, 48 warnings in 8.97s =======
```

e2e demo 输出包含真实的 `data_agent` 4 行飞书数据 + 写入 `doc_xxxx` 文档 ID，
延迟字段 `latency_ms=433`。

---

## 8. 残余风险 / 后续

1. **OPA 策略中递归被 `walk()` 替代** —— 行为对正常输入等价，但
   `walk()` 会枚举所有嵌套对象（包括 act 链以外的字段）。当前 act 结构
   只有 `{sub, act}`，安全；如未来引入嵌套对象需收紧过滤。
2. **`VERIFY_DPOP=false`** —— 为联调便利，IdP 暂未硬校验 DPoP htu/htm/ath。
   生产前需切回 `true`，并验证 DPoP key 与 jkt 绑定。
3. **`web_ui` 私钥落本地磁盘** —— 演示用 `.demo_keys/`，生产应走 KMS / Vault。
4. **`bcrypt<4`** —— 临时 pin。后续应升级 passlib 或换 argon2。
5. **act 链深度仍要 OPA 校验** —— `delegation_depth_ok` 用 `walk()` 现可工作；
   配合 capability YAML 的 `max_depth`（demo: doc_assistant=1）。
6. **Gateway 不验 token** —— 设计如此（zero-trust），所有校验在被叫 agent 端。
7. **WSL2 + docker-desktop port-forward** 启动竞争 —— 偶尔需 `docker compose
   restart gateway-mock` 才能在 host 9200 看到端口。

---

## 9. 触达的文件

新增：
- `services/idp/**` (M1)
- `services/opa/**` (M1，含 walk() 改写)
- `capabilities/{doc_assistant,data_agent,web_agent,web_ui}.yaml`
- `users/alice.yaml`
- `scripts/agent_entrypoint.sh`
- `scripts/e2e_demo.py`
- `agents/common/bootstrap_register.py`
- `docs/M1_INTEGRATION_REPORT.md`（本文）

修改：
- `agents/common/{auth.py,server.py}`
- `agents/doc_assistant/{handler.py,sdk.py}`
- `sdk/agent_token_sdk/{client.py,assertion.py}`
- `services/gateway_mock/main.py`
- `Dockerfile`, `docker-compose.yml`
- `services/idp/requirements.txt`（bcrypt<4）
- `agents/doc_assistant/capability.yaml`（加 orchestrate）
- `tests/sdk/test_assertion_dpop.py`（断言改 bare）

---

**结论**：M1 + M4 + M5 已完成端到端联通。`docker compose up -d` 可一键起 8 服务，
`pytest` 全绿，`scripts/e2e_demo.py` 可端到端跑出真实文档。
