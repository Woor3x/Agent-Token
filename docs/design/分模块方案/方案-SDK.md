# Agent Token SDK — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。**无 client_secret / client_credentials**。SDK = `AgentClient` (调用侧: 自签 Client Assertion → IdP Token Exchange → DPoP → Gateway) + `AgentServer` (服务侧 FastAPI 辅助 + 二次验签) + LangGraph/LangChain/AutoGen 适配器。每次调用一张一次性 token。

## 1. 职责

- **调用侧** `AgentClient.invoke(...)`: 自签 RFC 7523 Client Assertion → 调 IdP `/token/exchange` 换一次性 delegated token → 生成 DPoP proof → POST Gateway `/a2a/invoke`
- **服务侧** `AgentServer.create_app(handler)`: 暴露 `/invoke` + 验入 token + 执行 handler
- **DAG** `AgentClient.plan_validate(plan)`: 可选预审调用 IdP `/plan/validate`
- **适配器**: LangGraph node / LangChain tool / AutoGen agent
- **零 client_secret**: 身份仅由 private key 证明

## 2. 架构

```
┌────────────────────────────────────────────────────────────┐
│                  agent_token_sdk                           │
│                                                            │
│  调用侧  AgentClient                 服务侧  AgentServer    │
│  ┌───────────────────────┐          ┌────────────────────┐ │
│  │ invoke(target, intent)│          │ create_app(handler)│ │
│  │  ├─ AssertionSigner   │          │  ├─ /invoke route  │ │
│  │  │  (RFC 7523)        │          │  ├─ JWKS 验签      │ │
│  │  ├─ TokenExchanger    │          │  ├─ DPoP 校验(opt) │ │
│  │  │  → IdP /token/exch │          │  └─ handler()      │ │
│  │  ├─ DPoPSigner        │          └────────────────────┘ │
│  │  └─ HTTP → Gateway    │                                 │
│  └───────────────────────┘          适配器: LG / LC / AG   │
└────────────────────────────────────────────────────────────┘
```

## 3. 快速上手

### 3.1 调用侧

```python
from agent_token_sdk import AgentClient

client = AgentClient(
    agent_id="doc_assistant",
    private_key_path="/keys/doc_assistant.pem",
    kid="doc_assistant-2025-q1",
    idp_url="https://idp.local",
    gateway_url="https://gateway.local",
)

# 在 orchestrator 内部，on_behalf_of = user 的 delegated token
result = await client.invoke(
    target="data_agent",
    intent={
        "action":"feishu.bitable.read",
        "resource":"app_token:bascn_alice/table:tbl_q1",
        "params":{"page_size":100}
    },
    on_behalf_of=user_token,            # RFC 8693 subject_token
    purpose="generate_weekly_report",
    plan_id="plan_...", task_id="t1",
    trace_id="01HXYZ...",
)
print(result["data"])
```

### 3.2 服务侧

```python
from agent_token_sdk import AgentServer

async def handler(body, claims):
    action = body["intent"]["action"]
    ...
    return {"rows": [...]}

app = AgentServer(agent_id="data_agent",
                  idp_jwks_url="https://idp.local/jwks",
                  handler=handler).create_app()
# uvicorn app:app --port 8002
```

## 4. `AgentClient` 实现

