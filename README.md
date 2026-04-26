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

```
agents/           M4 编排 + 执行 agents（doc / data / web + common）
sdk/              M4 客户端 SDK（agent_token_sdk）
services/
  ├── idp/        M1 IdP：OIDC + RFC 7523 + 8693 + 9449 + admin
  ├── opa/        M1 OPA 策略 + 数据
  ├── feishu_mock/  M5 飞书 mock
  └── gateway_mock/ M5 网关 mock
capabilities/     agent capability YAML（注册时上传到 IdP）
users/            用户帐号 + 权限 YAML
scripts/          agent_entrypoint.sh + e2e_demo.py
tests/            pytest（66 用例）
docs/
  ├── M1_INTEGRATION_REPORT.md   M1 ↔ M4/M5 联调报告
  ├── RUN_CHAIN.md               完整调用链
  ├── AGENTS_FINAL_REPORT.md     agents 模块说明
  ├── SDK_FINAL_REPORT.md        SDK 模块说明
  └── design/                    分工 + 各模块设计文档（中文）
Dockerfile        agent-base 共享镜像
docker-compose.yml  8 服务编排
.env.example      环境变量样例（LLM key / IdP / Gateway）
```

## 关键文档

- 联调报告：[`docs/M1_INTEGRATION_REPORT.md`](docs/M1_INTEGRATION_REPORT.md)
- 调用链：[`docs/RUN_CHAIN.md`](docs/RUN_CHAIN.md)
- 设计文档（中文）：[`docs/design/`](docs/design/)
