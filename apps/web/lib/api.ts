import { getAccessToken } from "./auth";
import type { AuditEvent, AgentInfo, PlanResult, TraceResult, AuditStats, ChatResponse } from "@/types";

const DOC_ASSISTANT = "/api/proxy/doc-assistant";
const AUDIT        = "/api/proxy/audit";
const IDP          = "/api/proxy/idp";
const FEISHU       = "/api/proxy/feishu";
function userBearerHeaders(): HeadersInit {
  const t = getAccessToken();
  if (!t) throw new Error("not authenticated");
  return { Authorization: `Bearer ${t}`, "Content-Type": "application/json" };
}

// Admin-gated calls still carry the user's OIDC token so the proxy can verify
// the caller is authenticated. The proxy injects the actual admin token server-side.
function adminBearerHeaders(): HeadersInit {
  return userBearerHeaders();
}

// ── Chat ──────────────────────────────────────────────────────────────────────

/**
 * Data-source selection sent up via /chat. Three shapes:
 *   - bitable + specific table:  {kind:"bitable", app_token, table_id}
 *   - bitable whole app:         {kind:"bitable", app_token}            (table_id omitted)
 *   - docx document:             {kind:"docx", document_id}
 * Backward-compat: legacy callers may still send {app_token, table_id} with no
 * `kind` field — the planner treats that as kind:"bitable".
 */
export interface BitableSelection {
  kind?: "bitable" | "docx";
  app_token?: string;
  table_id?: string;
  document_id?: string;
  name?: string;
  // Upstream Feishu URL (tenant subdomain, e.g.
  // https://jcneyh7qlo8i.feishu.cn/base/<token>...). Captured at pick time
  // from DriveFile.url so the chip's ↗ jumps to the real tenant URL —
  // a hardcoded https://feishu.cn/... triggers user-verification.
  url?: string;
}

export async function sendChat(
  prompt: string,
  opts: {
    bitable?: BitableSelection;
    bitables?: BitableSelection[];
    timeoutMs?: number;
  } = {}
): Promise<ChatResponse> {
  const timeoutMs = opts.timeoutMs ?? 120_000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const body: Record<string, unknown> = { prompt };
    // Multi-select preferred; back-compat singleton kept for older callers.
    if (opts.bitables && opts.bitables.length > 0) body.bitables = opts.bitables;
    else if (opts.bitable) body.bitable = opts.bitable;
    const resp = await fetch(`${DOC_ASSISTANT}/chat`, {
      method: "POST",
      headers: userBearerHeaders(),
      body: JSON.stringify(body),
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

// ── Generated docs ────────────────────────────────────────────────────────────
// doc_assistant writes synthesized reports to Feishu cloud (DOC_STORAGE=feishu,
// the default) and also caches the block list locally keyed by the Feishu
// document_id so the Web UI can render an in-app preview without needing
// user-scope OAuth on Feishu. ``url`` carries the canonical Feishu cloud link
// (https://feishu.cn/docx/<id>) that the doc page surfaces as a jump button.

export interface FeishuBlock {
  block_type: string;
  text?: string;
}

export interface FeishuDoc {
  document_id: string;
  title: string;
  created_at: number;
  blocks: FeishuBlock[];
  // Optional metadata propagated from the doc_writer node. ``url`` is the
  // Feishu cloud URL when storage="feishu"; absent / internal when "local".
  url?: string;
  storage?: "feishu" | "local";
}

export async function getFeishuDoc(docId: string): Promise<FeishuDoc> {
  // Both feishu and local modes cache blocks under doc_assistant's storage,
  // keyed by ``document_id`` (Feishu id or ``doc_local_<ulid>``). Try that
  // path first; fall through to the legacy Feishu mock endpoint only when
  // the cache miss is genuine (e.g. an older trace link from before
  // dual-write was wired up).
  const resp = await fetch(`${DOC_ASSISTANT}/docs/${docId}`);
  if (resp.ok) return resp.json();
  if (resp.status !== 404) throw new Error(`doc not found: ${resp.status}`);
  const legacy = await fetch(`${FEISHU}/open-apis/docx/v1/documents/${docId}`);
  if (!legacy.ok) throw new Error(`doc not found: ${legacy.status}`);
  const body = await legacy.json();
  return { ...body.data.document, blocks: body.data.blocks };
}

export async function listFeishuDocs(): Promise<FeishuDoc[]> {
  const resp = await fetch(`${DOC_ASSISTANT}/docs`);
  if (!resp.ok) throw new Error(`list docs failed: ${resp.status}`);
  const body = await resp.json();
  return body.documents;
}

// ── Drive picker ──────────────────────────────────────────────────────────────
// Lists user-accessible bitables / tables so the chat UI can ask the user
// which sheet to analyse. Backed by doc_assistant /files endpoint, which in
// turn proxies to Feishu (mock or real Open Platform) using the agent's
// tenant token.

export interface DriveFile {
  token: string;     // app_token for bitable
  name: string;
  type: string;
  url: string;
  modified_time: number;
}

export interface BitableTable {
  table_id: string;
  name: string;
}

export async function listDriveFiles(folder = "", fileType = ""): Promise<DriveFile[]> {
  // Empty fileType → backend default "bitable" (folders are kept regardless
  // so the picker can drill in). Pass "any" to skip filtering entirely.
  const params = new URLSearchParams();
  if (folder) params.set("folder", folder);
  if (fileType) params.set("file_type", fileType);
  const qs = params.toString() ? `?${params}` : "";
  const resp = await fetch(`${DOC_ASSISTANT}/files${qs}`);
  if (!resp.ok) throw new Error(`list files failed: ${resp.status}`);
  const body = await resp.json();
  return body.files ?? [];
}

export async function listBitableTables(appToken: string): Promise<BitableTable[]> {
  const resp = await fetch(`${DOC_ASSISTANT}/files/${appToken}/tables`);
  if (!resp.ok) throw new Error(`list tables failed: ${resp.status}`);
  const body = await resp.json();
  return body.tables ?? [];
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
      const t = getAccessToken();
      if (!t) throw new Error("not authenticated");
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${t}`, Accept: "text/event-stream" },
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
