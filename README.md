# Agent-Token

> **Zero-Trust Agent-to-Agent (A2A) Authorization Platform**
> 面向 LLM 多 Agent 编排的零信任授权底座 — 一次性 DPoP-bound Token + OPA 策略 + 全链路审计与撤销。

<p>
  <img alt="status"   src="https://img.shields.io/badge/status-demo-blue">
  <img alt="tests"    src="https://img.shields.io/badge/tests-93%2F93-brightgreen">
  <img alt="standards" src="https://img.shields.io/badge/RFC-7521%20%7C%207523%20%7C%207636%20%7C%207638%20%7C%208693%20%7C%209449-informational">
  <img alt="policy"   src="https://img.shields.io/badge/policy-OPA%20Rego-purple">
  <img alt="runtime"  src="https://img.shields.io/badge/runtime-Docker%20Compose-2496ED">
</p>

***

## 1. 概要

Agent-Token 在多 Agent 系统中执行 **"每次调用、每个能力、每个资源都单独授权"** 的零信任原则：

- **凭证最小化**：编排器只持有用户 token，下游每跳通过 `urn:ietf:params:oauth:grant-type:token-exchange`（RFC 8693）签发**一次性、按动作收敛**的 delegated token。
- **持有人证明**：所有调用强制 DPoP（RFC 9449），`htm + htu + iat + jkt` 绑定 + Redis `jti` SETNX 防重放。
- **策略可证伪**：OPA + Rego 实现 10 条独立 deny rule（agent\_capability、executor\_map、scope/audience 覆盖、delegation\_depth、SoD、撤销、anomaly 等），Plan-mode 一次校验整张 DAG。
- **全链路审计**：每跳 `token_issued` / `authz_decision` / `token_consumed` / `result` 由 audit-api 持久化，`trace_id + dpop_jkt` 跨服务串联。
- **6 维度撤销**：`jti / sub / agent / trace / plan / chain`，Redis SADD + Pub/Sub 秒级生效。

技术栈：FastAPI · OPA · Redis · SQLite · LangGraph · Next.js 15 · React Flow · Docker Compose。

***

## 2. 架构

```
                     ┌─────────────────────────────────────────────────────────┐
                     │                       Audit-API                         │
                     │   (token_issued / authz_decision / token_consumed)      │
                     └──────────────▲──────────────▲──────────────▲────────────┘
                                    │              │              │
              OIDC PKCE  ┌──────────┴───┐  ┌───────┴────────┐  ┌──┴─────────┐
   Web-UI ───────────────▶     IdP      │  │    Gateway     │  │   Agents   │
   (Next.js)              │  Token-Ex   │  │  OPA enforce   │  │  doc/data/ │
                          │  KMS / JWKS │◀─┤  DPoP + mTLS   │◀─┤  web       │
                          └──────┬──────┘  └────────┬───────┘  └─────┬──────┘
                                 │ JWKS/JWT          │ decisions      │ Feishu /
                                 ▼                   ▼                ▼ Web
                          ┌──────────────┐    ┌─────────────┐
                          │   Redis      │    │     OPA     │
                          │ jti / revoke │    │   Rego v1.2 │
                          └──────────────┘    └─────────────┘
```

LangGraph 内部链路：`planner → plan_validate (OPA bulk) → dispatcher → synthesizer → doc_writer`，
每个下游 task 走一次 `token_exchange` + DPoP 调 Gateway，Gateway 拒/放后回 audit。

***

## 3. 标准合规