```python
# sdk/agent_token_sdk/client.py
import httpx, time, uuid
from .assertion import AssertionSigner
from .dpop import DPoPSigner

class AgentClient:
    def __init__(self, agent_id, private_key_path, kid,
                 idp_url, gateway_url):
        self.agent_id    = agent_id
        self.idp_url     = idp_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self._assertion  = AssertionSigner(agent_id, private_key_path, kid, self.idp_url)
        self._dpop       = DPoPSigner(private_key_path, kid)
        self._http       = httpx.AsyncClient(timeout=30, verify=True)

    async def invoke(self, target, intent, *, on_behalf_of,
                     purpose="", plan_id=None, task_id=None, trace_id=None,
                     idempotency_key=None):
        # 1. 自签 Client Assertion (RFC 7523)
        assertion = self._assertion.sign(
            aud=f"{self.idp_url}/token/exchange",
            exp_delta=60)

        # 2. 调 IdP /token/exchange 换 delegated token (RFC 8693)
        scope = f"{intent['action']}:{intent['resource']}"
        r = await self._http.post(f"{self.idp_url}/token/exchange", data={
            "grant_type":"urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": on_behalf_of,
            "subject_token_type":"urn:ietf:params:oauth:token-type:access_token",
            "actor_token": assertion,
            "actor_token_type":"urn:ietf:params:oauth:token-type:jwt",
            "audience": f"agent:{target}",
            "scope": scope,
            "resource": intent["resource"],
            "purpose": purpose,
            "plan_id": plan_id or "",
            "task_id": task_id or "",
            "trace_id": trace_id or "",
            "dpop_jkt": self._dpop.jkt_b64u,
        })
        if r.status_code != 200:
            raise TokenExchangeError(r.status_code, r.text)
        delegated = r.json()["access_token"]

        # 3. 生成 DPoP proof (绑 Gateway /a2a/invoke URL + at hash)
        url = f"{self.gateway_url}/a2a/invoke"
        dpop = self._dpop.sign(url=url, method="POST", access_token=delegated)

        # 4. 调 Gateway
        headers = {
            "Authorization": f"DPoP {delegated}",
            "DPoP": dpop,
            "X-Target-Agent": target,
            "Content-Type": "application/json",
        }
        if trace_id:         headers["Traceparent"] = f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"
        if plan_id:          headers["X-Plan-Id"] = plan_id
        if task_id:          headers["X-Task-Id"] = task_id
        if idempotency_key:  headers["X-Idempotency-Key"] = idempotency_key

        body = {"intent": intent}
        if idempotency_key: body["idempotency_key"] = idempotency_key

        resp = await self._http.post(url, json=body, headers=headers)
        if resp.status_code != 200:
            err = resp.json().get("error", {})
            raise A2AError(err.get("code","UNKNOWN"), err.get("message",""),
                           trace_id=err.get("trace_id"))
        return resp.json()

    async def plan_validate(self, plan, user_token, trace_id=None):
        # 调 IdP /plan/validate 预审
        assertion = self._assertion.sign(aud=f"{self.idp_url}/plan/validate", exp_delta=60)
        r = await self._http.post(f"{self.idp_url}/plan/validate",
            headers={"Authorization": f"Bearer {user_token}",
                     "X-Actor-Assertion": assertion,
                     "X-Trace-Id": trace_id or ""},
            json={"orchestrator": self.agent_id, "plan": plan})
        r.raise_for_status()
        return r.json()

    async def close(self):
        await self._http.aclose()
    async def __aenter__(self): return self
    async def __aexit__(self, *a): await self.close()
```

## 5. `AssertionSigner` (RFC 7523 Client Assertion)

```python
# sdk/agent_token_sdk/assertion.py
import time, uuid, jwt
from cryptography.hazmat.primitives import serialization

class AssertionSigner:
    def __init__(self, agent_id, private_key_path, kid, idp_url):
        self.agent_id = agent_id
        self.kid      = kid
        with open(private_key_path,"rb") as f:
            self._pk = serialization.load_pem_private_key(f.read(), password=None)

    def sign(self, aud, exp_delta=60):
        now = int(time.time())
        payload = {
            "iss": self.agent_id,
            "sub": self.agent_id,
            "aud": aud,
            "iat": now,
            "nbf": now,
            "exp": now + exp_delta,     # ≤60s 强约束
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, self._pk, algorithm="RS256",
                          headers={"kid": self.kid, "typ":"JWT"})
```

## 6. `DPoPSigner`

```python
# sdk/agent_token_sdk/dpop.py
import time, uuid, hashlib, base64, jwt
from cryptography.hazmat.primitives import serialization

def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

class DPoPSigner:
    def __init__(self, private_key_path, kid):
        with open(private_key_path,"rb") as f:
            self._pk = serialization.load_pem_private_key(f.read(), password=None)
        self.kid = kid
        self._jwk = self._public_jwk()
        # jkt = SHA-256 of JWK (RFC 7638 canonical)
        canonical = f'{{"e":"{self._jwk["e"]}","kty":"RSA","n":"{self._jwk["n"]}"}}'
        self.jkt_b64u = b64url(hashlib.sha256(canonical.encode()).digest())

    def _public_jwk(self):
        pub = self._pk.public_key().public_numbers()
        def i2b(n): return b64url(n.to_bytes((n.bit_length()+7)//8, "big"))
        return {"kty":"RSA","alg":"RS256","n":i2b(pub.n),"e":i2b(pub.e),"kid":self.kid}

    def sign(self, url, method, access_token=None):
        now = int(time.time())
        payload = {"htu":url, "htm":method.upper(), "iat":now, "jti":str(uuid.uuid4())}
        if access_token:
            ath = b64url(hashlib.sha256(access_token.encode()).digest())
            payload["ath"] = ath
        headers = {"typ":"dpop+jwt","alg":"RS256","jwk":self._jwk}
        return jwt.encode(payload, self._pk, algorithm="RS256", headers=headers)
```

