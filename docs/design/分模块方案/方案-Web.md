# Web UI — 细化方案 v2

> 与 `方案-细化.md` v2 对齐。Next.js 14 App Router + TypeScript。登录走 **本地 IdP OIDC + PKCE** (不直连飞书)。五页: 聊天 / DAG / Trace / 审计 / 撤销面板 (+ 管理员)。

## 1. 组件职责

- 用户登录 (IdP `/oidc/authorize` + PKCE)
- 聊天入口: 调 Gateway `/a2a/nl`，SSE 实时步骤
- DAG 可视化: Gateway `/a2a/plan/status` + Audit `/audit/plans/{id}`
- Trace 树: Audit `/audit/traces/{id}` React Flow
- 审计查询 + 实时流: Audit `/audit/events` + SSE `/audit/stream`
- 撤销面板: 调 IdP `/revoke` (6 粒度)
- 管理员: Agent 注册列表 + 规则配置

## 2. 架构

```
Browser (Next.js 14 App Router, TS)
  │
  │  pages                     components
  │  ├─ /login                 ├─ LoginButton (PKCE)
  │  ├─ /                      ├─ ChatBox + StepProgress
  │  ├─ /plans/[id]            ├─ DagGraph (React Flow)
  │  ├─ /traces/[id]           ├─ TraceTree (React Flow)
  │  ├─ /audit                 ├─ AuditTable + EventStream
  │  ├─ /revoke                ├─ RevokePanel
  │  └─ /admin/agents          └─ AgentRegistry
  │
  ├──▶ IdP    /oidc/authorize + /oidc/token + /revoke
  ├──▶ Gateway /a2a/nl  /a2a/plan/submit  /a2a/plan/{id}/status
  └──▶ Audit  /audit/events  /audit/traces  /audit/plans  /audit/stream (SSE)
```

## 3. 技术栈

| 层 | 选型 |
|---|---|
| 框架 | Next.js 14 (App Router) + TypeScript |
| 状态 | Zustand + SWR |
| 图可视化 | React Flow v12 + dagre 布局 |
| UI | shadcn/ui + Tailwind |
| SSE | 原生 EventSource |
| 认证 | 自实现 PKCE (调本地 IdP) — 不用 next-auth 第三方 provider |
| 图表 | Recharts (stats) |
| i18n | next-intl (zh-CN/en-US) |

## 4. 路由与页面

| 路径 | 组件 | 说明 |
|---|---|---|
| `/login` | LoginPage | 跳转 IdP `/oidc/authorize` + PKCE |
| `/auth/callback` | CallbackPage | 收 code → 换 token → 存 sessionStorage |
| `/` | ChatPage | NL 聊天 + 步骤进度 (SSE) |
| `/plans/[id]` | PlanPage | DAG 可视化 + 子任务状态 |
| `/traces/[id]` | TracePage | trace tree (React Flow) |
| `/audit` | AuditPage | 筛选查询 + 实时流 |
| `/revoke` | RevokePage | 6 粒度撤销 + 撤销历史 |
| `/admin/agents` | AgentsPage | Agent 注册信息 (admin only) |
| `/admin/anomaly` | AnomalyPage | 规则配置 + 告警 |

## 5. 页面设计

### 5.1 `/` 聊天

```
┌──────────────────────────────────────────────────────────┐
│ Agent Token Demo            [Alice ▾] [Plans] [Audit]    │
├──────────────────────────────────────────────────────────┤
│ [Alice] 把 Q1 销售表写成周报                             │
│                                                          │
│ [Agent] 规划中...                                        │
│   ✓ DocAssistant 规划 DAG (3 tasks)  plan_id=plan_abc    │
│   ✓ IdP 预审 plan_allow (overall=allow)                  │
│   ✓ t1: data_agent feishu.bitable.read (tbl_q1) 143ms    │
│   ✓ t2: web_agent  web.search (行业均值) 2100ms          │
│   ✓ t3: doc_assistant feishu.doc.write doc_token:... 520ms │
│ 完成！ [查看 DAG] [查看 Trace] [查看审计]                │
│                                                          │
│ [输入...] [发送]                                          │
└──────────────────────────────────────────────────────────┘
```

### 5.2 `/plans/[id]` DAG

```
                ┌─────────┐
                │ planner │
                └────┬────┘
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
   ┌───────┐    ┌───────┐    ┌───────┐
   │ t1    │    │ t2    │    │       │
   │ data  │    │ web   │    │       │
   │ allow │    │ allow │    │       │
   │ 143ms │    │ 2100ms│    │       │
   └───┬───┘    └───┬───┘    └───────┘
       └───────┬────┘
               ▼
           ┌───────┐
           │ t3    │
           │ docw  │
           │ allow │
           │ 520ms │
           └───────┘
```

