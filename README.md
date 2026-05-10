# Agent-Token

零信任 Agent-to-Agent 授权 demo，覆盖 RFC 7521 / 7523 / 7638 / 8693 / 9449（DPoP）+ OIDC PKCE，附带 OPA 策略引擎、审计管道、Next.js 控制台和真飞书 Open Platform 接入。

| 模块 | 内容 |
|------|------|
| **M1 IdP** | OIDC + Token Exchange + DPoP + KMS（SQLite + Redis） |
| **M2 Gateway** | 路由 + OPA authz + 熔断 + 审计转发 |
| **M3 OPA** | Rego 策略：delegation、scope、audience、撤销 |
| **M4 Agents** | doc_assistant（LangGraph 编排） / data_agent / web_agent |
| **M5 Mocks + 真飞书** | feishu-mock 假数据、`FEISHU_BASE` 一键切真 Open Platform |
| **M6 Audit + Web-UI** | audit-api（SQLite 持久化）、Next.js 控制台（admin / chat / docs / traces / plans / revoke） |

## 快速开始

```bash
# 1. 拉镜像 + 起栈（首跑会 build agent-base + infra/* + web-ui）
docker compose up -d --build

# 2. 单测（93/93 全过；mock 仅作为 tests/_fixtures 在测试期 ASGITransport 装载）
python -m pytest -q

# 3. 真飞书 + 真 LLM smoke
#    需先填 .env（见下文），然后：
docker compose exec data-agent python /app/scripts/feishu_smoke.py
```

## .env 关键配置

复制 `.env.example` 为 `.env` 后按需填：

```bash
# ─── LLM provider ────────────────────────────────────────
LLM_PROVIDER=mock              # mock | volc | openai
ARK_API_KEY=ark-xxxxxxxx-...   # 火山方舟 API Key（控制台领）
ARK_MODEL=ep-2026xxxx-xxxxx    # endpoint id 或公开模型名（doubao-seed-1-6-250615）

# ─── Feishu / Lark Open Platform ────────────────────────
FEISHU_BASE=https://open.feishu.cn           # 默认接真飞书；test fixture 直接 ASGI 装载，不依赖此变量
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_SHARED_ROOT_FOLDER=                   # 必填：picker 根目录（bot 是 collaborator 的 folder token）
FEISHU_CONTACT_DEPT_ID=                      # 留空 planner 自动 skip 通讯录任务
FEISHU_CALENDAR_ID=                          # 留空 planner 自动 skip 日程任务
FEISHU_DOCX_FOLDER_TOKEN=                    # 仅 DOC_STORAGE=feishu 时生效；避免 tenant_token 写 root drive 403
```

真飞书前置：飞书应用后台勾权限 `bitable:app:readonly` + `docx:document` + 按需 `contact:user.base:readonly` + `calendar:calendar:readonly`，**发布版本审核通过**才生效。

## 健康探测

| 服务 | 端口 / 路径 |
|------|-------------|
| IdP | `:8000/healthz` |
| OPA | `:8181/health` |
| Gateway | `:9200/healthz` |
| doc-assistant / data-agent / web-agent | `:8100` / `:8101` / `:8102` `/healthz` |
| audit-api | `:8090/healthz` |
| web-ui | `:3000` |

Web-UI 入口：`http://localhost:3000`（默认 `alice / alice`，登录走完整 OIDC PKCE）。

## 目录

```text
├── agents/                 # 业务 Agent
│   ├── common/             # LLM 工厂 / auth / logging / Server 基类
│   ├── data_agent/         # 飞书 bitable / contact / calendar / drive / docx 读
│   ├── doc_assistant/      # LangGraph 编排（planner → plan_validate → dispatcher → synthesizer → doc_writer）
│   └── web_agent/          # web 搜索 / fetch
├── infra/                  # 基建服务（与 agents 同级）
│   ├── audit-api/          # 审计 API（SQLite，service-token / admin-token 隔离）
│   ├── gateway/            # API 网关（路由 + OPA + 熔断 + 审计 + mTLS 可选）
│   ├── idp/                # IdP（OIDC PKCE / Token Exchange / DPoP / KMS / 撤销）
│   └── opa/                # Rego 策略 + data 静态规则
├── apps/
│   └── web/                # Next.js 控制台（admin/agents、audit、chat、docs/[id]、plans/[id]、revoke、traces/[id]）
├── sdk/agent_token_sdk/    # AgentClient / DPoP / assertion / Server wrapper
├── capabilities/           # 能力声明 YAML（与 OPA data 对齐）
├── users/                  # 预设用户（alice.yaml 等）
├── scripts/                # agent_entrypoint.sh / feishu_smoke.py
├── tests/                  # 单测（agents / sdk / 内部 _fixtures/feishu_mock 仅测试期使用）
├── docs/                   # 设计文档 + 联调报告 + 调用链
├── .env.example            # 环境变量模板
├── docker-compose.yml      # 8 容器一键部署
└── Dockerfile              # agent-base 镜像
```

## 端到端调用链

```
Web-UI ──OIDC PKCE──▶ IdP ──user_token──▶ Web-UI
   │
   └──A2A invoke (DPoP)──▶ Gateway ──OPA authz──▶ doc_assistant
                                                      │
                       ┌──────────────────────────────┴────────────────────────┐
                       │                                                       │
                  planner (LLM)                                          synthesizer (LLM)
                       │                                                       │
                       ▼                                                       ▼
                  dispatcher ──token_exchange──▶ IdP ──delegated token──▶ data_agent / web_agent
                                                                                │
                                                                          Feishu / Web
                       ▲
                       │
                  doc_writer ──▶ Feishu Open Platform docx
```

每跳都被 audit-api 记录：`token_issued` / `token_consumed` / `authz_decision` / `result`，全链路 `trace_id` 串联，`dpop_jkt` 绑定。

## 关键文档

- 联调报告：[`docs/M1_INTEGRATION_REPORT.md`](docs/M1_INTEGRATION_REPORT.md) / [`docs/M2_INTEGRATION_REPORT.md`](docs/M2_INTEGRATION_REPORT.md)
- 调用链：[`docs/RUN_CHAIN.md`](docs/RUN_CHAIN.md)
- Agent / SDK 报告：[`docs/AGENTS_FINAL_REPORT.md`](docs/AGENTS_FINAL_REPORT.md) / [`docs/SDK_FINAL_REPORT.md`](docs/SDK_FINAL_REPORT.md)
- 系统现状：[`docs/系统现状.md`](docs/系统现状.md)
- 设计文档（中文）：[`docs/design/`](docs/design/)

## 测试矩阵

| 路径 | 状态 |
|------|------|
| `pytest tests/` | 93/93 ✅ |
| 真飞书 + 真 ARK Doubao e2e | ✅ ~88s（含 LLM 思考） |
| Audit 链路 dpop_jkt 绑定校验 | ✅ |
| Web-UI 9 个页面 200 | ✅ |
