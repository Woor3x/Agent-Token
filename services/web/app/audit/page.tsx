"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { listAuditEvents, streamAuditEvents } from "@/lib/api";
import type { AuditEvent } from "@/types";

const EVENT_TYPES = ["", "authz_decision", "token_issued", "token_consumed", "revoke_issued", "anomaly", "agent_registered"];
const DECISIONS = ["", "allow", "deny"];

function fmtTime(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "—";
  // UTC+8
  const offset = 8 * 60;
  const local = new Date(d.getTime() + offset * 60_000);
  return local.toISOString().slice(11, 23);
}

function decisionBadge(d?: string) {
  if (d === "allow") return "bg-green-50 text-green-700 border-green-200";
  if (d === "deny") return "bg-red-50 text-red-600 border-red-200";
  return "bg-slate-50 text-slate-500 border-slate-200";
}

function eventTypeBadge(t: string) {
  if (t === "authz_decision") return "bg-blue-50 text-blue-700";
  if (t === "token_issued") return "bg-purple-50 text-purple-700";
  if (t === "token_consumed") return "bg-indigo-50 text-indigo-700";
  if (t === "revoke_issued") return "bg-orange-50 text-orange-700";
  if (t === "anomaly") return "bg-red-50 text-red-600";
  if (t === "agent_registered") return "bg-teal-50 text-teal-700";
  return "bg-slate-50 text-slate-600";
}

// Group events by trace_id, preserving chronological order within each group.
// Events without a trace_id each form their own singleton group keyed by event_id.
function groupByTrace(events: AuditEvent[]): { key: string; traceId: string | null; items: AuditEvent[] }[] {
  const order: string[] = [];
  const map: Record<string, AuditEvent[]> = {};
  for (const ev of events) {
    const key = ev.trace_id ?? ev.event_id ?? Math.random().toString();
    if (!map[key]) { order.push(key); map[key] = []; }
    map[key].push(ev);
  }
  return order.map((key) => ({
    key,
    traceId: map[key][0]?.trace_id ?? null,
    items: map[key],
  }));
}

function EventRow({ ev }: { ev: AuditEvent }) {
  return (
    <tr className="hover:bg-slate-50 transition-colors">
      <td className="px-4 py-2 font-mono text-xs text-slate-500 whitespace-nowrap pl-8">
        {fmtTime(ev.timestamp)}
      </td>
      <td className="px-4 py-2">
        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${eventTypeBadge(ev.event_type)}`}>
          {ev.event_type}
        </span>
      </td>
      <td className="px-4 py-2 text-slate-600 text-xs">
        {ev.caller_agent ?? ev.caller_sub ?? "—"}
      </td>
      <td className="px-4 py-2 text-slate-600 text-xs">
        {ev.callee_agent ?? "—"}
        {ev.callee_action && <span className="text-slate-400"> · {ev.callee_action}</span>}
      </td>
      <td className="px-4 py-2">
        {ev.decision && (
          <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${decisionBadge(ev.decision)}`}>
            {ev.decision}
          </span>
        )}
        {ev.deny_reasons && ev.deny_reasons.length > 0 && (
          <div className="text-xs text-red-400 mt-0.5">{(ev.deny_reasons as string[]).join(", ")}</div>
        )}
      </td>
      <td className="px-4 py-2 text-xs text-slate-400">
        {ev.latency_ms != null ? `${ev.latency_ms}ms` : "—"}
      </td>
      <td className="px-4 py-2 text-xs flex gap-2">
        {ev.plan_id && (
          <a href={`/plans/${ev.plan_id}`} className="text-blue-600 hover:underline">DAG</a>
        )}
        {ev.trace_id && (
          <a href={`/traces/${ev.trace_id}`} className="text-blue-600 hover:underline">Trace</a>
        )}
      </td>
    </tr>
  );
}

function TraceGroupHeader({ group }: { group: { key: string; traceId: string | null; items: AuditEvent[] } }) {
  const allows = group.items.filter(e => e.decision === "allow").length;
  const denies = group.items.filter(e => e.decision === "deny").length;
  return (
    <tr className="bg-slate-50 border-t-2 border-slate-200">
      <td colSpan={7} className="px-4 py-1.5">
        <div className="flex items-center gap-3 text-xs">
          <span className="font-mono text-slate-500 truncate max-w-xs">{group.traceId ?? "(no trace)"}</span>
          <span className="text-slate-300">·</span>
          <span className="text-slate-400">{group.items.length} 条</span>
          {allows > 0 && <span className="text-green-600 font-medium">{allows} allow</span>}
          {denies > 0 && <span className="text-red-500 font-medium">{denies} deny</span>}
          {group.traceId && (
            <a href={`/traces/${group.traceId}`} className="text-blue-500 hover:underline ml-auto">Trace →</a>
          )}
        </div>
      </td>
    </tr>
  );
}

