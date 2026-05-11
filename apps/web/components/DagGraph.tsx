"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import { getPlan } from "@/lib/api";
import type { PlanResult } from "@/types";

// ── Action → human-readable Chinese label ────────────────────────────────────
const ACTION_LABEL: Record<string, string> = {
  "feishu.bitable.read":  "读取飞书多维表",
  "feishu.bitable.list":  "列出飞书多维表",
  "feishu.docx.read":     "读取飞书文档",
  "feishu.docx.write":    "写入飞书文档",
  "feishu.drive.list":    "列出云盘文件",
  "feishu.contact.read":  "读取通讯录",
  "feishu.calendar.read": "读取日历",
  "web.search":           "网络检索",
  "web.fetch":            "抓取网页",
  "a2a.invoke":           "A2A 调用",
};

function actionLabel(action?: string): string {
  if (!action) return "未知动作";
  return ACTION_LABEL[action] ?? action;
}

// ── Agent → display name + tint ──────────────────────────────────────────────
const AGENT_TINT: Record<string, { bg: string; border: string; text: string }> = {
  user:          { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" },
  doc_assistant: { bg: "#e0e7ff", border: "#6366f1", text: "#3730a3" },
  data_agent:    { bg: "#dcfce7", border: "#16a34a", text: "#166534" },
  web_agent:     { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" },
};

const DEFAULT_TINT = { bg: "#f1f5f9", border: "#94a3b8", text: "#475569" };
function tintFor(agent?: string | null): { bg: string; border: string; text: string } {
  if (agent && AGENT_TINT[agent]) return AGENT_TINT[agent];
  return DEFAULT_TINT;
}

function decisionPillClass(decision?: string) {
  if (decision === "allow") return "bg-green-100 text-green-700";
  if (decision === "deny") return "bg-red-100 text-red-700";
  return "bg-slate-100 text-slate-500";
}

function decisionEdgeColor(decision?: string) {
  if (decision === "allow") return "#22c55e";
  if (decision === "deny") return "#ef4444";
  return "#94a3b8";
}

// ── Frame node builder (user / planner / synthesizer / writer) ───────────────
function frameNode(
  id: string,
  role: string,
  agent: string,
  desc: string,
  tintKey?: string,
): Node {
  // The legend explains role→color (user is yellow, orchestrator purple…).
  // Frame nodes carry an explicit `tintKey` so they don't degrade to the
  // grey default when the agent display name (e.g. "alice") isn't in the
  // AGENT_TINT lookup.
  const t = tintFor(tintKey ?? agent);
  return {
    id,
    type: "default",
    data: {
      label: (
        <div className="text-center leading-tight">
          <div className="text-[10px] font-medium opacity-70" style={{ color: t.text }}>
            {role}
          </div>
          <div className="font-semibold text-sm" style={{ color: t.text }}>
            {agent}
          </div>
          <div className="text-[10px] opacity-80 mt-0.5" style={{ color: t.text }}>
            {desc}
          </div>
        </div>
      ),
    },
    position: { x: 0, y: 0 },
    style: {
      border: `2px solid ${t.border}`,
      background: t.bg,
      borderRadius: 10,
      padding: "10px 14px",
      width: 200,
    },
  };
}

function layoutDag(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 80, ranksep: 70, marginx: 20, marginy: 20 });
  nodes.forEach((n) => {
    const w = (n.style?.width as number) ?? 180;
    g.setNode(n.id, { width: w, height: 70 });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    const w = (n.style?.width as number) ?? 180;
    return { ...n, position: { x: p.x - w / 2, y: p.y - 35 } };
  });
}

