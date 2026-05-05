"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import { getPlan } from "@/lib/api";
import type { PlanResult } from "@/types";

function decisionColor(decision?: string) {
  if (decision === "allow") return "#22c55e";
  if (decision === "deny") return "#ef4444";
  return "#94a3b8";
}

function layoutDag(nodes: Node[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 60 });
  nodes.forEach((n) => g.setNode(n.id, { width: 160, height: 64 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  return nodes.map((n) => {
    const p = g.node(n.id);
    return { ...n, position: { x: p.x - 80, y: p.y - 32 } };
  });
}

function buildFromPlan(plan: PlanResult): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = plan.tasks.map((t, i) => ({
    id: t.task_id ?? `t${i}`,
    type: "default",
    data: {
      label: (
        <div className="text-xs text-center leading-tight">
          <div className="font-medium">{t.agent ?? "?"}</div>
          <div className="text-slate-500">{t.action ?? ""}</div>
          {t.latency_ms != null && (
            <div className="text-slate-400">{t.latency_ms}ms</div>
          )}
        </div>
      ),
    },
    position: { x: 0, y: 0 },
    style: {
      border: `2px solid ${decisionColor(t.decision)}`,
      borderRadius: 8,
      background: "white",
      padding: "8px 12px",
      width: 160,
    },
  }));

  const edges: Edge[] = [];
  plan.tasks.forEach((t, i) => {
    if (i > 0) {
      edges.push({
        id: `e-${i}`,
        source: plan.tasks[i - 1].task_id ?? `t${i - 1}`,
        target: t.task_id ?? `t${i}`,
        label: t.decision,
      });
    }
  });

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
      if (p.tasks?.length) {
        const { nodes: n, edges: e } = buildFromPlan(p);
        setNodes(n);
        setEdges(e);
      }
    } catch (e) {
      setError(String(e));
    }
  }, [planId, setNodes, setEdges]);

  useEffect(() => { load(); }, [load]);

  if (error) return <div className="p-4 text-red-600 text-sm">{error}</div>;
  if (!plan) return <div className="p-4 text-slate-400 text-sm animate-pulse">加载中…</div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">计划 ID</div>
          <div className="font-mono text-xs truncate">{plan.plan_id}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">任务数</div>
          <div className="font-semibold">{plan.tasks.length}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">结果</div>
          <div>
            <span className="text-green-600 font-medium">{plan.summary?.allow ?? 0} allow</span>
            {" · "}
            <span className="text-red-500">{plan.summary?.deny ?? 0} deny</span>
          </div>
        </div>
      </div>

      {nodes.length > 0 ? (
        <div className="h-96 border border-slate-200 rounded-xl overflow-hidden bg-white">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            fitView
          >
            <Background />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>
      ) : (
        <div className="h-48 flex items-center justify-center text-slate-400 text-sm border border-dashed border-slate-200 rounded-xl">
          暂无任务数据
        </div>
      )}

      <div className="space-y-1">
        {plan.tasks.map((t, i) => (
          <div key={i} className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-2 text-sm">
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: decisionColor(t.decision) }}
            />
            <span className="font-medium w-32 truncate">{t.task_id ?? `t${i}`}</span>
            <span className="text-slate-500 flex-1">{t.agent} · {t.action}</span>
            <span className="text-slate-400 text-xs">{t.latency_ms != null ? `${t.latency_ms}ms` : ""}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded ${
              t.decision === "allow" ? "bg-green-50 text-green-700" :
              t.decision === "deny" ? "bg-red-50 text-red-600" :
              "bg-slate-50 text-slate-500"
            }`}>
              {t.decision ?? "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
