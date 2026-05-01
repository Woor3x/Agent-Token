# Agent-Token 完整运行链文档

本文记录从「容器启动」到「飞书文档落库」的完整运行链：每一步落在哪个进程、走哪个 RFC、读哪个文件、写哪个文件。

---

## 1. 容器拓扑

`docker compose up --build` 起 6 个服务（一个 image，按 `APP_MODULE` 分流）：

| 服务名 | 容器内监听 | 宿主端口 | 角色 |
| --- | --- | --- | --- |
| `feishu-mock` | 9000 | 9000 | 飞书 OpenAPI 桩（auth / bitable / contact / calendar / docx） |
| `idp-mock`    | 9100 | 9100 | RFC 8693 Token Exchange + `/jwks` + `/plan/validate` |
| `gateway-mock`| 9200 | 9200 | A2A 网关，按 `X-Target-Agent` 转发 `/invoke` |
| `doc-assistant` | 8100 | 8100 | 编排 / 写文档 |
| `data-agent`  | 8101 | 8101 | 飞书数据读（bitable / contact / calendar） |
| `web-agent`   | 8102 | 8102 | Web 检索 |

公共 env：`IDP_ISSUER`、`MOCK_AUTH_SECRET`、`FEISHU_BASE`、`GATEWAY_URL`、`LLM_PROVIDER`、`ARK_API_KEY`、`ARK_MODEL`。Compose 通过 `x-common-env` 锚点统一注入。

健康检查：

```bash
curl http://localhost:9100/healthz   # idp-mock
curl http://localhost:9200/healthz   # gateway-mock（peers 列表）
curl http://localhost:8100/healthz   # doc-assistant
```

---

## 2. 模块层次

```
agents/
  common/
    auth.py        # verify_delegated_token (HS256 mock + RS256 prod)
    server.py      # AgentServer.create_app() — /invoke 通用骨架
    config.py      # AgentConfig.load(env)
    capability.py  # capability.yaml 解析 + scope 匹配
    llm/
      base.py      # LLMProvider ABC + ChatMessage / ChatResult / LLMError
      mock.py      # 离线测试用
      volc.py      # 火山方舟 Doubao Seed (ep-xxx) OpenAI-兼容 endpoint
      openai.py    # 兼容 OpenAI / Azure
      factory.py   # make_llm() 按 LLM_PROVIDER 选实现
  doc_assistant/
    main.py        # build_app() 注入 LLM、peer_apps
    handler.py     # orchestrate / feishu.doc.write 两条入口
    graph.py       # LangGraph 线性 fallback（planner→fanout→synth→writer）
    nodes/
      planner.py     # ★ LLM 路径 + rule-based fallback
      fanout.py      # 按 DAG.deps 分批并发调 SDK
      synthesizer.py # ★ LLM 写「执行摘要」段，再追加表格段
      doc_writer.py  # 调 feishu.docx.create + blocks.batch_create
    prompts/
      planner_system.txt
      synthesizer_system.txt
    sdk.py         # AsgiSdkClient（in-proc）/ HttpSdkClient（生产经 Gateway）
  data_agent/
  web_agent/

services/
  feishu_mock/     # 端口 9000
  idp_mock/        # 端口 9100  ← 新增
  gateway_mock/    # 端口 9200  ← 新增

sdk/               # 端到端集成 SDK，业务 client 用
```

---

## 3. 从用户输入到文档落库的运行链

下面用一次「汇总 Q1 销售并写报告」demo 走完全链路。所有步骤都打 `trace_id`，可在日志里串起来。

### 3.1 用户 → DocAssistant `/invoke`

```
client → POST http://localhost:8100/invoke
  Authorization: DPoP <user_token_hs256>
  body: {"intent": {"action": "orchestrate",
                    "resource": "agent:doc_assistant",
                    "params": {"prompt": "汇总 Q1 销售并写报告"}}}
```

* Token 来自调用方先打 `idp-mock /token/exchange` 拿到的「初始用户令牌」（mock 路径下也可直接 `sign_mock_token`）。
* 进入 `agents.common.server.AgentServer.create_app /invoke`：
  1. `verify_delegated_token` 读 `Authorization`，校验 iss / aud / exp / scope（HS256 mock 模式）。
  2. `_extract_deny_reasons` 拿 capability.yaml + scope 匹配；不通过返回 403。
  3. 通过则 `await self.handler(body, claims, capability)` → `DocAssistantHandler.__call__`。

### 3.2 DocAssistant 编排（`agents/doc_assistant/handler.py`）

`action == "orchestrate"` 分支：

```python
sdk = AsgiSdkClient(apps=peer_apps, user_sub=claims.sub)
state = {
    "user_prompt": prompt, "user_token": claims.raw,
    "trace_id": ..., "plan_id": ...,
    "sdk": sdk, "feishu_base": ..., "feishu_oauth": ...,
    "client_factory": ..., "llm": self._llm,
}
final = await run_graph(state)
```