function AuditPageInner() {
  const searchParams = useSearchParams();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const stopStreamRef = useRef<(() => void) | null>(null);

  const [filter, setFilter] = useState({
    event_type: "",
    decision: "",
    trace_id: searchParams.get("trace_id") ?? "",
    plan_id: searchParams.get("plan_id") ?? "",
  });
  const [offset, setOffset] = useState(0);

  async function load(off = 0) {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (filter.event_type) params.event_type = filter.event_type;
      if (filter.decision) params.decision = filter.decision;
      if (filter.trace_id) params.trace_id = filter.trace_id;
      if (filter.plan_id) params.plan_id = filter.plan_id;
      const { total, events } = await listAuditEvents(params, 50, off);
      setEvents(events);
      setTotal(total);
      setOffset(off);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(0); }, []); // eslint-disable-line

  function toggleStream() {
    if (streaming) {
      stopStreamRef.current?.();
      stopStreamRef.current = null;
      setStreaming(false);
    } else {
      setStreaming(true);
      const stop = streamAuditEvents(
        {},
        (e) => setEvents((prev) => [e, ...prev].slice(0, 500)),
        (err) => { setError(String(err)); setStreaming(false); }
      );
      stopStreamRef.current = stop;
    }
  }

  useEffect(() => () => { stopStreamRef.current?.(); }, []);

  const groups = groupByTrace(events);

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-800">审计事件</h1>
        <button
          onClick={toggleStream}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
            streaming
              ? "bg-red-50 text-red-600 border border-red-200 hover:bg-red-100"
              : "bg-green-50 text-green-700 border border-green-200 hover:bg-green-100"
          }`}
        >
          <span className={`w-2 h-2 rounded-full ${streaming ? "bg-red-500 animate-pulse" : "bg-green-500"}`} />
          {streaming ? "停止实时" : "实时流"}
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 items-end">
        <select
          value={filter.event_type}
          onChange={(e) => setFilter((f) => ({ ...f, event_type: e.target.value }))}
          className="border border-slate-200 rounded px-2 py-1.5 text-sm bg-white"
        >
          {EVENT_TYPES.map((t) => <option key={t} value={t}>{t || "全部类型"}</option>)}
        </select>
        <select
          value={filter.decision}
          onChange={(e) => setFilter((f) => ({ ...f, decision: e.target.value }))}
          className="border border-slate-200 rounded px-2 py-1.5 text-sm bg-white"
        >
          {DECISIONS.map((d) => <option key={d} value={d}>{d || "全部决策"}</option>)}
        </select>
        <input
          value={filter.trace_id}
          onChange={(e) => setFilter((f) => ({ ...f, trace_id: e.target.value }))}
          placeholder="trace_id"
          className="border border-slate-200 rounded px-2 py-1.5 text-sm bg-white w-44"
        />
        <input
          value={filter.plan_id}
          onChange={(e) => setFilter((f) => ({ ...f, plan_id: e.target.value }))}
          placeholder="plan_id"
          className="border border-slate-200 rounded px-2 py-1.5 text-sm bg-white w-44"
        />
        <button
          onClick={() => load(0)}
          className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm transition-colors"
        >
          查询
        </button>
        <span className="text-sm text-slate-400 ml-auto">共 {total} 条 · {groups.length} 个 trace</span>
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      {/* Table */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500 w-36">时间</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">类型</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">调用方</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">目标</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500 w-28">决策</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500 w-16">耗时</th>
                <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500 w-24">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && (
                <tr>
                  <td colSpan={7} className="px-4 py-6 text-center text-slate-400 text-sm animate-pulse">
                    加载中…
                  </td>
                </tr>
              )}
              {!loading && events.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-4 py-6 text-center text-slate-400 text-sm">
                    暂无数据
                  </td>
                </tr>
              )}
              {groups.map((group) => (
                <>
                  <TraceGroupHeader key={`hdr-${group.key}`} group={group} />
                  {group.items.map((ev) => (
                    <EventRow key={ev.event_id} ev={ev} />
                  ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      <div className="flex gap-2 justify-end">
        <button
          disabled={offset === 0}
          onClick={() => load(Math.max(0, offset - 50))}
          className="px-3 py-1.5 text-sm border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
        >
          上一页
        </button>
        <button
          disabled={offset + 50 >= total}
          onClick={() => load(offset + 50)}
          className="px-3 py-1.5 text-sm border border-slate-200 rounded hover:bg-slate-50 disabled:opacity-40"
        >
          下一页
        </button>
      </div>
    </div>
  );
}

export default function AuditPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-slate-400 text-sm animate-pulse">加载中…</div>}>
      <AuditPageInner />
    </Suspense>
  );
}