右侧 drawer 显示选中 task 的: jti / scope / issued_at / consumed_at / policy_version。

### 5.3 `/traces/[id]` Trace 树

React Flow，节点颜色:
- 绿 = allow
- 红 = deny
- 灰 = revoked
- 紫 = anomaly 触发点

边标注 latency + decision。

### 5.4 `/audit`

```
筛选: event_type[▼] decision[▼] agent[▼] trace_id[___] plan[___] [今天▼] [搜索]
──────────────────────────────────────────────────────────────
时间         | 类型              | 调用→目标         | 决策   | 耗时
10:00:00.123 | authz_decision    | user→doc          | allow  | 500
10:00:00.200 | token_issued      | doc→data (jti=t1) | -      | -
10:00:00.343 | authz_decision    | doc→data          | allow  | 143
10:00:00.345 | token_consumed    | jti=t1            | -      | -
10:00:10.500 | authz_decision    | web→data          | deny   | 7
...
10:00:12.000 | anomaly           | consecutive_deny  | -      | -
10:00:12.050 | revoke_issued     | agent:web_agent   | -      | -
10:00:13.000 | authz_decision    | web→...           | deny   | 2 (revoked)
──────────────────────────────────────────────────────────────
[实时 ●ON] [导出 CSV]              共 1402  ← 1/28 →
```

### 5.5 `/revoke` 撤销面板

```
撤销粒度:  (●) jti  ( ) sub  ( ) agent  ( ) trace  ( ) plan  ( ) chain
值:        [_____________________________]
原因:      [manual/__________________________]
TTL:       [3600]  秒
                                                         [执行撤销]
────────────────────────────────────────────────────────
最近撤销:
  10:00:12  agent:web_agent  anomaly:consecutive_deny  TTL=3600s  by service:anomaly
  09:45:00  jti=abc123       manual:审计要求           TTL=300s   by user:alice
  ...
```

### 5.6 `/admin/agents`

| agent_id | role | kid | 能力数 | accept_from | max_depth | 状态 |
|---|---|---|---|---|---|---|
| doc_assistant | orchestrator | doc_assistant-2025-q1 | 3 | [user] | 1 | active |
| data_agent | executor | data_agent-2025-q1 | 3 | [doc_assistant] | 3 | active |
| web_agent | executor | web_agent-2025-q1 | 2 | [doc_assistant] | 2 | revoked ⚠ |

点击行 → 显示完整 capability.yaml。

## 6. 核心代码

### 6.1 本地 IdP OIDC + PKCE 登录

```typescript
// lib/auth.ts
const IDP = process.env.NEXT_PUBLIC_IDP_URL!;
const CLIENT_ID = "web-ui";
const REDIRECT = `${location.origin}/auth/callback`;

function b64url(buf: Uint8Array) {
  return btoa(String.fromCharCode(...buf)).replace(/=/g,"").replace(/\+/g,"-").replace(/\//g,"_");
}

export async function startLogin() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(verifier))));
  sessionStorage.setItem("pkce_verifier", verifier);
  const url = new URL(`${IDP}/oidc/authorize`);
  url.searchParams.set("response_type","code");
  url.searchParams.set("client_id", CLIENT_ID);
  url.searchParams.set("redirect_uri", REDIRECT);
  url.searchParams.set("scope","openid profile");
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method","S256");
  url.searchParams.set("state", crypto.randomUUID());
  location.href = url.toString();
}

export async function finishLogin(code: string) {
  const verifier = sessionStorage.getItem("pkce_verifier")!;
  const r = await fetch(`${IDP}/oidc/token`, {
    method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body: new URLSearchParams({
      grant_type:"authorization_code",
      code, code_verifier:verifier,
      client_id:CLIENT_ID, redirect_uri:REDIRECT
    })
  });
  const tok = await r.json();
  sessionStorage.setItem("id_token", tok.id_token);
  sessionStorage.setItem("access_token", tok.access_token);
  sessionStorage.setItem("expires_at", String(Date.now()+tok.expires_in*1000));
  return tok;
}

export function userToken(): string | null {
  const t = sessionStorage.getItem("access_token");
  const exp = Number(sessionStorage.getItem("expires_at")||0);
  return t && Date.now() < exp ? t : null;
}
```

### 6.2 NL 聊天 (SSE)