| 规范                     | 模块           | 说明                                                                          |
| ---------------------- | ------------ | --------------------------------------------------------------------------- |
| RFC 6749 / 7636        | IdP, Web-UI  | OIDC Authorization Code + PKCE S256                                         |
| RFC 7519 / 7521 / 7523 | IdP, SDK     | JWT, Bearer & JWT-Bearer Client Assertion                                   |
| RFC 7638               | IdP, SDK     | JWK Thumbprint (`jkt`)                                                      |
| RFC 8693               | IdP          | Token Exchange — `subject_token` + `actor_token` → delegated one-shot token |
| RFC 9449               | Gateway, SDK | DPoP `htm + htu + iat + jti + jkt`                                          |
| OPA / Rego v1.2        | OPA          | 10 条 a2a deny rule + plan-mode 批量校验                                         |

***

## 4. 组件

| 服务              | 端口   | 关键职责                                                                             | 持久化                    |
| --------------- | ---- | -------------------------------------------------------------------------------- | ---------------------- |
| `idp`           | 8000 | OIDC + Token Exchange + KMS（dev: passphrase；prod: KMS keys） + 撤销发布               | SQLite + Redis         |
| `gateway`       | 9200 | A2A 路由 + DPoP 校验 + OPA enforce + mTLS 可选 + audit forward                         | SQLite (gateway-audit) |
| `opa`           | 8181 | Rego 决策（input.intent / delegation\_chain → decision + reasons）                   | static data            |
| `doc-assistant` | 8100 | LangGraph 编排器（planner / plan\_validate / dispatcher / synthesizer / doc\_writer） | —                      |
| `data-agent`    | 8101 | 飞书 bitable / contact / calendar / drive / docx 读取                                | —                      |
| `web-agent`     | 8102 | web.search + web.fetch（dispatcher 自动追加 fetch\_top\_k）                            | —                      |
| `audit-api`     | 8090 | 事件 ingest / events / traces / plans / stats / SSE                                | SQLite (WAL)           |
| `web-ui`        | 3000 | Next.js 15 控制台（chat / docs / plans / traces / audit / revoke / admin）            | sessionStorage         |
| `redis`         | 6379 | jti 一次性、撤销 SADD、Pub/Sub                                                          | —                      |

***

## 5. 快速开始

**前置**：Docker ≥ 24，docker compose v2，4 GB 空闲内存，Linux/WSL2/macOS。

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env：填 ARK_API_KEY / FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_SHARED_ROOT_FOLDER

# 2. 启动全栈（首跑构建 agent-base / infra / web-ui）
docker compose up -d --build

# 3. 健康自检
for p in 8000/healthz 9200/healthz 8181/health 8100/healthz 8101/healthz 8102/healthz 8090/healthz; do
  curl -sf http://localhost:${p} && echo " ✓ ${p}"
done

# 4. 浏览器打开控制台
xdg-open http://localhost:3000   # 默认 alice / alice123，完整 OIDC PKCE
```

**端到端 smoke**：

```bash
# 单元 + 集成（93/93）
python -m pytest -q

# 真飞书 + 真 Doubao LLM
docker compose exec data-agent python /app/scripts/feishu_smoke.py
```

***

## 6. 配置参考

`.env`（关键项；完整字段见 `.env.example`）：

| 变量                                    | 默认                        | 说明                                              |
| ------------------------------------- | ------------------------- | ----------------------------------------------- |
| `LLM_PROVIDER`                        | `mock`                    | `mock` / `volc`（火山方舟 ARK） / `openai`            |
| `ARK_API_KEY`                         | —                         | 火山方舟 API Key                                    |
| `ARK_MODEL`                           | `doubao-seed-1-6-250615`  | endpoint id 或公开模型名                              |
| `FEISHU_BASE`                         | `https://open.feishu.cn`  | 飞书 Open Platform 根                              |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | —                         | 飞书自建应用凭证                                        |
| `FEISHU_SHARED_ROOT_FOLDER`           | —                         | picker 根目录 folder\_token（bot 必须是 collaborator）  |
| `FEISHU_CONTACT_DEPT_ID`              | 空                         | 留空则 planner 跳过通讯录任务                             |
| `FEISHU_CALENDAR_ID`                  | 空                         | 留空则 planner 跳过日程任务                              |
| `DOC_STORAGE`                         | `local`                   | `local`（apps/web/doc-out） / `feishu`（写回飞书 docx） |
| `ADMIN_TOKEN`                         | `change-me-in-production` | audit-api admin 接口鉴权                            |
| `IDP_KMS_PASSPHRASE`                  | dev 默认                    | prod 必须自定义；解封 KMS 私钥                            |

