# SDK 模块最终报告

> 范围：`sdk/agent_token_sdk/` 包，对应 `分模块方案/方案-SDK.md` v2 与 `方案-细化.md` v2 第 §6 / §13 节。
> 验收方式：单元测试 + 与 `agents/*` 模块端到端联调。
> 状态：**全部完成**，`pytest` 绿（49 / 49）。

---

## 1. 计划表与执行结果

| 步骤 | 内容 | 状态 |
| --- | --- | --- |
| S1 | SDK 包骨架、`pyproject.toml`、`__init__` 导出表 | ✅ |
| S2 | `AssertionSigner`（RFC 7523 client_assertion） | ✅ |
| S3 | `DPoPSigner`（RFC 9449 + RFC 7638 jkt） | ✅ |
| S4 | `errors` + `is_retryable` 重试分类 | ✅ |
| S5 | `AgentClient`：Token Exchange + DPoP + Gateway 调用 | ✅ |
| S6 | `AgentServer` 薄封装（复用 `agents.common.server`） | ✅ |
| S7 | LangGraph / LangChain / AutoGen 适配器 | ✅ |
| S8 | SDK 单元测试（11 + 6 = 17 用例） | ✅ |
| S9 | SDK ↔ agents 联调（3 用例，真实 data_agent / web_agent ASGI） | ✅ |
| S10 | 调试 + 最终报告（本文件） | ✅ |

---

## 2. 模块结构

```
sdk/
├── pyproject.toml                  # setuptools, name=agent-token-sdk v1.0.0
└── agent_token_sdk/
    ├── __init__.py                 # 公共导出
    ├── assertion.py                # RFC 7523 自签 Client Assertion
    ├── dpop.py                     # RFC 9449 DPoP + RFC 7638 jkt
    ├── errors.py                   # SDKError 系 + is_retryable
    ├── client.py                   # AgentClient（caller）+ invoke_with_retry
    ├── server.py                   # AgentServer（callee 薄封装）
    └── adapters/
        ├── langgraph.py            # make_a2a_node()
        ├── langchain.py            # make_a2a_tool()
        └── autogen.py              # A2AAgent
```

代码体量：SDK 实现 706 行，测试 752 行，总计 ~1.5k 行。

---

## 3. 关键设计决策

### 3.1 `AgentClient`

- **流水线**（`client.py:75-131`）严格对齐方案-SDK §4：
  1. `AssertionSigner.sign(aud=<idp>/token/exchange)` 产出 `client_assertion`（HS256 mock / RS256 prod）。
  2. `DPoPSigner.sign(url=<idp>/token/exchange, method=POST)` 产出 DPoP Proof。
  3. POST 表单到 IdP，包含 RFC 8693 全部字段（`grant_type` / `subject_token` / `audience` / `scope` / `resource` / `dpop_jkt` / `purpose` / `plan_id` / `task_id` / `trace_id`）。
  4. 拿到一次性 delegated token 后，对 `<gateway>/a2a/invoke` 重新签发 DPoP Proof（这次 `ath = SHA256(token)`）。
  5. POST 到 Gateway，`X-Target-Agent` 头声明目标。
- **HTTP 复用**：构造函数允许注入 `httpx.AsyncClient`。生产可挂全局连接池；测试通过 `mounts={…ASGITransport}` 路由虚拟主机到内嵌 IdP / Gateway / Agent ASGI app（`tests/sdk/helpers.py:150-159`）。
- **错误分流**：IdP 非 200 → `TokenExchangeError`（带 status_code + body）；Gateway 非 200 → `A2AError(code, message, trace_id)`。
- **重试**：独立函数 `invoke_with_retry`，仅对 `is_retryable(code)` 真值的错误（`RATE_LIMITED` / `UPSTREAM_TIMEOUT` / `CIRCUIT_OPEN` / `AGENT_INTERNAL_ERROR`）进行指数回退。`AUTHN_*` / `AUTHZ_*` / `INTENT_INVALID` 永不重试。