```typescript
// lib/api/nl.ts
export async function sendNL(prompt: string, onStep: (s:any)=>void) {
  const token = userToken(); if (!token) throw new Error("not logged in");
  const r = await fetch(`${GATEWAY}/a2a/nl`, {
    method:"POST",
    headers:{
      "Authorization":`Bearer ${token}`,
      "Content-Type":"application/json",
      "Accept":"text/event-stream"
    },
    body: JSON.stringify({prompt})
  });
  const reader = r.body!.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    buf += dec.decode(value, {stream:true});
    for (const chunk of buf.split("\n\n")) {
      if (!chunk.startsWith("data:")) continue;
      onStep(JSON.parse(chunk.slice(5).trim()));
    }
    buf = buf.slice(buf.lastIndexOf("\n\n")+2);
  }
}
```

### 6.3 Audit SSE

```typescript
// hooks/useAuditStream.ts
export function useAuditStream(filter: Record<string,string> = {}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  useEffect(() => {
    const qs = new URLSearchParams(filter).toString();
    const es = new EventSource(`${AUDIT}/audit/stream?${qs}`, { withCredentials: true });
    es.addEventListener("audit_event", (e:any) => {
      setEvents(prev => [JSON.parse(e.data), ...prev].slice(0, 500));
    });
    return () => es.close();
  }, [JSON.stringify(filter)]);
  return events;
}
```

### 6.4 Trace Tree (React Flow)

```typescript
// components/TraceTree.tsx
import ReactFlow from "reactflow";
import dagre from "@dagrejs/dagre";

function layout(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph().setDefaultEdgeLabel(()=>({}));
  g.setGraph({rankdir:"LR"});
  nodes.forEach(n => g.setNode(n.id,{width:180,height:70}));
  edges.forEach(e => g.setEdge(e.source,e.target));
  dagre.layout(g);
  return {
    nodes: nodes.map(n => ({...n, position:{x:g.node(n.id).x,y:g.node(n.id).y}})),
    edges
  };
}

export function TraceTree({ traceId }: {traceId:string}) {
  const { data } = useSWR(`${AUDIT}/audit/traces/${traceId}`, fetcher);
  if (!data) return <Skeleton/>;
  const {nodes, edges} = layout(...buildGraph(data));
  return <ReactFlow nodes={nodes} edges={edges} fitView nodeTypes={NODE_TYPES}/>;
}
```

### 6.5 Revoke 面板

```typescript
// lib/api/revoke.ts
export async function revoke({type, value, reason, ttl_sec}: RevokeReq) {
  const token = userToken();
  const r = await fetch(`${IDP}/revoke`, {
    method:"POST",
    headers:{
      "Authorization":`Bearer ${token}`,
      "Content-Type":"application/json"
    },
    body: JSON.stringify({type, value, reason, ttl_sec})
  });
  if (!r.ok) throw new Error((await r.json()).error?.message ?? "revoke failed");
  return r.json();
}
```

### 6.6 DAG 可视化

```typescript
// components/DagGraph.tsx
export function DagGraph({ planId }: {planId:string}) {
  const { data } = useSWR(`${AUDIT}/audit/plans/${planId}`, fetcher, {refreshInterval:2000});
  if (!data) return <Skeleton/>;
  const nodes = data.tasks.map((t:any)=>({
    id: t.task_id,
    type: "agentNode",
    data: { label:`${t.task_id}\n${t.agent}\n${t.action}`,
            decision:t.decision, latency:t.latency_ms },
    position:{x:0,y:0}
  }));
  const edges = buildDagEdges(data.tasks);
  return <ReactFlow nodes={layoutDag(nodes)} edges={edges} fitView nodeTypes={NODE_TYPES}/>;
}
```

## 7. Web UI ↔ 后端 API 映射

| UI 动作 | 端点 | 方法 |
|---|---|---|
| 登录 | IdP `/oidc/authorize` + `/oidc/token` | GET/POST |
| 用户信息 | IdP `/oidc/userinfo` | GET |
| NL 聊天 | Gateway `/a2a/nl` | POST (SSE) |
| 提交 DAG | Gateway `/a2a/plan/submit` | POST |
| Plan 状态 | Gateway `/a2a/plan/{id}/status` | GET |
| Plan 审计视图 | Audit `/audit/plans/{id}` | GET |
| Trace 视图 | Audit `/audit/traces/{id}` | GET |
| 审计查询 | Audit `/audit/events` | GET |
| 审计实时流 | Audit `/audit/stream` | SSE |
| 审计统计 | Audit `/audit/stats` | GET |
| 执行撤销 | IdP `/revoke` | POST |
| 撤销状态 | IdP `/revoke/status` | GET |
| Agent 列表 | IdP `/agents` | GET |
| Agent 详情 | IdP `/agents/{id}` | GET |
| 异常规则配置 | Anomaly `/anomaly/rules` + `/anomaly/admin/reload` | GET/POST |
| 异常告警 | Anomaly `/anomaly/alerts` | GET |

## 8. 模块文件映射