**飞书权限**：`bitable:app:readonly` + `docx:document` + 按需 `contact:user.base:readonly` / `calendar:calendar:readonly`，**发布版本审核通过**才生效。

***

## 7. 服务接口（精简）

| 接口                                                           | 方法         | 鉴权                       | 用途                 |
| ------------------------------------------------------------ | ---------- | ------------------------ | ------------------ |
| `/.well-known/openid-configuration`                          | GET        | none                     | OIDC discovery     |
| `/oidc/authorize` / `/oidc/token`                            | GET / POST | PKCE / client\_assertion | OIDC code → tokens |
| `/oauth/token` (grant\_type=token-exchange)                  | POST       | client\_assertion + DPoP | 下游一次性 token        |
| `/a2a/invoke` (Gateway)                                      | POST       | DPoP + Bearer            | A2A 调用入口           |
| `/audit/events` / `/audit/traces/{id}` / `/audit/plans/{id}` | GET        | admin / service token    | 审计查询               |
| `/audit/revoke`                                              | POST       | admin token              | 6 维度撤销发布           |
| `/audit/stats`                                               | GET        | admin                    | 全局聚合指标             |

完整契约见各模块设计文档（[`docs/design/分模块方案/`](docs/design/分模块方案/)）与服务源码 OpenAPI（FastAPI `/docs`）。

***

## 8. 安全模型

- **凭证最小化**：用户 token 只在编排器内，绝不外传；下游一律 token-exchange 后的 audience+scope+resource 三轴收敛 token。
- **持有人证明**：每个 Token 绑定 EC P-256 公钥（`cnf.jkt`），DPoP proof 必带 `htm/htu/iat`，Redis 防重放窗口 120s。
- **重放防御**：`jti` Redis `SETNX EX=120`，二次消费拒绝。
- **委派链**：`actor_token` 链接 caller → callee，OPA 校验 `accept_from` + `max_depth`，链断即拒。
- **职责分离 (SoD)**：写动作的同 trace 内禁止由签发者本人完成（防 self-exchange 提权）。
- **撤销**：Redis Set 维护 `revoked:{type}`，Gateway 决策前查 6 个 Set，Pub/Sub 通知所有节点失效缓存。
- **审计完整性**：每事件 `trace_id + span_id + parent_span_id + dpop_jkt + token_one_time` 全量入库；SQLite WAL，service-token 与 admin-token 分隔写读权限。
- **mTLS（可选）**：Gateway ↔ Agent 链路支持双向证书。

威胁建模 / 控制矩阵：[`docs/design/方案-细化.md`](docs/design/方案-细化.md)。

***

## 9. 可观测性

| 维度                 | 数据来源                                  | 控制台入口                |
| ------------------ | ------------------------------------- | -------------------- |
| Plan DAG（含 OPA 决策） | `/audit/plans/{id}`                   | `/plans/{plan_id}`   |
| Trace 时序树          | `/audit/traces/{id}`                  | `/traces/{trace_id}` |
| 原始事件流              | `/audit/events` + SSE `/audit/stream` | `/audit`             |
| 全局指标               | `/audit/stats`                        | `/audit` 汇总卡片        |
| 撤销视图               | `/audit/revoke`                       | `/revoke`            |
| Agent 注册表          | OPA `data.agents`                     | `/admin/agents`      |

DAG/Trace 视图标注 allow / deny / pending、`task_id`、`jti` 前缀、决策原因，可直跳飞书源数据。

***

## 10. 测试

