"use client";

import { useEffect, useRef, useState } from "react";
import { streamAuditEvents } from "@/lib/api";
import type { AuditEvent } from "@/types";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Step {
  id: string;
  label: string;
  detail?: string;
  /** "done" = blue dot  "error" = red dot  "info" = grey dot */
  status: "done" | "error" | "info";
  ts: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function nowIso(): string {
  return new Date().toISOString();
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("zh-CN", { hour12: false });
  } catch {
    return "";
  }
}

/**
 * Map an AuditEvent to a timeline Step.
 *
 * NOTE: audit-api SSE sends the event types AFTER the IdP's _TYPE_MAP remapping:
 *   IdP "token.issue"  →  audit-api SSE "token_issued"
 *   IdP "authz_decision"  →  audit-api SSE "authz_decision"  (unchanged)
 *   Gateway "token_consumed"  →  audit-api SSE "token_consumed"  (new)
 */
function mapEvent(e: AuditEvent): Step | null {
  const ts = e.timestamp ?? nowIso();
  const id = e.event_id ?? `${Date.now()}-${Math.random()}`;

  switch (e.event_type) {
    case "token_issued": {
      const agent = e.callee_agent ?? "";
      const action = e.callee_action ?? "";
      return {
        id,
        label: `身份验证通过 · 令牌签发${agent ? " → " + agent : ""}`,
        detail: action || undefined,
        status: "done",
        ts,
      };
    }

    case "authz_decision": {
      const allow = e.decision === "allow";
      const agent = e.callee_agent ?? "";
      const action = e.callee_action ?? "";
      return {
        id,
        label: allow
          ? `授权通过 · 请求放行${agent ? " → " + agent : ""}`
          : `授权拒绝 · 请求拦截${agent ? " → " + agent : ""}`,
        detail: allow ? (action || undefined) : (e.deny_reasons?.join("；") || undefined),
        status: allow ? "done" : "error",
        ts,
      };
    }

    case "token_consumed": {
      const agent = e.callee_agent ?? "";
      const action = e.callee_action ?? "";
      return {
        id,
        label: `令牌消耗 · 开始执行${agent ? " → " + agent : ""}`,
        detail: action || undefined,
        status: "info",
        ts,
      };
    }

    default:
      return null;
  }
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  /**
   * true  = task is running; subscribe to SSE and show pulsing indicator.
   * false = task finished; stop pulsing, append "完成" step, keep visible.
   *
   * The parent keeps this component mounted for a few seconds after active
   * becomes false (linger) so that late-arriving SSE events still render.
   */
  active: boolean;
}

export function ExecutionFlow({ active }: Props) {
  const [steps, setSteps] = useState<Step[]>([
    { id: "start", label: "任务已提交", status: "done", ts: nowIso() },
  ]);
  // Track whether we've appended the completion step yet.
  const completedRef = useRef(false);

  // ── SSE subscription ─────────────────────────────────────────────────────
  useEffect(() => {
    const stop = streamAuditEvents(
      {},
      (event: AuditEvent) => {
        const step = mapEvent(event);
        if (step) setSteps((s) => [...s, step]);
      },
      () => {
        // Silently swallow SSE errors during the task — the result comes back
        // via the sendChat() Promise regardless.
      }
    );
    return stop;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Completion step ───────────────────────────────────────────────────────
  // When the parent signals active=false (task done), append one final step.
  useEffect(() => {
    if (!active && !completedRef.current) {
      completedRef.current = true;
      setSteps((s) => [
        ...s,
        { id: "done", label: "任务处理完毕", status: "done", ts: nowIso() },
      ]);
    }
  }, [active]);

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="bg-white border border-slate-200 rounded-xl px-4 py-3 min-w-[300px] max-w-sm">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <svg
          className="w-3.5 h-3.5 text-blue-500 shrink-0"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M13 10V3L4 14h7v7l9-11h-7z"
          />
        </svg>
        <span className="text-xs font-medium text-slate-500">
          {active ? "执行中…" : "执行完毕"}
        </span>
        {active && (
          <span className="ml-auto w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
        )}
      </div>

      {/* Timeline */}
      <ol className="space-y-0">
        {steps.map((step, i) => {
          const isLast = i === steps.length - 1;
          const dotColor =
            step.status === "error"
              ? "bg-red-400"
              : step.status === "info"
              ? "bg-slate-400"
              : "bg-blue-400";
          const labelColor =
            step.status === "error" ? "text-red-600" : "text-slate-700";

          return (
            <li key={step.id} className="flex gap-3">
              {/* Left rail */}
              <div className="flex flex-col items-center">
                <span className={`mt-1 w-2 h-2 rounded-full shrink-0 ${dotColor}`} />
                {/* Connector line — hidden after the last step */}
                {!isLast && <span className="flex-1 w-px bg-slate-200 my-1" />}
              </div>

              {/* Content */}
              <div className={`flex-1 ${isLast ? "pb-0" : "pb-3"}`}>
                <div className="flex items-start justify-between gap-2">
                  <span className={`text-sm leading-snug ${labelColor}`}>
                    {step.label}
                  </span>
                  <span className="shrink-0 text-[11px] text-slate-300 tabular-nums mt-0.5">
                    {fmtTs(step.ts)}
                  </span>
                </div>
                {step.detail && (
                  <p className="text-xs text-slate-400 mt-0.5 font-mono truncate max-w-[220px]">
                    {step.detail}
                  </p>
                )}
              </div>
            </li>
          );
        })}

        {/* Pulsing "in-progress" row — only while active */}
        {active && (
          <li className="flex gap-3">
            <div className="flex flex-col items-center">
              <span className="mt-1 w-2 h-2 rounded-full bg-blue-300 animate-pulse shrink-0" />
            </div>
            <div className="flex-1">
              <span className="text-sm text-slate-400 animate-pulse">处理中…</span>
            </div>
          </li>
        )}
      </ol>
    </div>
  );
}