`run_graph` 调用 LangGraph DAG（线性 fallback 顺序）：

```
planner → fanout → synthesizer → doc_writer
```

### 3.3 Planner（`nodes/planner.py`）— LLM 路径

* 读 `state["llm"]`。若不为 `None`：
  1. 加载 `prompts/planner_system.txt`（强制 JSON 输出 schema）。
  2. `llm.chat(messages=..., temperature=0.1, max_tokens=800, json_mode=True)`。
  3. `_extract_json` 去围栏 + `{...}` 兜底。
  4. 若任务最后一步缺 `feishu.doc.write`，自动补一条 deps 包含全部前置任务。
  5. `validate_dag` 验 id 唯一 / agent 枚举 / action 枚举 / resource 正则 / deps 顺序。
* LLM 路径 `LLMError` / `ValueError` / `JSONDecodeError` → fallback 到 `_rule_plan`（中英文关键词匹配 → 三类任务 + doc.write 收尾）。
* 输出 `state["dag"] = [...]`。

### 3.4 Fanout（`nodes/fanout.py`）

按 `dag` 的 `deps` 拓扑分批并发调用 SDK：

```python
for batch in topo_batches(dag):
    results = await asyncio.gather(*[run_task(sdk, t) for t in batch])
```

SDK 两种实现：

* **`AsgiSdkClient`**（容器内 demo 也走这条，因为 doc-assistant 容器同时挂 data_agent/web_agent ASGI app；纯网络版本走下一条）— 直接 `httpx.ASGITransport` 打 peer app `/invoke`。
* **`HttpSdkClient`**（真生产）— 调 `GATEWAY_URL/a2a/invoke`，header 带 `X-Target-Agent: data_agent`。Gateway 转给对应容器。

每次 fan-out 前都会 `_mint_mock_token` 模拟「跟 IdP 申请新一次性 delegated token」：

```
sub = user_sub
act.sub = doc_assistant       # actor chain (RFC 8693 §4.1)
aud = agent:<target>
scope = ["<action>:<resource>"]
ttl = 60s, one_time = True
cnf.jkt = mock-jkt            # DPoP 绑定（RFC 9449）
```

真链路应改为 SDK 调 `idp-mock /token/exchange`（form 字段见 §3.6）。

### 3.5 子 Agent（data_agent / web_agent）

收到 `POST /invoke` →
1. `verify_delegated_token` 校验 token 的 iss / aud（应等于自己 agent_id）/ exp / scope；
2. `capability.yaml` 二次自检（zero-trust：不信任 Gateway）；
3. handler 调相应 endpoint：
   * data_agent → `feishu_mock` `/open-apis/bitable/v1/...` 或 `/contact/v3/...`；先用 `FeishuOAuth` 拿 tenant_access_token。
   * web_agent → 内置 mock 检索结果。
4. 返回 `{status:"ok", data:{...}}` → fanout 收到落到 `state["results"][task_id]`。

### 3.6 Token Exchange (RFC 8693) — `idp-mock`

`services/idp_mock/main.py`：

```
POST /token/exchange  (application/x-www-form-urlencoded)
  grant_type=urn:ietf:params:oauth:grant-type:token-exchange
  subject_token=<user_token>
  subject_token_type=urn:ietf:params:oauth:token-type:jwt
  requested_token_type=urn:ietf:params:oauth:token-type:jwt
  client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
  client_assertion=<RFC 7521 client JWT>
  audience=agent:<target>
  scope=<action>:<resource>
  resource=<resource>
  dpop_jkt=<RFC 7638 thumbprint>
  purpose / plan_id / task_id / trace_id  (extension claims)
```

* `client_assertion` 在 mock 模式下 `verify_signature=False` 解码即过；real 模式必须按 JWKS 验签。
* 校验 `iss == sub`（RFC 7521 §3）。
* 签发 HS256 delegated token（real 模式 RS256），带：
  - `cnf.jkt` 绑定 DPoP key thumbprint；
  - `act.sub` 体现委托链；
  - `one_time:true` + 短 `exp`；
  - 业务字段 `purpose / plan_id / task_id / trace_id`。

返回 `audit_id` 用于回放。

### 3.7 Synthesizer（`nodes/synthesizer.py`）— LLM 摘要

* `_digest_results(state)` 把 `results` 截断成紧凑 JSON（每任务保留前 N 条记录）。
* 如果 `state["llm"]` 不为空：
  1. 加载 `prompts/synthesizer_system.txt`；
  2. `llm.chat(temperature=0.3, max_tokens=400)`；
  3. 在 `Auto Report` heading 后插入 `执行摘要` heading2 + 摘要 text 块。
