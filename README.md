# Agent-Token

零信任 Agent-to-Agent 授权 demo（RFC 7521 / 7523 / 7638 / 8693 / 9449 + DPoP）。
M1 IdP + OPA + Redis + SQLite | M4 编排/执行 agents | M5 mocks（Gateway / 飞书）。

## 快速开始

```bash
# 单测
python -m pytest -q

# 起栈（首次 build IdP + agent-base）
docker compose up -d --build

# 端到端演示（OIDC PKCE → token-exchange → invoke → 写飞书 doc）
python scripts/e2e_demo.py
```

健康探测：
- IdP `:8000/healthz`
- OPA `:8181/health`
- Gateway `:9200/healthz`
- doc-assistant `:8100/healthz` · data-agent `:8101` · web-agent `:8102`
- feishu-mock `:9000/healthz`

## 目录

```text
├── agents/                 # 具体的业务 Agent 实现
│   ├── common/             # Agent 公共基础组件（含 LLM 客户端工厂、鉴权、日志、基础 Server 等）
│   ├── data_agent/         # 数据处理 Agent（含飞书日历、多维表格、联系人等 API 接入和数据处理）
│   ├── doc_assistant/      # 文档助手 Agent（基于图的复杂逻辑处理，包含规划、分发、合成节点）
│   └── web_agent/          # Web 搜索 Agent（提供基于搜索引擎的信息检索与抓取能力）
├── capabilities/           # Agent 能力注册声明（YAML 格式，如 Web 搜索、文档分析等能力配置）
├── scripts/                # 自动化脚本与端到端 (E2E) 演示代码（如启动入口、测试 demo）
├── sdk/                    # Agent-Token 开发者 SDK
│   └── agent_token_sdk/    # 提供对各类 Agent 框架（Autogen, Langchain, Langgraph）的适配，以及 DPoP、断言等安全特性的支持
├── services/               # 后端核心微服务与外部接口 Mock 服务
│   ├── feishu_mock/        # 飞书开放平台 API Mock 服务（用于本地无外部依赖测试）
│   ├── gateway/            # 统一 API 网关（负责路由鉴权、意图解析、限流熔断、调用链追踪与审计）
│   ├── gateway_mock/       # 网关 Mock 服务
│   ├── idp/                # 身份认证服务 (Identity Provider)，处理 OIDC 流程、Token 交换、KMS 密钥轮换与权限验证
│   ├── idp_mock/           # IDP Mock 服务
│   └── opa/                # Open Policy Agent 策略引擎（包含 A2A 通信、委派控制、计划验证等 Rego 策略及测试数据）
├── users/                  # 测试/预设用户配置文件目录（例如 alice.yaml）
├── .env.example            # 环境变量配置模板
├── docker-compose.yml      # 本地一键部署/容器编排配置文件
├── Dockerfile              # 项目基础镜像构建文件
├── requirements.txt        # Python 核心环境依赖清单
└── start.sh                # 项目快速初始化与启动脚本
```


## 关键文档

- 联调报告：[`docs/M1_INTEGRATION_REPORT.md`](docs/M1_INTEGRATION_REPORT.md)
- 调用链：[`docs/RUN_CHAIN.md`](docs/RUN_CHAIN.md)
- 设计文档（中文）：[`docs/design/`](docs/design/)