## 7. `AgentServer` (服务侧)

```python
# sdk/agent_token_sdk/server.py
import httpx, jwt
from cachetools import TTLCache
from fastapi import FastAPI, Request, HTTPException

_jwks = TTLCache(maxsize=10, ttl=600)

class AgentServer:
    def __init__(self, agent_id, idp_jwks_url, handler, verify_dpop=False):
        self.agent_id     = agent_id
        self.idp_jwks_url = idp_jwks_url
        self.handler      = handler
        self.verify_dpop  = verify_dpop   # 内网可关；Gateway 已验过一轮

    async def _get_jwk(self, kid):
        if kid in _jwks: return _jwks[kid]
        async with httpx.AsyncClient() as c:
            r = await c.get(self.idp_jwks_url); r.raise_for_status()
            for k in r.json()["keys"]:
                _jwks[k["kid"]] = k
        return _jwks.get(kid)

    async def verify_incoming(self, req: Request):
        auth = req.headers.get("Authorization","")
        if not auth.startswith("DPoP "):
            raise HTTPException(401, {"code":"AUTHN_TOKEN_INVALID","message":"no DPoP bearer"})
        token = auth[5:]
        hdr = jwt.get_unverified_header(token)
        key = await self._get_jwk(hdr.get("kid"))
        if not key:
            raise HTTPException(401, {"code":"AUTHN_TOKEN_INVALID","message":"unknown kid"})
        try:
            claims = jwt.decode(token, jwt.PyJWK(key).key, algorithms=["RS256"],
                                audience=f"agent:{self.agent_id}", leeway=30)
        except Exception as e:
            raise HTTPException(401, {"code":"AUTHN_TOKEN_INVALID","message":str(e)})
        if not claims.get("one_time"):
            raise HTTPException(401, {"code":"AUTHN_TOKEN_INVALID","message":"not one_time"})
        # DPoP 内网可选验 (默认关；Gateway 已验)
        if self.verify_dpop:
            self._verify_dpop_proof(req, claims)
        return claims

    def create_app(self):
        app = FastAPI(title=f"agent:{self.agent_id}")
        @app.post("/invoke")
        async def invoke(req: Request):
            claims = await self.verify_incoming(req)
            body = await req.json()
            try:
                data = await self.handler(body, claims)
            except PermissionError as e:
                raise HTTPException(403, {"code":"AGENT_FORBIDDEN","message":str(e)})
            except Exception as e:
                raise HTTPException(500, {"code":"AGENT_INTERNAL_ERROR","message":str(e)})
            return {"status":"ok","data":data,"trace_id":claims.get("trace_id")}
        @app.get("/healthz")
        async def health(): return {"status":"ok","agent":self.agent_id}
        return app
```

## 8. 适配器

### 8.1 LangGraph

```python
# sdk/agent_token_sdk/adapters/langgraph.py
def make_a2a_node(client, target):
    async def node(state):
        res = await client.invoke(
            target=target, intent=state["intent"],
            on_behalf_of=state["user_token"],
            purpose=state.get("purpose",""),
            plan_id=state.get("plan_id"), task_id=state.get("task_id"),
            trace_id=state.get("trace_id"))
        return {**state, "a2a_result": res["data"]}
    return node
```

### 8.2 LangChain Tool

```python
# sdk/agent_token_sdk/adapters/langchain.py
from langchain_core.tools import tool

def make_a2a_tool(client, target, description, ctx_provider):
    @tool(description=description)
    async def a2a_tool(action: str, resource: str, params: dict = {}) -> str:
        ctx = ctx_provider()                  # {user_token, plan_id, task_id, trace_id}
        res = await client.invoke(
            target=target,
            intent={"action":action,"resource":resource,"params":params},
            **ctx)
        return json.dumps(res["data"])
    a2a_tool.name = f"call_{target}"
    return a2a_tool
```