* LLM 失败（任何 `Exception`）→ 静默跳过，仅保留模板表格段（保证 demo 不挂）。
* 输出 `state["blocks"] = [...]`。

### 3.8 DocWriter（`nodes/doc_writer.py`）

* 调 `FeishuOAuth.get_tenant_token` → `feishu-mock /open-apis/auth/v3/tenant_access_token/internal`；
* `POST /open-apis/docx/v1/documents`（创建文档）；
* `POST /open-apis/docx/v1/documents/{doc_id}/blocks/batch_create`（批量写 blocks）；
* 返回 `{doc_id, doc_url, block_count}` → 整个 `final` 返回给 DocAssistant `/invoke` 调用方。

### 3.9 响应

DocAssistant `/invoke` 200：

```json
{
  "status": "ok",
  "trace_id": "...",
  "event_id": "...",
  "latency_ms": 412,
  "data": {
    "plan_id": "...",
    "dag": [...],
    "results": {"t1": {...}, "t2": {...}},
    "doc": {"doc_id": "...", "doc_url": "...", "block_count": 7}
  }
}
```

---

## 4. LLM 接入说明

`agents/common/llm/factory.py:make_llm()` 按 `LLM_PROVIDER` 选实现：

| `LLM_PROVIDER` | 实现 | 必需 env |
| --- | --- | --- |
| `mock`（默认）| `MockLLMProvider` | — |
| `volc` | `VolcArkProvider`（火山方舟 Doubao Seed） | `ARK_API_KEY`、`ARK_MODEL=ep-xxxxxxxx`、可选 `ARK_BASE` |
| `openai` | `OpenAIProvider` | `OPENAI_API_KEY`、`OPENAI_MODEL`、可选 `OPENAI_BASE` |

启用真模型只需在 `docker-compose.yml` 同级写 `.env`：

```dotenv
LLM_PROVIDER=volc
ARK_API_KEY=sk-xxxxxxxx
ARK_MODEL=ep-20250920-doubao-seed
```

DocAssistant 在 `main.py:build_app()` 不显式注入 llm 时，会自动 `make_llm()`。Planner / Synthesizer 通过 `state["llm"]` 拿到；二者都内置降级路径，LLM 出错不影响 demo 走通。

LLM 调用点：

| 节点 | 用途 | 失败处理 |
| --- | --- | --- |
| `planner_node` | 中英文 prompt → DAG JSON | fallback 到 keyword 规则 |
| `synthesizer_node` | 多 agent 结果 → 80–200 字「执行摘要」 | 静默跳过，仅保留模板表格 |

---

## 5. 调试 / 验证

```bash
# 单元 + 集成
python -m pytest -q
# → 66 passed

# 容器构建
docker compose build

# 起栈
docker compose up

# 三个健康检查
curl localhost:9100/healthz
curl localhost:9200/healthz
curl localhost:8100/healthz

# 走一次完整 orchestrate（mock 模式不需要真飞书凭据）
TOKEN=$(python -c "from agents.common.server import sign_mock_token;print(sign_mock_token(sub='user:alice',actor_sub=None,aud='agent:doc_assistant',scope=['orchestrate:agent:doc_assistant']))")
curl -X POST localhost:8100/invoke \
  -H "Authorization: DPoP $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"intent":{"action":"orchestrate","resource":"agent:doc_assistant","params":{"prompt":"汇总 Q1 销售并写报告"}}}'
```

---

## 6. 走过的 RFC 一览

| 步骤 | RFC | 体现 |
| --- | --- | --- |
| 用户 → DocAssistant 鉴权 | RFC 7519 + 自定义 cnf 段 | `verify_delegated_token` |
| Client Assertion | RFC 7521 §3 | `idp-mock /token/exchange` 校验 `iss == sub` |
| Token Exchange | RFC 8693 | grant_type、subject_token、actor 链 |
| DPoP Key 绑定 | RFC 9449 + RFC 7638 | `cnf.jkt`、`dpop_jkt` 表单字段 |
| Scope 最小化 | OAuth2 + OIDC 实践 | `scope = "<action>:<resource>"` |
| Capability self-check | zero-trust | `_extract_deny_reasons` |
| Trace 透传 | W3C Traceparent | `Traceparent`、`X-Plan-Id`、`X-Task-Id` |

---

## 7. 后续

* 把 `HttpSdkClient` 接到 idp-mock：fanout 之前先 `POST /token/exchange` 拿 fresh token，而不是 `_mint_mock_token` 本地伪造，能完整演示 RFC 8693。
* `verify_delegated_token` 支持 RS256 + JWKS 缓存，配合 `idp-mock /jwks` 上线公钥。
* gateway-mock 加 DPoP `htm/htu/iat/jti` 校验 + replay 防御。
