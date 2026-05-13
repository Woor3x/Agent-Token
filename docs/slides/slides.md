---
theme: seriph
title: A2A-Token-System · 决赛答辩
info: |
  A2A-Token-System · 面向多 Agent 协作的零信任授权平台
  飞书 AI 校园挑战赛 决赛 2026
class: text-center
highlighter: shiki
lineNumbers: false
transition: slide-left
mdc: true
colorSchema: light
fonts:
  sans: 'Inter, PingFang SC, sans-serif'
  mono: 'JetBrains Mono'
download: true
exportFilename: a2a-token-system-defense
drawings:
  persist: false
---

<!-- P1 · 封面 · § 一、 -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 一、</div>

<div class="text-center mt-12">

# A2A-Token-System

<div class="text-2xl text-gray-600 mt-4 mb-12">面向多 Agent 协作的零信任授权平台</div>

<div class="grid grid-cols-3 gap-4 max-w-2xl mx-auto mb-8">
  <div class="p-3 rounded border border-blue-200 bg-blue-50">
    <div class="font-bold text-base">陈奕燔</div>
    <div class="text-xs text-gray-500">组长 · IdP / OPA</div>
  </div>
  <div class="p-3 rounded border border-blue-200 bg-blue-50">
    <div class="font-bold text-base">周展鹏</div>
    <div class="text-xs text-gray-500">Gateway / Web / Audit</div>
  </div>
  <div class="p-3 rounded border border-blue-200 bg-blue-50">
    <div class="font-bold text-base">金梓墨</div>
    <div class="text-xs text-gray-500">Agents / SDK / 飞书</div>
  </div>
</div>

<div class="text-sm text-gray-500">杭州电子科技大学 · 飞书 AI 校园挑战赛 决赛 2026</div>
<div class="text-xs text-gray-400 mt-2">github.com/your-org/A2A-Token-System</div>

</div>

<!--
开场 15s：
- 项目名 · 一句副标点出"零信任 + 多 Agent + 授权"
- 三人分工先快速亮一下
- 然后翻页进入项目结果
-->

---

<!-- P2 · 核心代码模块速览 · § 二-1-1) -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 二-1-1)</div>

# 核心代码模块速览

<div class="grid grid-cols-4 gap-3 mt-6 text-sm">

<div class="p-3 rounded border border-rose-200 bg-rose-50">
  <div class="font-bold">IdP</div>
  <div class="text-xs text-gray-500 font-mono">/token/exchange</div>
  <div class="text-xs mt-1">三验签发委托 token · 按需最小权限</div>
</div>

<div class="p-3 rounded border border-rose-200 bg-rose-50">
  <div class="font-bold">Gateway</div>
  <div class="text-xs text-gray-500 font-mono">authn_middleware</div>
  <div class="text-xs mt-1">唯一入口 JWKS 验签 + per-call OPA 复核</div>
</div>

<div class="p-3 rounded border border-rose-200 bg-rose-50">
  <div class="font-bold">OPA</div>
  <div class="text-xs text-gray-500 font-mono">agent.authz / a2a.rego</div>
  <div class="text-xs mt-1">Rego 10 条全 AND · 决策与代码解耦</div>
</div>

<div class="p-3 rounded border border-amber-200 bg-amber-50">
  <div class="font-bold">Audit API</div>
  <div class="text-xs text-gray-500 font-mono">BatchWriter</div>
  <div class="text-xs mt-1">asyncio.Queue → SQLite 批写 + SSE 广播</div>
</div>

<div class="p-3 rounded border border-emerald-200 bg-emerald-50">
  <div class="font-bold">SDK</div>
  <div class="text-xs text-gray-500 font-mono">client.invoke</div>
  <div class="text-xs mt-1">屏蔽 DPoP + TE · 三框架 adapter</div>
</div>

<div class="p-3 rounded border border-emerald-200 bg-emerald-50">
  <div class="font-bold">doc_assistant</div>
  <div class="text-xs text-gray-500 font-mono">dispatcher._topo_layers</div>
  <div class="text-xs mt-1">LangGraph DAG 拓扑分层并发执行</div>
</div>

<div class="p-3 rounded border border-sky-200 bg-sky-50">
  <div class="font-bold">data_agent / web_agent</div>
  <div class="text-xs text-gray-500 font-mono">tool dispatcher</div>
  <div class="text-xs mt-1">飞书 OpenAPI + Tavily 检索</div>
</div>

<div class="p-3 rounded border border-indigo-200 bg-indigo-50">
  <div class="font-bold">Web 前端</div>
  <div class="text-xs text-gray-500 font-mono">OIDC PKCE</div>
  <div class="text-xs mt-1">RFC 7636 抗授权码截获</div>
</div>

</div>

<div class="text-center text-sm text-gray-500 mt-6">
下一页看 7 模块如何协同 →
</div>

