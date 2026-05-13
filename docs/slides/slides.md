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