```
web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                  # /
│   ├── login/page.tsx
│   ├── auth/callback/page.tsx
│   ├── plans/[id]/page.tsx
│   ├── traces/[id]/page.tsx
│   ├── audit/page.tsx
│   ├── revoke/page.tsx
│   └── admin/
│       ├── agents/page.tsx
│       └── anomaly/page.tsx
├── components/
│   ├── ChatBox.tsx
│   ├── StepProgress.tsx
│   ├── DagGraph.tsx
│   ├── TraceTree.tsx
│   ├── AuditTable.tsx
│   ├── AuditFilter.tsx
│   ├── RevokePanel.tsx
│   ├── AgentRegistry.tsx
│   ├── AnomalyRuleEditor.tsx
│   └── nodes/
│       ├── UserNode.tsx
│       ├── AgentNode.tsx
│       ├── TaskNode.tsx
│       └── AnomalyNode.tsx
├── hooks/
│   ├── useAuditStream.ts
│   ├── useTrace.ts
│   └── usePlan.ts
├── lib/
│   ├── auth.ts                   # PKCE OIDC
│   ├── api/
│   │   ├── nl.ts
│   │   ├── plan.ts
│   │   ├── audit.ts
│   │   ├── revoke.ts
│   │   └── idp.ts
│   └── util.ts
├── types/
│   ├── audit.ts
│   ├── trace.ts
│   ├── plan.ts
│   └── agent.ts
├── public/
├── next.config.mjs
├── tailwind.config.ts
└── package.json
```

## 9. 环境变量

```bash
# web/.env.local
NEXT_PUBLIC_IDP_URL=http://localhost:8000
NEXT_PUBLIC_GATEWAY_URL=http://localhost:8001
NEXT_PUBLIC_AUDIT_API_URL=http://localhost:8004
NEXT_PUBLIC_ANOMALY_URL=http://localhost:8005

NEXT_PUBLIC_OIDC_CLIENT_ID=web-ui
# 开发模式跳过 OIDC (内置 mock user)
NEXT_PUBLIC_MOCK_AUTH=false
```

**注**: v2 下不再用飞书 OIDC。用户身份由本地 IdP 颁发 (演示预置用户 alice/bob)。

## 10. 启动

```bash
cd web
nvm use 20
pnpm install
pnpm dev                  # localhost:3000
pnpm build && pnpm start  # 生产
```

docker-compose:
```yaml
web:
  build: ./web
  ports: ["3000:3000"]
  environment:
    - NEXT_PUBLIC_IDP_URL=http://idp:8000
    - NEXT_PUBLIC_GATEWAY_URL=http://gateway:8001
    - NEXT_PUBLIC_AUDIT_API_URL=http://audit-api:8004
    - NEXT_PUBLIC_ANOMALY_URL=http://anomaly:8005
  depends_on: [idp, gateway, audit-api, anomaly]
```

## 11. 演示要点 (对应场景 A-F)

| 场景 | 展示 | 页面 |
|---|---|---|
| A 正常流程 | 聊天输入 → 步骤逐条打勾 → 完成链接 | `/` |
| B 越权 | web_agent 视角调 data_agent → 红色 deny 节点 + reason=executor_mismatch | `/traces/...` |
| C 撤销 | 点 sub=user:alice → 后续请求 AUTHN_REVOKED | `/revoke` + `/audit` |
| D DPoP 重放 | 触发 DPoP jti 重放 → deny 红线 | `/audit` |
| E 异常检测 | 5 次连续 deny → anomaly 紫色节点 → agent revoked 灰色 | `/audit` + `/traces/...` |
| F 并行 DAG | 3 任务 fan-out → DAG 图并行节点 | `/plans/...` |

## 12. 性能目标

| 指标 | 目标 |
|---|---|
| 首屏 (TTFB) | < 300ms |
| SSE 事件渲染延迟 | < 100ms |
| Trace 图布局 (20 spans) | < 150ms |
| DAG 刷新轮询 | 2s |
| 审计表格虚拟化 (10k 行) | 流畅滚动 |

## 13. 契约

| Web UI → 后端 | 认证 |
|---|---|
| IdP `/oidc/*` | PKCE |
| IdP `/revoke`, `/agents` | Bearer access_token (user) |
| Gateway `/a2a/nl`, `/a2a/plan/*` | Bearer access_token |
| Audit `/audit/*` | Bearer access_token (admin scope) |
| Anomaly `/anomaly/*` | Bearer access_token (admin scope) |

| 后端 → Web UI | 说明 |
|---|---|
| SSE `/audit/stream` | Bearer in query (EventSource 限制) 或 fetch stream |
| SSE `/a2a/nl` | 同 |