| 套件        | 覆盖                                 | 命令                                                                   |
| --------- | ---------------------------------- | -------------------------------------------------------------------- |
| 单元        | IdP / Gateway / OPA / SDK / Agents | `python -m pytest -q`                                                |
| 集成        | token-exchange 链路 + DPoP + 撤销      | `python -m pytest tests/sdk/test_integration_with_agents.py`         |
| E2E       | 真飞书 + 真 Doubao                     | `docker compose exec data-agent python /app/scripts/feishu_smoke.py` |
| Web-UI 冒烟 | 9 个页面 / Playwright                 | `cd apps/web && pnpm test:e2e`                                       |

CI 矩阵：`pytest`（Python 3.11） · `pnpm build`（Node 20）。

***

## 11. 仓库结构

```
.
├── agents/                 # 业务 Agent
│   ├── common/             # LLM 工厂 / auth / logging / Server 基类
│   ├── data_agent/         # Feishu bitable / contact / calendar / drive / docx
│   ├── doc_assistant/      # LangGraph 编排
│   └── web_agent/          # web.search / web.fetch
├── infra/                  # 基建
│   ├── idp/                # OIDC + Token Exchange + KMS + 撤销
│   ├── gateway/            # A2A 路由 + OPA + DPoP + mTLS
│   ├── opa/                # Rego 策略 + data
│   └── audit-api/          # 审计 SQLite + SSE
├── apps/web/               # Next.js 控制台
├── sdk/agent_token_sdk/    # AgentClient / DPoP / Server wrapper
├── capabilities/           # 能力声明 YAML（对齐 OPA data.agents）
├── users/                  # 预设用户
├── scripts/                # entrypoint / smoke 脚本
├── tests/                  # pytest 套件
├── docs/                   # 设计 / 报告 / 调用链
├── docker-compose.yml      # 9 容器编排
├── Dockerfile              # agent-base 镜像
└── .env.example
```

***

## 12. 文档索引

- **课题背景**：[`docs/design/课题.md`](docs/design/课题.md)
- **设计总览（v2，1495 行）**：[`docs/design/方案-细化.md`](docs/design/方案-细化.md) — 原则、Token 形态、OPA 规则、撤销、KMS、审计全链路。
- **分模块设计**：[`docs/design/分模块方案/`](docs/design/分模块方案/)
  - [IdP](docs/design/分模块方案/方案-IdP.md) · [Gateway](docs/design/分模块方案/方案-GATEWAY.md) · [OPA](docs/design/分模块方案/方案-OPA.md)
  - [Agents](docs/design/分模块方案/方案-Agents.md) · [SDK](docs/design/分模块方案/方案-SDK.md) · [Web](docs/design/分模块方案/方案-Web.md)
  - [AuditAPI](docs/design/分模块方案/方案-AuditAPI.md) · [Anomaly](docs/design/分模块方案/方案-Anomaly.md)
- **服务级 README**：[`infra/idp/README.md`](infra/idp/) · [`infra/gateway/README.md`](infra/gateway/README.md) · [`infra/audit-api/README.md`](infra/audit-api/README.md) · [`apps/web/README.md`](apps/web/README.md)
- **运行时 API 文档**：每个 FastAPI 服务 `/docs`（OpenAPI 3）— `http://localhost:{8000,9200,8090,8100,8101,8102}/docs`

> 注：分模块设计文档为 v2 spec（2026-04 撰写），部分路径与 flatten 后的仓库布局（`infra/` + `agents/` + `apps/`）不同步；以本 README 与源码为准。

***

## 13. 许可

Demo / 研究用途。生产部署前请：

1. 替换所有 `change-me-in-production` 默认值
2. 切换 `IDP_KMS_PASSPHRASE` 为 KMS 托管密钥
3. 启用 Gateway ↔ Agent mTLS
4. 配置外部 OIDC / RBAC 接管 admin token