function buildFromPlan(plan: PlanResult): { nodes: Node[]; edges: Edge[] } {
  const orch = plan.orchestrator ?? "doc_assistant";
  const user = plan.user ?? "user";
  const taskNodeId = (i: number) => `task-${i}`;

  // ── Frame: user → planner → [validate] → [tasks] → synthesizer → doc_writer
  const nodes: Node[] = [
    frameNode("user", "用户请求", user, "发起对话", "user"),
    frameNode("planner", "① 规划", orch, "LLM 生成 DAG", "doc_assistant"),
  ];

  // The audit-api emits a synthetic "plan.validate" placeholder row at the
  // head of `tasks` (task_id=null, agent=null, action=null) that carries the
  // OPA decision for the generated plan. Surface it as a visible node so a
  // deny on plan validation doesn't disappear from the diagram.
  const validateRow = plan.tasks.find(
    (t) => t.agent == null && t.action == null && t.task_id == null,
  );
  const realTasks = plan.tasks.filter(
    (t) => t.agent != null || t.action != null,
  );

  if (validateRow) {
    const dec = validateRow.decision;
    const col = decisionEdgeColor(dec);
    nodes.push({
      id: "plan-validate",
      type: "default",
      data: {
        label: (
          <div className="text-center leading-tight">
            <div className="text-[10px] font-medium opacity-70 text-indigo-900">
              ② OPA 校验
            </div>
            <div className="font-semibold text-sm text-indigo-900">
              plan.validate
            </div>
            <div className="flex items-center justify-center mt-1">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${decisionPillClass(dec)}`}>
                {dec ?? "pending"}
              </span>
            </div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: {
        border: `2px solid ${dec === "deny" ? "#ef4444" : "#6366f1"}`,
        background: "#e0e7ff",
        borderRadius: 10,
        padding: "10px 14px",
        width: 200,
        boxShadow: dec === "deny" ? `0 0 0 3px ${col}33` : undefined,
      },
    });
  }

  realTasks.forEach((t, i) => {
    const tint = tintFor(t.agent);
    const decColor = decisionEdgeColor(t.decision);
    const isHotBorder = t.decision === "deny";
    const taskTag = t.task_id ?? `t${i}`;
    nodes.push({
      id: taskNodeId(i),
      type: "default",
      data: {
        label: (
          <div className="text-center leading-tight" title={`${t.agent ?? "?"} · ${t.action ?? ""}`}>
            <div className="flex items-center justify-between gap-1 mb-1">
              <span
                className="text-[10px] font-mono px-1 rounded bg-white/70"
                style={{ color: tint.text }}
              >
                {taskTag}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${decisionPillClass(t.decision)}`}>
                {t.decision ?? "pending"}
              </span>
            </div>
            <div className="font-semibold text-sm" style={{ color: tint.text }}>
              {actionLabel(t.action)}
            </div>
            <div className="text-[11px] mt-0.5" style={{ color: tint.text, opacity: 0.85 }}>
              {t.agent ?? "?"}
            </div>
            <div className="text-[10px] text-slate-500 mt-0.5 truncate">
              {t.latency_ms != null ? `${t.latency_ms}ms` : "—"}
              {t.jti && (
                <>
                  <span className="mx-1 opacity-50">·</span>
                  <span className="font-mono opacity-70">{t.jti.slice(0, 6)}</span>
                </>
              )}
            </div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: {
        border: `2px solid ${isHotBorder ? "#ef4444" : tint.border}`,
        background: tint.bg,
        borderRadius: 10,
        padding: "10px 14px",
        width: 200,
        boxShadow: t.decision === "allow" ? `0 0 0 2px ${decColor}22` : undefined,
      },
    });
  });

  nodes.push(
    frameNode("synth", "③ 合成", orch, "汇总结果生成报告", "doc_assistant"),
    frameNode("writer", "④ 写入", orch, "落盘文档", "doc_assistant"),
  );

  // ── Edges ─────────────────────────────────────────────────────────────────
  const edges: Edge[] = [];
  const addEdge = (id: string, source: string, target: string, label?: string, color = "#94a3b8") => {
    edges.push({
      id,
      source,
      target,
      label,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: 1.8 },
      labelStyle: { fontSize: 10, fill: color },
      labelBgStyle: { fill: "white", fillOpacity: 0.95 },
    });
  };

  addEdge("e-u-p", "user", "planner", "prompt");

  // Insert plan-validate between planner and tasks if present.
  const upstreamForTasks = validateRow ? "plan-validate" : "planner";
  if (validateRow) {
    const vcol = decisionEdgeColor(validateRow.decision);
    addEdge("e-p-v", "planner", "plan-validate", "validate", vcol);
    // Deny on plan-validate halts dispatch — show that visually with a
    // dashed "halt" edge that bypasses tasks.
    if (validateRow.decision === "deny") {
      addEdge("e-v-halt", "plan-validate", "synth", "halted", "#ef4444");
    }
  }

  if (realTasks.length === 0) {
    if (!validateRow || validateRow.decision !== "deny") {
      addEdge("e-p-s", upstreamForTasks, "synth", "(no tasks)");
    }
  } else {
    realTasks.forEach((t, i) => {
      const tid = taskNodeId(i);
      const c = decisionEdgeColor(t.decision);
      addEdge(`e-p-${i}`, upstreamForTasks, tid, "dispatch", c);
      addEdge(`e-${i}-s`, tid, "synth", t.decision ?? undefined, c);
    });
  }

  addEdge("e-s-w", "synth", "writer", "markdown");

  return { nodes: layoutDag(nodes, edges), edges };
}

