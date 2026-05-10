"use client";

import { useEffect, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import { getTrace } from "@/lib/api";
import type { TraceSpan, TraceResult } from "@/types";
import { useState } from "react";

function decisionColor(d?: string | null) {
  if (d === "allow") return "#22c55e";
  if (d === "deny") return "#ef4444";
  return "#94a3b8";
}

function flattenSpans(spans: TraceSpan[]): TraceSpan[] {
  const result: TraceSpan[] = [];
  function walk(s: TraceSpan) {
    result.push(s);
    s.children.forEach(walk);
  }
  spans.forEach(walk);
  return result;
}

function buildGraph(result: TraceResult): { nodes: Node[]; edges: Edge[] } {
  const all = flattenSpans(result.spans);

  const nodes: Node[] = all.map((s) => ({
    id: s.span_id,
    type: "default",
    data: {
      label: (
        <div className="text-xs text-center leading-tight">
          <div className="font-medium truncate max-w-[130px]">{s.callee ?? s.caller ?? s.span_id}</div>
          {s.latency_ms != null && <div className="text-slate-400">{s.latency_ms}ms</div>}
        </div>
      ),
    },
    position: { x: 0, y: 0 },
    style: {
      border: `2px solid ${decisionColor(s.decision)}`,
      borderRadius: 8,
      background: "white",
      padding: "6px 10px",
      width: 150,
    },
  }));

  const edges: Edge[] = all
    .filter((s) => s.parent_span_id)
    .map((s) => ({
      id: `e-${s.span_id}`,
      source: s.parent_span_id!,
      target: s.span_id,
      label: s.decision,
    }));

  // dagre layout
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 50, ranksep: 80 });
  nodes.forEach((n) => g.setNode(n.id, { width: 150, height: 60 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return {
    nodes: nodes.map((n) => {
      const p = g.node(n.id);
      return { ...n, position: { x: p.x - 75, y: p.y - 30 } };
    }),
    edges,
  };
}

export default function TraceTree({ traceId }: { traceId: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const t = await getTrace(traceId);
      setTrace(t);
      if (t.spans?.length) {
        const { nodes: n, edges: e } = buildGraph(t);
        setNodes(n);
        setEdges(e);
      }
    } catch (e) {
      setError(String(e));
    }
  }, [traceId, setNodes, setEdges]);

  useEffect(() => { load(); }, [load]);

  if (error) return <div className="p-4 text-red-600 text-sm">{error}</div>;
  if (!trace) return <div className="p-4 text-slate-400 text-sm animate-pulse">加载中…</div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3 text-sm">
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">Trace ID</div>
          <div className="font-mono text-xs truncate">{trace.trace_id}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">Spans</div>
          <div className="font-semibold">{trace.total_spans}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">Allow</div>
          <div className="text-green-600 font-semibold">{trace.decisions?.allow ?? 0}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">Deny</div>
          <div className="text-red-500 font-semibold">{trace.decisions?.deny ?? 0}</div>
        </div>
      </div>

      <div className="flex gap-3 text-xs">
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-green-500 inline-block" /> allow</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-red-500 inline-block" /> deny</span>
        <span className="flex items-center gap-1"><span className="w-3 h-3 rounded-sm bg-slate-400 inline-block" /> unknown</span>
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
          </ReactFlow>
        </div>
      ) : (
        <div className="h-48 flex items-center justify-center text-slate-400 text-sm border border-dashed border-slate-200 rounded-xl">
          暂无 Span 数据
        </div>
      )}
    </div>
  );
}