### 3.2 `AssertionSigner`

- 强制 `iss == sub == "agent:<agent_id>"`（`assertion.py`），符合方案-IdP §6.1 的 `private_key_jwt` 校验项。
- `exp_delta` 必须在 (0, 60] 区间，超界抛 `AssertionSignError`，对齐方案-细化 §13.1 的"短寿命"要求。
- 三种密钥来源优先级：`private_key_pem` → `private_key_path` → `MOCK_AUTH_SECRET`（仅当 `MOCK_AUTH=true`）。

### 3.3 `DPoPSigner`

- 无 PEM 时，构造时即生成 ephemeral RSA-2048 keypair，保证测试也能产出真实 RFC 7638 thumbprint。
- `jkt_b64u`：按 RFC 7638 规范化 `{"e":..,"kty":"RSA","n":..}` 后 SHA-256 → base64url-no-pad（`dpop.py:51-71`）。
- `sign(url, method, access_token=None)` 输出 `typ=dpop+jwt` JWT，附 `htu` / `htm` / `iat` / `jti`，且当 `access_token` 提供时附 `ath = b64url(SHA256(token))`。

### 3.4 `AgentServer`（callee 侧）

- 故意做成 `agents.common.server.AgentServer` 的薄封装（`server.py:23-67`）：单一来源原则。SDK 用户与生产 Agent 共享同一份 `/invoke` 语义（zero-trust 重验证、deny 原因、trace 上下文、JSON envelope）。
- 暴露 `(agent_id, capability_path, handler, idp_jwks_url?, idp_issuer?)`，无需用户手填 `AgentConfig`。

### 3.5 适配器

- 三个适配器均做 **soft-import**：
  - LangChain：尝试 `from langchain_core.tools import tool`，失败则回退为带 `.name` / `.description` 的普通可调用对象。
  - AutoGen：尝试 `from autogen import ConversableAgent`，失败则继承一个 stub 基类。
  - LangGraph：根本不导入 `langgraph`，只返回 `async def node(state) -> dict`，由调用方自行 `graph.add_node(...)`。
- 这样 SDK 安装 `pip install agent-token-sdk` 时不需任何 LLM 框架依赖，按需 `pip install agent-token-sdk[langgraph]`。

---

## 4. 测试覆盖

```
tests/sdk/
├── helpers.py                      # mock IdP + mock Gateway + 共享 httpx
├── test_assertion_dpop.py          # 7 用例
├── test_client.py                  # 4 用例
├── test_server_wrapper.py          # 3 用例
├── test_adapters.py                # 3 用例
└── test_integration_with_agents.py # 3 用例（联调）
```

`pytest tests/sdk/ -q` → **20 passed**。

### 4.1 单元测试要点

- `test_assertion_mock_claims_roundtrip`：`iss == sub == "agent:doc_assistant"`、`exp - iat == 30`、`kid` 在 header。
- `test_assertion_rejects_exp_delta_out_of_range`：0 与 120 均抛 `AssertionSignError`。
- `test_dpop_jkt_matches_rfc7638`：手动按 RFC 7638 规范化串 → SHA256 → b64url，断言与 `signer.jkt_b64u` 完全相等。
- `test_dpop_proof_has_htu_htm_ath`：`typ=dpop+jwt` / `alg=RS256` / `ath=b64url(SHA256(token))`。
- `test_invoke_propagates_error_envelope`：Gateway 返回 `{"error":{"code":"AUTHZ_SCOPE_EXCEEDED",...}}` → `A2AError.code == "AUTHZ_SCOPE_EXCEEDED"`，`trace_id` 透传。
- `test_token_exchange_400_raises`：IdP 返回 400 → `TokenExchangeError(status_code=400)`。

### 4.2 联调（S9）

`test_integration_with_agents.py` 把 SDK 套到真实 `agents/data_agent` + `agents/web_agent` 上：