export default function DagGraph({ planId }: { planId: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const p = await getPlan(planId);
      setPlan(p);
      const { nodes: n, edges: e } = buildFromPlan(p);
      setNodes(n);
      setEdges(e);
    } catch (e) {
      setError(String(e));
    }
  }, [planId, setNodes, setEdges]);

  useEffect(() => { load(); }, [load]);

  if (error) return <div className="p-4 text-red-600 text-sm">{error}</div>;
  if (!plan) return <div className="p-4 text-slate-400 text-sm animate-pulse">加载中…</div>;

  const realTasks = plan.tasks.filter((t) => t.agent != null || t.action != null);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">计划 ID</div>
          <div className="font-mono text-xs truncate">{plan.plan_id}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">用户 → 编排器</div>
          <div className="font-medium">
            {plan.user ?? "user"} → {plan.orchestrator ?? "doc_assistant"}
          </div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">下游任务 · 决策</div>
          <div>
            <span className="font-semibold">{realTasks.length}</span>
            <span className="text-slate-400 mx-1">·</span>
            <span className="text-green-600 font-medium">{plan.summary?.allow ?? 0} allow</span>
            {" · "}
            <span className="text-red-500">{plan.summary?.deny ?? 0} deny</span>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 text-xs items-center text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        <span className="font-medium">图例：</span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#fef3c7", border: "1px solid #f59e0b" }} /> 用户
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#e0e7ff", border: "1px solid #6366f1" }} /> 编排器 doc_assistant
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#dcfce7", border: "1px solid #16a34a" }} /> 数据 data_agent
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#dbeafe", border: "1px solid #3b82f6" }} /> 网络 web_agent
        </span>
        <span className="ml-auto text-slate-500">绿线 = allow · 红线 = deny · 灰线 = pending</span>
      </div>

      {nodes.length > 0 ? (
        <div className="h-[520px] border border-slate-200 rounded-xl overflow-hidden bg-white">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            fitView
            fitViewOptions={{ padding: 0.2 }}
          >
            <Background />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
      ) : (
        <div className="h-48 flex items-center justify-center text-slate-400 text-sm border border-dashed border-slate-200 rounded-xl">
          暂无任务数据
        </div>
      )}

      <div className="space-y-1">
        <div className="text-xs text-slate-500 px-1">下游任务详情</div>
        {realTasks.length === 0 ? (
          <div className="text-xs text-slate-400 px-1">本次计划没有派发下游任务（只在编排器内部处理）</div>
        ) : (
          realTasks.map((t, i) => (
            <div key={i} className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-2 text-sm">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: decisionEdgeColor(t.decision) }}
              />
              <span className="font-medium w-44 truncate">{actionLabel(t.action)}</span>
              <span className="text-slate-500 flex-1 truncate">
                {t.agent ?? "?"} <span className="text-slate-300">·</span>{" "}
                <span className="font-mono text-xs text-slate-400">{t.action ?? ""}</span>
              </span>
              <span className="text-slate-400 text-xs">{t.latency_ms != null ? `${t.latency_ms}ms` : ""}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded ${decisionPillClass(t.decision)}`}>
                {t.decision ?? "—"}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
