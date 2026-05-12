"use client";

import { useEffect, useCallback, useState } from "react";
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
import { getTrace } from "@/lib/api";
import type { TraceSpan, TraceResult } from "@/types";

// Trace visualisation goal:
//   - Show "caller → callee" as the dominant visual story (who invoked whom)
//   - Order spans chronologically (top→bottom or left→right) so the user can
//     read the actual A2A handshake sequence
//   - Use color/role tints to instantly tell agents apart
//   - Cope with mixed span_id schemas from the audit-api (some "evt_<uuid>",
//     some short hex) and dangling parent references

const AGENT_TINT: Record<string, { bg: string; border: string; text: string }> = {
  user:          { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" },
  alice:         { bg: "#fef3c7", border: "#f59e0b", text: "#92400e" },
  doc_assistant: { bg: "#e0e7ff", border: "#6366f1", text: "#3730a3" },
  data_agent:    { bg: "#dcfce7", border: "#16a34a", text: "#166534" },
  web_agent:     { bg: "#dbeafe", border: "#3b82f6", text: "#1e40af" },
  idp:           { bg: "#fce7f3", border: "#ec4899", text: "#9d174d" },
  gateway:       { bg: "#ede9fe", border: "#8b5cf6", text: "#5b21b6" },
};

const DEFAULT_TINT = { bg: "#f1f5f9", border: "#94a3b8", text: "#475569" };
function tintFor(agent?: string | null): { bg: string; border: string; text: string } {
  if (agent && AGENT_TINT[agent]) return AGENT_TINT[agent];
  return DEFAULT_TINT;
}

function decisionColor(d?: string | null) {
  if (d === "allow") return "#22c55e";
  if (d === "deny") return "#ef4444";
  return "#94a3b8";
}

function decisionPill(d?: string | null) {
  if (d === "allow") return "bg-green-100 text-green-700";
  if (d === "deny") return "bg-red-100 text-red-600";
  return "bg-slate-100 text-slate-500";
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

// Short id for display when callee/caller aren't available.
const shortId = (id: string) => id.replace(/^evt_/, "").slice(0, 6);

function buildGraph(result: TraceResult): { nodes: Node[]; edges: Edge[] } {
  const all = flattenSpans(result.spans);
  const presentIds = new Set(all.map((s) => s.span_id));

  // Dangling parent references → synthetic placeholder nodes so edges land
  // on something visible instead of being silently dropped by React Flow.
  const ghostParents = new Set<string>();
  for (const s of all) {
    if (s.parent_span_id && !presentIds.has(s.parent_span_id)) {
      ghostParents.add(s.parent_span_id);
    }
  }

  const nodes: Node[] = all.map((s, i) => {
    const callee = s.callee ?? null;
    const caller = s.caller ?? null;
    const isUnknown = !callee && !caller;
    const tint = tintFor(callee);
    const order = i + 1;
    const primary = callee ?? caller ?? shortId(s.span_id);

    return {
      id: s.span_id,
      type: "default",
      data: {
        label: (
          <div className="leading-tight">
            <div className="flex items-center justify-between gap-2 mb-1">
              <span className="text-[10px] font-mono px-1 rounded bg-white/70" style={{ color: tint.text }}>
                #{order}
              </span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${decisionPill(s.decision)}`}>
                {s.decision ?? "—"}
              </span>
            </div>
            <div
              className={`font-semibold text-sm text-center truncate ${isUnknown ? "italic opacity-70" : ""}`}
              style={{ color: tint.text }}
              title={callee ?? caller ?? s.span_id}
            >
              {primary}
            </div>
            {caller && callee && (
              <div className="text-[10px] text-center mt-0.5 opacity-80" style={{ color: tint.text }}>
                {caller} → {callee}
              </div>
            )}
            <div className="text-[10px] text-center mt-0.5 opacity-70" style={{ color: tint.text }}>
              {s.latency_ms != null ? `${s.latency_ms}ms` : "—"}
              <span className="mx-1 opacity-50">·</span>
              <span className="font-mono">{shortId(s.span_id)}</span>
            </div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: {
        border: `2px solid ${tint.border}`,
        background: tint.bg,
        borderRadius: 10,
        padding: "8px 12px",
        width: 200,
      },
    };
  });

  for (const gid of ghostParents) {
    nodes.push({
      id: gid,
      type: "default",
      data: {
        label: (
          <div className="text-xs text-center leading-tight text-slate-400 italic">
            <div className="font-medium truncate max-w-[170px]">未捕获的父 Span</div>
            <div className="text-[10px] font-mono">{shortId(gid)}</div>
            <div className="text-[10px]">(missing parent)</div>
          </div>
        ),
      },
      position: { x: 0, y: 0 },
      style: {
        border: "2px dashed #cbd5e1",
        borderRadius: 10,
        background: "#f8fafc",
        padding: "8px 12px",
        width: 180,
      },
    });
  }

  const edges: Edge[] = all
    .filter((s) => s.parent_span_id)
    .map((s) => {
      const stroke = decisionColor(s.decision);
      return {
        id: `e-${s.span_id}`,
        source: s.parent_span_id!,
        target: s.span_id,
        label: s.decision,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 18, height: 18 },
        style: { stroke, strokeWidth: 2 },
        labelStyle: { fontSize: 10, fill: stroke, fontWeight: 600 },
        labelBgStyle: { fill: "white", fillOpacity: 0.95 },
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 4,
      };
    });

  // For root spans (no parent) — chain them in chronological order so the
  // user reads the trace as a sequence rather than scattered islands.
  const roots = all.filter((s) => !s.parent_span_id);
  for (let i = 1; i < roots.length; i++) {
    edges.push({
      id: `e-root-${i}`,
      source: roots[i - 1].span_id,
      target: roots[i].span_id,
      label: "次序",
      type: "smoothstep",
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#cbd5e1", width: 14, height: 14 },
      style: { stroke: "#cbd5e1", strokeWidth: 1.5, strokeDasharray: "4 4" },
      labelStyle: { fontSize: 9, fill: "#94a3b8" },
      labelBgStyle: { fill: "white", fillOpacity: 0.9 },
    });
  }

  // Layout: top-down keeps caller→callee handshakes reading as descent.
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 70, marginx: 20, marginy: 20 });
  nodes.forEach((n) => {
    const w = (n.style?.width as number) ?? 200;
    g.setNode(n.id, { width: w, height: 80 });
  });
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  return {
    nodes: nodes.map((n) => {
      const p = g.node(n.id);
      const w = (n.style?.width as number) ?? 200;
      return { ...n, position: { x: p.x - w / 2, y: p.y - 40 } };
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

  const flat = flattenSpans(trace.spans ?? []);

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

      <details className="bg-amber-50 border border-amber-200 rounded-lg text-xs text-slate-700">
        <summary className="cursor-pointer px-3 py-2 font-medium text-amber-900 select-none">
          如何阅读这张 Trace 图？（点击展开示例）
        </summary>
        <div className="px-4 pb-3 pt-1 space-y-1.5 leading-relaxed">
          <p>
            <span className="font-semibold">每个框 = 一次 A2A 调用</span>
            （一个 Span）。框内 <span className="font-mono">#1 #2</span> 是<strong>调用时序</strong>，
            从 #1 开始按时间顺序读下去。
          </p>
          <p>
            框中标题是被调用方（callee），下方 <span className="font-mono">caller → callee</span>
            表示「谁调用了谁」。例如：<span className="font-mono">doc_assistant → web_agent</span>
            代表编排器拿着一次性 Token 去调用 web_agent。
          </p>
          <p>
            <strong>实线箭头</strong>表示真实的父子调用关系，颜色编码授权决策：
            <span className="text-green-600 font-medium">绿色 = allow</span>、
            <span className="text-red-600 font-medium">红色 = deny</span>。
            <strong>虚线</strong>表示多条独立根 Span 之间的时序顺序（不是真实调用）。
          </p>
          <p>
            <span className="font-mono">17e95b</span> 这种是 <strong>span_id 的短 ID</strong>
            （取 <span className="font-mono">evt_…</span> 前 6 个十六进制字符），仅用于在屏幕上识别同一个 Span，
            完整 ID 在下方明细列表里。
          </p>
          <p>
            灰色虚线框「<strong>未捕获的父 Span</strong>」表示：当前 Trace 里有一个 Span
            的 <span className="font-mono">parent_span_id</span> 指向一个 audit-api 没返回的父记录
            （通常是另一条 Trace 的边界或更早的 Span 被裁剪了），为避免箭头悬空，画一个占位框。
          </p>
        </div>
      </details>

      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        <span className="font-medium">图例：</span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#fef3c7", border: "1px solid #f59e0b" }} /> 用户
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#e0e7ff", border: "1px solid #6366f1" }} /> doc_assistant
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#dcfce7", border: "1px solid #16a34a" }} /> data_agent
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ background: "#dbeafe", border: "1px solid #3b82f6" }} /> web_agent
        </span>
        <span className="ml-auto flex items-center gap-3">
          <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-green-500" /> allow</span>
          <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-red-500" /> deny</span>
          <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-slate-300 border-t border-dashed border-slate-400" /> 时序</span>
        </span>
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
          暂无 Span 数据
        </div>
      )}

      <div className="space-y-1">
        <div className="text-xs text-slate-500 px-1">执行明细（按顺序）</div>
        {flat.map((s, i) => {
          const t = tintFor(s.callee);
          return (
            <div
              key={s.span_id}
              className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-2 text-sm"
            >
              <span className="text-xs font-mono text-slate-400 w-6">#{i + 1}</span>
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: decisionColor(s.decision) }} />
              <span
                className="font-semibold w-32 truncate"
                style={{ color: t.text }}
              >
                {s.callee ?? "—"}
              </span>
              <span className="text-slate-500 text-xs flex-1 truncate">
                {s.caller ? `${s.caller} → ${s.callee ?? "?"}` : "(根 span)"}
              </span>
              <span className="text-slate-400 text-xs">
                {s.latency_ms != null ? `${s.latency_ms}ms` : ""}
              </span>
              <span className={`text-xs px-1.5 py-0.5 rounded ${decisionPill(s.decision)}`}>
                {s.decision ?? "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