### 8.3 AutoGen

```python
# sdk/agent_token_sdk/adapters/autogen.py
from autogen import ConversableAgent

class A2AAgent(ConversableAgent):
    def __init__(self, agent_id, target, client, ctx_provider, **kw):
        super().__init__(name=agent_id, **kw)
        self._client, self._target, self._ctx = client, target, ctx_provider
    async def a2a_invoke(self, intent, purpose=""):
        ctx = self._ctx()
        return await self._client.invoke(target=self._target, intent=intent,
                                         purpose=purpose, **ctx)
```

## 9. 错误类型

```python
# sdk/agent_token_sdk/errors.py
class A2AError(Exception):
    def __init__(self, code, message, trace_id=None):
        self.code, self.message, self.trace_id = code, message, trace_id
        super().__init__(f"[{code}] {message} (trace={trace_id})")
class TokenExchangeError(Exception): ...
class AssertionSignError(Exception): ...
class DPoPSignError(Exception): ...
```

重试策略 (内置):

| code | 重试 |
|---|---|
| `AUTHN_TOKEN_INVALID` | 不重试 (每次都是一次性新 token) |
| `AUTHN_REVOKED` / `AGENT_REVOKED` | 不重试 |
| `TOKEN_REPLAYED` | 不重试 (SETNX 失败代表撞车) |
| `AUTHZ_*` | 不重试 |
| `RATE_LIMITED` | 指数退避 最多 3 次 |
| `CIRCUIT_OPEN` | 退避 30s |
| `UPSTREAM_TIMEOUT` / 502 / 503 | 指数退避 最多 2 次 |

## 10. SDK HTTP 行为总表

| 场景 | 出站顺序 |
|---|---|
| `invoke()` | 1. IdP `/token/exchange` (Client Assertion) → 2. Gateway `/a2a/invoke` (DPoP) |
| `plan_validate()` | IdP `/plan/validate` (user token + assertion) |
| `AgentServer` 验入 | 拉 `/jwks` (LRU cache 10min) |

## 11. 模块文件映射

```
sdk/agent_token_sdk/
├── __init__.py              # 导出 AgentClient, AgentServer, A2AError, ...
├── client.py                # AgentClient
├── server.py                # AgentServer
├── assertion.py             # AssertionSigner (RFC 7523)
├── dpop.py                  # DPoPSigner
├── errors.py
└── adapters/
    ├── langgraph.py
    ├── langchain.py
    └── autogen.py
```

## 12. 安装与配置

```bash
pip install -e sdk/
# 或
uv pip install -e sdk/
```

环境变量 (可选，用于简化 `AgentClient(...)`):

| 变量 | 说明 |
|---|---|
| `AGENT_ID` | agent_id |
| `AGENT_PRIVATE_KEY_PATH` | 私钥路径 (RFC 7523 签名 + DPoP 共用) |
| `AGENT_KID` | 公钥 kid (对应 IdP 注册的 public_key_jwk.kid) |
| `IDP_URL` | IdP base URL |
| `GATEWAY_URL` | Gateway base URL |

**注**: SDK 不再读取 `AGENT_CLIENT_SECRET`；v2 下密码凭据不存在。

## 13. 性能目标

| 指标 | 目标 |
|---|---|
| `invoke()` 端到端 (含 Token Exchange + Gateway + Agent 处理) p99 | < 100ms + 业务耗时 |
| `/token/exchange` 往返 p99 | < 30ms |
| DPoP 签名耗时 | < 3ms |
| Assertion 签名耗时 | < 3ms |
| JWKS LRU hit rate | > 99% |

## 14. 契约

| SDK → 外部 | 认证 |
|---|---|
| IdP `/token/exchange` | Client Assertion (RFC 7523) + subject_token (user/orchestrator delegated token) |
| IdP `/plan/validate` | user_token + Actor Assertion |
| IdP `/jwks` | 公开 |
| Gateway `/a2a/invoke` | Delegated Token + DPoP proof |

| 外部 → SDK (AgentServer) | 认证 |
|---|---|
| Gateway (mTLS) → `/invoke` | 转发的 delegated token + DPoP |
