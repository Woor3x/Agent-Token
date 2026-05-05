import { getAccessToken } from "./auth";
import type { AuditEvent, AgentInfo, PlanResult, TraceResult, AuditStats, ChatResponse } from "@/types";

const DOC_ASSISTANT = process.env.NEXT_PUBLIC_DOC_ASSISTANT_URL!;
const AUDIT = process.env.NEXT_PUBLIC_AUDIT_URL!;
const IDP = process.env.NEXT_PUBLIC_IDP_URL!;
const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN!;
const FEISHU = process.env.NEXT_PUBLIC_FEISHU_URL!;

function userBearerHeaders(): HeadersInit {
  const t = getAccessToken();
  if (!t) throw new Error("not authenticated");
  return { Authorization: `Bearer ${t}`, "Content-Type": "application/json" };
}

function adminBearerHeaders(): HeadersInit {
  return { Authorization: `Bearer ${ADMIN_TOKEN}`, "Content-Type": "application/json" };
}

// ── Chat ──────────────────────────────────────────────────────────────────────

export async function sendChat(prompt: string, timeoutMs = 120_000): Promise<ChatResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${DOC_ASSISTANT}/chat`, {
      method: "POST",
      headers: userBearerHeaders(),
      body: JSON.stringify({ prompt }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail ?? body.error?.message ?? `chat failed: ${resp.status}`);
    }
    return resp.json();
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError")
      throw new Error("请求超时（>120s），请重试");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ── Feishu mock docs ──────────────────────────────────────────────────────────

export interface FeishuBlock {
  block_type: string;
  text?: string;
}

export interface FeishuDoc {
  document_id: string;
  title: string;
  created_at: number;
  blocks: FeishuBlock[];
}

export async function getFeishuDoc(docId: string): Promise<FeishuDoc> {
  const resp = await fetch(`${FEISHU}/open-apis/docx/v1/documents/${docId}`);
  if (!resp.ok) throw new Error(`doc not found: ${resp.status}`);
  const body = await resp.json();
  return { ...body.data.document, blocks: body.data.blocks };
}

export async function listFeishuDocs(): Promise<FeishuDoc[]> {
  const resp = await fetch(`${FEISHU}/open-apis/docx/v1/documents`);
  if (!resp.ok) throw new Error(`list docs failed: ${resp.status}`);
  const body = await resp.json();
  return body.data.documents;
}

// ── Audit ─────────────────────────────────────────────────────────────────────

export async function listAuditEvents(
  params: Record<string, string> = {},
  limit = 50,
  offset = 0
): Promise<{ total: number; events: AuditEvent[]; next_offset?: number }> {
  const qs = new URLSearchParams({ ...params, limit: String(limit), offset: String(offset) });
  const resp = await fetch(`${AUDIT}/audit/events?${qs}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`audit/events: ${resp.status}`);
  return resp.json();
}

export async function getTrace(traceId: string): Promise<TraceResult> {
  const resp = await fetch(`${AUDIT}/audit/traces/${traceId}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`audit/traces: ${resp.status}`);
  return resp.json();
}

export async function getPlan(planId: string): Promise<PlanResult> {
  const resp = await fetch(`${AUDIT}/audit/plans/${planId}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`audit/plans: ${resp.status}`);
  return resp.json();
}

export async function getAuditStats(window = "1h"): Promise<AuditStats> {
  const resp = await fetch(`${AUDIT}/audit/stats?window=${window}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`audit/stats: ${resp.status}`);
  return resp.json();
}

// ── Audit SSE ─────────────────────────────────────────────────────────────────
// Uses fetch streaming (not EventSource) so we can send Authorization header.

export function streamAuditEvents(
  filter: Record<string, string>,
  onEvent: (e: AuditEvent) => void,
  onError?: (e: Error) => void
): () => void {
  const qs = new URLSearchParams(filter);
  const url = `${AUDIT}/audit/stream?${qs}`;
  let aborted = false;
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}`, Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`SSE ${resp.status}`);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";

      while (!aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() ?? "";
        for (const chunk of parts) {
          const lines = chunk.split("\n");
          const eventLine = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
          const dataLine = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
          if (eventLine === "audit_event" && dataLine) {
            try { onEvent(JSON.parse(dataLine)); } catch { /* ignore parse errors */ }
          }
        }
      }
    } catch (err) {
      if (!aborted) onError?.(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return () => {
    aborted = true;
    controller.abort();
  };
}

// ── Revoke ────────────────────────────────────────────────────────────────────

export async function revokeToken(body: {
  type: string;
  value: string;
  reason?: string;
  ttl_sec?: number;
}): Promise<{ revoked: boolean; type: string; value: string }> {
  const resp = await fetch(`${IDP}/revoke`, {
    method: "POST",
    headers: adminBearerHeaders(),
    body: JSON.stringify({ reason: "", ttl_sec: 86400, ...body }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error?.message ?? `revoke failed: ${resp.status}`);
  }
  return resp.json();
}

export async function checkRevokeStatus(type: string, value: string): Promise<{ revoked: boolean }> {
  const qs = new URLSearchParams({ type, value });
  const resp = await fetch(`${IDP}/revoke/status?${qs}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`revoke/status: ${resp.status}`);
  return resp.json();
}

// ── IdP agents ────────────────────────────────────────────────────────────────

export async function listAgents(status?: string): Promise<{ agents: AgentInfo[] }> {
  const qs = status ? `?status=${status}` : "";
  const resp = await fetch(`${IDP}/agents${qs}`, { headers: adminBearerHeaders() });
  if (!resp.ok) throw new Error(`agents: ${resp.status}`);
  return resp.json();
}