```
AgentClient(doc_assistant)
   │  ─── client_assertion + DPoP ───►  Mock IdP (FastAPI)
   │  ◄── one-time delegated token ───
   │
   │  ─── DPoP(ath) + Bearer + X-Target-Agent ───►  Mock Gateway
   │                                                     │
   │                                                     ▼
   │                              Real DataAgentHandler / WebAgentHandler
   │                                  (= agents.common.server.AgentServer)
   │                                       │
   │                                       ▼
   │                                Feishu Mock (services/feishu_mock)
   │  ◄── {"status":"ok","data":{...}} ─────────
```

3 个用例：
1. `test_sdk_to_data_agent_through_mock_gateway`：bitable.read 命中 → 返回 4 行真实 mock 数据（North/South/East/West）。
2. `test_sdk_scope_mismatch_yields_authz_error`：caller 请求 `web.search` 但 target=data_agent → data_agent capability 不覆盖 → 403 `AUTHZ_*`。
3. `test_sdk_to_web_agent_search`：web_agent 的 `web.search` 路径走通。

### 4.3 整仓回归

`pytest -q` → **49 passed**（agents 29 + SDK 20）。

---

## 5. 与方案文档的对齐情况

| 方案-SDK 章节 | 实现位置 | 备注 |
| --- | --- | --- |
| §3 Public API | `__init__.py` | 全部导出 |
| §4 Token Exchange | `client.py:_token_exchange` | 表单字段、grant_type、token_type 全部对齐 |
| §5 DPoP 绑定 | `client.py:97-98` + `dpop.py` | jkt 经 IdP 形成 cnf，invoke 阶段携带 ath |
| §6 错误码 | `errors.py` | `_NO_RETRY` / `_RETRYABLE` 明确划分 |
| §7 AgentServer | `server.py` | 薄封装 `agents.common` |
| §8.1 LangGraph | `adapters/langgraph.py` | `make_a2a_node` |
| §8.2 LangChain | `adapters/langchain.py` | `make_a2a_tool` |
| §8.3 AutoGen | `adapters/autogen.py` | `A2AAgent` |

| 方案-细化 章节 | 对齐项 |
| --- | --- |
| §6.1 自签 client_assertion | iss==sub、`exp ≤ 60s`、kid 在 header |
| §6.2 一次性 delegated token | mock IdP 写入 `one_time:true`；agent 端拒绝缺失 |
| §6.3 DPoP cnf.jkt 绑定 | client_assertion 的 form 上送 `dpop_jkt`，IdP 写入 `cnf.jkt` |
| §13.1 act 链 | mock IdP 写入 `act={"sub":<agent>, "act":null}` |
| §13.2 trace_id / plan_id / task_id | SDK form 透传 + Gateway 头透传 + 测试断言 |

---

## 6. 调试过程要点

1. **`tests/conftest.py` 缺少 `sdk/` 在 `sys.path`** → SDK import 失败。修复：补加 `_SDK = _ROOT / "sdk"` 入 `sys.path`。
2. **mock IdP form-parse 依赖 `python-multipart`** → starlette `request.form()` 触发 `AssertionError`。修复：`pip install python-multipart`（已在 fastapi 生态常见）。
3. **HMAC 短密钥 InsecureKeyLengthWarning** → 仅 mock 路径，生产会用 RS256；忽略。
4. **联调 user token 的 aud** → web_agent 测试需 `aud=agent:web_agent`，`mint_user_token(aud=...)` 直接支持。

---

## 7. 后续可扩展项（不在本次范围）

- IdP `/jwks` 真实暴露 → SDK 切到 RS256 client_assertion；目前仅占位 mock 路径。
- 内置 `tenacity`-style retry 中间件（当前是手写 `invoke_with_retry`）。
- 流式 / SSE 透传（方案-SDK §10 提到，可作为下一里程碑）。
- mTLS / 客户端证书绑定（方案-细化 §6.4 alt）。

---

## 8. 验收命令

```bash
cd /mnt/e/Project/Agent-Token
python -m pytest tests/sdk/ -q          # 20 passed
python -m pytest -q                     # 49 passed (agents + sdk)
```

— 完 —