<!--
P2 35s：
- 横扫 7 模块，让评委建立"这是 7 个独立组件的协作"心智
- 配色：红=安全核心、橙=审计、绿=AI编排、蓝=业务Agent、紫=用户前端
- 这套配色 P3 架构图节点继承
- 结束句引向 P3
-->

---

<!-- P3 · 系统架构 · § 二-1-2) 设计 -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 二-1-2)</div>

# 系统架构

```mermaid {scale: 0.55}
flowchart LR
  U[User<br/>Web 前端]:::user
  IDP[IdP<br/>OIDC + TE]:::sec
  GW[Gateway<br/>authn+OPA]:::sec
  OPA[OPA<br/>Rego]:::sec
  AUDIT[Audit API<br/>SQLite+SSE]:::audit
  DOC[doc_assistant<br/>orchestrator]:::ai
  DATA[data_agent<br/>executor]:::ai
  WEB[web_agent<br/>executor]:::ai
  FS[飞书 OpenAPI]:::ext
  TAV[Tavily]:::ext

  U -->|OIDC PKCE| IDP
  U --> GW
  GW -.JWKS.- IDP
  GW -->|per-call decide| OPA
  GW --> DOC
  DOC -->|TE 委托| IDP
  DOC --> GW
  GW --> DATA
  GW --> WEB
  DATA --> FS
  WEB --> TAV
  GW -.记录.- AUDIT
  IDP -.记录.- AUDIT

  classDef sec fill:#fff1f2,stroke:#fb7185,stroke-width:1.5px
  classDef audit fill:#fffbeb,stroke:#fbbf24,stroke-width:1.5px
  classDef ai fill:#ecfdf5,stroke:#34d399,stroke-width:1.5px
  classDef user fill:#eef2ff,stroke:#818cf8,stroke-width:1.5px
  classDef ext fill:#f1f5f9,stroke:#64748b,stroke-width:1px
```

<div class="text-xs text-gray-500 text-center mt-4">
标准协议栈（OIDC + Token Exchange + DPoP）· 职责严格分离（orchestrator / executor 互斥）
</div>

<!--
P3 50s：
- 节点配色与 P2 模块卡片对齐
- 强调三条主线：用户登录、Agent 间委托（TE）、per-call 鉴权
- 引出下一页"三步走"展开数据流
-->

---

<!-- P4 · 系统功能简述 · § 二-1-2) 简述 -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 二-1-2)</div>

# 系统功能简述

<div class="grid grid-cols-3 gap-5 mt-6">

<div class="p-4 rounded-lg border-2 border-indigo-200 bg-indigo-50">
  <div class="text-3xl mb-1">①</div>
  <div class="font-bold text-base mb-2">用户登录</div>
  <div class="text-xs text-gray-700 leading-relaxed">
    OIDC + PKCE（RFC 7636）<br/>
    抗授权码截获<br/>
    一次性 code → access_token
  </div>
</div>

<div class="p-4 rounded-lg border-2 border-rose-200 bg-rose-50">
  <div class="text-3xl mb-1">②</div>
  <div class="font-bold text-base mb-2">Token Exchange</div>
  <div class="text-xs text-gray-700 leading-relaxed">
    RFC 8693 委托链<br/>
    客户端 assertion + DPoP + 上游 token<br/>
    <b>120s 一次性</b> · sub_jti 绑定
  </div>
</div>

<div class="p-4 rounded-lg border-2 border-emerald-200 bg-emerald-50">
  <div class="text-3xl mb-1">③</div>
  <div class="font-bold text-base mb-2">执行鉴权</div>
  <div class="text-xs text-gray-700 leading-relaxed">
    Gateway 验签 + per-call OPA<br/>
    Rego 全 AND 决策<br/>
    Audit 全程记录
  </div>
</div>

</div>

<div class="mt-8 p-3 rounded bg-amber-50 border border-amber-200 text-sm text-center">
🔒 <b>AI 链路</b>（编排 / 调用 / 输出）与 <b>安全链路</b>（IdP / GW / OPA）<b>严格隔离</b> — AI 故障不污染授权决策
</div>

<!--
P4 45s：
- 用三栏对照 P3 三条主线，落到"做什么"
- 强调 ② 的 120s 一次性是核心创新点之一
- 底部隔离提示是技术亮点 — 为 P8 铺垫
-->

---

<!-- P5 · 项目亮点 ① 协议栈 + 零信任 A2A · § 二-1-3) -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 二-1-3) ①</div>

# 亮点 ①：标准协议栈 + 零信任 A2A

<div class="grid grid-cols-6 gap-2 mb-4 text-xs">
  <div class="p-2 rounded border border-gray-300 bg-white text-center">
    <div class="font-bold">RFC 7519</div><div class="text-gray-500">JWT</div>
  </div>
  <div class="p-2 rounded border border-gray-300 bg-white text-center">
    <div class="font-bold">RFC 7523</div><div class="text-gray-500">Client Assertion</div>
  </div>
  <div class="p-2 rounded border border-gray-300 bg-white text-center">
    <div class="font-bold">RFC 7636</div><div class="text-gray-500">PKCE</div>
  </div>
  <div class="p-2 rounded border border-gray-300 bg-white text-center">
    <div class="font-bold">RFC 7638</div><div class="text-gray-500">JWK Thumbprint</div>
  </div>
  <div class="p-2 rounded border-2 border-rose-400 bg-rose-50 text-center">
    <div class="font-bold">RFC 8693</div><div class="text-rose-600">Token Exchange ★</div>
  </div>
  <div class="p-2 rounded border-2 border-rose-400 bg-rose-50 text-center">
    <div class="font-bold">RFC 9449</div><div class="text-rose-600">DPoP ★</div>
  </div>
</div>

```mermaid {scale: 0.55}
sequenceDiagram
  participant C as Caller Agent
  participant I as IdP
  participant G as Gateway
  participant T as Target Agent
  C->>I: client_assertion (60s) + DPoP + 上游 token
  I->>I: 三验：assertion / DPoP / scope ∩
  I-->>C: one-shot token (120s, sub_jti)
  C->>G: 调用 + DPoP proof
  G->>G: JWKS 验签 + cnf.jkt 匹配
  G->>T: 转发
  G-->>Audit: jti / jkt / decision
```

<div class="grid grid-cols-3 gap-3 mt-2 text-xs">
  <div class="p-2 rounded bg-blue-50 border border-blue-200">
    <b>assertion</b> 证身份
  </div>
  <div class="p-2 rounded bg-blue-50 border border-blue-200">
    <b>one-shot token</b> 防重放
  </div>
  <div class="p-2 rounded bg-blue-50 border border-blue-200">
    <b>DPoP</b> 防盗用
  </div>
</div>

<!--
P5 55s：
- 6 RFC 不念，扫一眼即可。重点钉两颗星：8693 与 9449
- sequence 图主讲：IdP 三验 + Gateway cnf.jkt 复验
- 时序图证明：标准协议栈，复用 IETF，没有自创轮子
-->

---

<!-- P6 · 亮点 ② 最小权限 + 三道关 · § 二-1-3) -->

<div class="absolute top-4 right-6 text-xs text-gray-400">§ 二-1-3) ②</div>

# 亮点 ②：最小权限 + 三道关

<div class="grid grid-cols-2 gap-6 mt-4">

<div>
  <div class="text-sm font-bold mb-2 text-gray-700">最小权限计算</div>
  <div class="p-4 rounded-lg bg-gradient-to-br from-purple-50 to-blue-50 border-2 border-purple-200">
    <div class="font-mono text-sm text-center my-2 leading-relaxed">
      <span class="text-purple-700">effective_scope</span> =<br/>
      <span class="text-rose-600">callee_caps</span> ∩<br/>
      <span class="text-emerald-600">user_perms</span> ∩<br/>
      <span class="text-amber-600">requested_scope</span>
    </div>
  </div>
  <div class="text-xs mt-3 space-y-1">
    <div>• <b>callee_caps</b>：被调端能力上限（注册时声明）</div>
    <div>• <b>user_perms</b>：用户授权范围（OIDC consent）</div>
    <div>• <b>requested_scope</b>：本次任务真正需要</div>
  </div>
  <div class="text-xs mt-3 p-2 bg-amber-50 rounded">三者全空 ⇒ 拒签 · 永不"宽给"</div>
</div>

<div>
  <div class="text-sm font-bold mb-2 text-gray-700">三道关防御</div>
  <div class="space-y-2">
    <div class="p-3 rounded border-l-4 border-rose-400 bg-rose-50">
      <div class="font-bold text-sm">① IdP 签发关</div>
      <div class="text-xs text-gray-600">事前 ABAC：subject / agent / action / context</div>
    </div>
    <div class="p-3 rounded border-l-4 border-orange-400 bg-orange-50">
      <div class="font-bold text-sm">② Gateway × OPA 关</div>
      <div class="text-xs text-gray-600">per-call Rego 10 条全 AND 复核</div>
    </div>
    <div class="p-3 rounded border-l-4 border-amber-400 bg-amber-50">
      <div class="font-bold text-sm">③ Agent self-check 关</div>
      <div class="text-xs text-gray-600">不信 Gateway · SDK 内置签名+scope 校验</div>
    </div>
  </div>
</div>

</div>

<!--
P6 55s：
- 公式是"创新性"的钉子 — 三集合交集
- 三道关展示"纵深防御"，回应技术深度评分
- 注意：与 P5 是不同视觉布局（公式 vs 横向卡）避免审美疲劳
-->

---
