"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { getAccessToken } from "@/lib/auth";
import {
  sendChat,
  listDriveFiles,
  listBitableTables,
  type DriveFile,
  type BitableTable,
  type BitableSelection,
} from "@/lib/api";
import { useChatStore } from "@/lib/store";
import type { ChatResponse, DagTask } from "@/types";
import Markdown from "@/components/Markdown";
import { ExecutionFlow } from "@/components/ExecutionFlow";

function extractContent(resp: ChatResponse): string {
  // Prefer explicit doc content string (legacy shape).
  if (typeof resp.doc === "string" && resp.doc) return resp.doc;
  // Local-storage doc: render a short summary line; the full content lives at
  // /docs/{id} and is reachable via the "文档查看 →" link below.
  if (resp.doc && typeof resp.doc === "object" && "document_id" in resp.doc) {
    const d = resp.doc;
    const title = d.title ?? "(无标题)";
    const sections = typeof d.block_count === "number" ? ` · ${d.block_count} 段` : "";
    return `# ${title}\n\n✅ 文档已生成${sections}。点击下方「文档查看 →」打开完整报告。`;
  }
  // Fallback: gather title/content from dag task params (legacy planner shape).
  const parts: string[] = [];
  for (const task of (resp.dag ?? []) as DagTask[]) {
    const p = task.params ?? {};
    if (p.title) parts.push(`# ${p.title}`);
    if (p.content) parts.push(p.content);
  }
  if (parts.length) return parts.join("\n\n");
  return "任务完成（无文档输出）";
}

function docUrl(resp: ChatResponse): string | null {
  if (resp.doc && typeof resp.doc === "object" && "document_id" in resp.doc)
    return `/docs/${resp.doc.document_id}`;
  return null;
}

export default function ChatPage() {
  const router = useRouter();
  const { messages, taskStatus, addMessage, setTaskStatus, clearMessages } = useChatStore();
  const loading = taskStatus === "running";
  const [flowKey, setFlowKey] = useState(0);
  // flowVisible stays true for FLOW_LINGER_MS after the task completes so
  // that late-arriving SSE events (delayed by network / batch flush) can still
  // render in the timeline before the component unmounts.
  const [flowVisible, setFlowVisible] = useState(false);
  const lingerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [input, setInput] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [files, setFiles] = useState<DriveFile[] | null>(null);
  const [tables, setTables] = useState<BitableTable[] | null>(null);
  const [pickedFile, setPickedFile] = useState<DriveFile | null>(null);
  // Multi-select: each entry is one data source the user chose. Selections
  // accumulate across picker interactions; the picker only closes on explicit
  // 完成/× actions, not on each pick.
  const [bitables, setBitables] = useState<BitableSelection[]>([]);
  const [pickerErr, setPickerErr] = useState<string | null>(null);
  // breadcrumb of folder tokens we've descended into (root = "")
  const [folderStack, setFolderStack] = useState<{ token: string; name: string }[]>([
    { token: "", name: "根目录" },
  ]);

  // auth check only on mount — don't put router in deps to avoid re-running on every navigation
  useEffect(() => {
    if (!getAccessToken()) router.replace("/login");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Rehydrate the Zustand store from sessionStorage after the component mounts
  // on the client. This must be separate from the auth check because it needs
  // to run on every mount (including navigation returns), and skipHydration:true
  // in the store prevents the auto-hydration that races with Next.js SSR.
  useEffect(() => {
    useChatStore.persist.rehydrate();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Linger: keep the ExecutionFlow visible for 2 s after the task finishes so
  // that SSE events delayed by batch-flush latency can still appear.
  const FLOW_LINGER_MS = 2000;
  useEffect(() => {
    if (loading) {
      // New task starting — show the flow immediately and cancel any pending hide.
      setFlowVisible(true);
      if (lingerTimerRef.current) {
        clearTimeout(lingerTimerRef.current);
        lingerTimerRef.current = null;
      }
    } else if (flowVisible) {
      // Task just finished — schedule hide after linger period.
      lingerTimerRef.current = setTimeout(
        () => setFlowVisible(false),
        FLOW_LINGER_MS
      );
    }
    return () => {
      if (lingerTimerRef.current) clearTimeout(lingerTimerRef.current);
    };
  }, [loading]); // eslint-disable-line react-hooks/exhaustive-deps

  function selLabel(s: BitableSelection): string {
    if (s.name) return s.name;
    if (s.document_id) return s.document_id;
    if (s.app_token) {
      return `${s.app_token}${s.table_id ? "/" + s.table_id : " (整选)"}`;
    }
    return "?";
  }

  // Strip any existing query/fragment from a Feishu URL so we can re-append
  // ``?table=...&from=from_copylink`` cleanly. Backend drive listings come
  // back without query params, but we still defend against future shape
  // changes.
  function _stripQuery(u: string): string {
    const i = u.indexOf("?");
    const j = u.indexOf("#");
    const cut = [i, j].filter((x) => x >= 0).reduce((a, b) => Math.min(a, b), u.length);
    return u.slice(0, cut);
  }

  // Ensure ``?from=from_copylink`` is on the URL — without it, Feishu wraps
  // the deep-link in a user-verification challenge that breaks anonymous
  // / cross-tenant viewers. ``key=val`` already present is left untouched.
  function _withCopyLink(u: string): string {
    if (!u || u === "#") return u;
    if (u.includes("from=from_copylink")) return u;
    return u + (u.includes("?") ? "&" : "?") + "from=from_copylink";
  }

  // Build a clickable Feishu URL for one selection. Prefer the upstream URL
  // captured at pick time (tenant subdomain, e.g.
  // https://jcneyh7qlo8i.feishu.cn/base/<token>?from=from_copylink) — the
  // bare https://feishu.cn/... shape triggers Feishu's user-verification flow.
  // For sub-table picks we splice in ?table=<id>&from=from_copylink.
  function selUrl(s: BitableSelection): string {
    if (s.url) {
      if (s.kind === "bitable" && s.table_id) {
        return `${_stripQuery(s.url)}?table=${s.table_id}&from=from_copylink`;
      }
      return _withCopyLink(s.url);
    }
    // Last-resort fallback (kept for legacy state without url field).
    if (s.kind === "docx" && s.document_id) {
      return `https://feishu.cn/docx/${s.document_id}?from=from_copylink`;
    }
    if (s.app_token) {
      const base = `https://feishu.cn/base/${s.app_token}`;
      return s.table_id
        ? `${base}?table=${s.table_id}&from=from_copylink`
        : `${base}?from=from_copylink`;
    }
    return "#";
  }

  // Same idea for raw picker rows — folder rows have no public deep link, so
  // we only emit a URL for files the user can actually open in Feishu.
  function fileUrl(f: DriveFile): string | null {
    // Backend drive.list returns the tenant-subdomain URL straight from the
    // Feishu API. Folders aren't deep-linkable so we leave them alone; for
    // base/docx we splice in ``?from=from_copylink`` so anonymous viewers
    // bypass Feishu's user-verification gate.
    if (f.url) return f.type === "folder" ? f.url : _withCopyLink(f.url);
    if (f.type === "bitable") return `https://feishu.cn/base/${f.token}?from=from_copylink`;
    if (f.type === "docx") return `https://feishu.cn/docx/${f.token}?from=from_copylink`;
    if (f.type === "folder") return `https://feishu.cn/drive/folder/${f.token}`;
    return null;
  }

  async function submit() {
    const prompt = input.trim();
    if (!prompt || loading) return;
    setInput("");
    const picked = bitables.length > 0 ? [...bitables] : undefined;
    addMessage({ role: "user", text: prompt, sources: picked });
    setFlowKey((k) => k + 1);
    setFlowVisible(true);
    if (lingerTimerRef.current) {
      clearTimeout(lingerTimerRef.current);
      lingerTimerRef.current = null;
    }
    setTaskStatus("running");

    try {
      const resp = await sendChat(prompt, { bitables: picked });
      addMessage({ role: "agent", text: extractContent(resp), response: resp });
      setTaskStatus("idle");
    } catch (e) {
      addMessage({ role: "agent", text: "", error: String(e) });
      setTaskStatus("error");
    }
  }

  async function loadFolder(token: string) {
    setFiles(null);
    setPickerErr(null);
    try {
      // include both bitable and docx so the user can pick either as a data source
      setFiles(await listDriveFiles(token, "bitable,docx"));
    } catch (e) {
      setPickerErr(String(e));
    }
  }

  async function openPicker() {
    setPickerOpen(true);
    setPickerErr(null);
    setTables(null);
    setPickedFile(null);
    setFolderStack([{ token: "", name: "根目录" }]);
    await loadFolder("");
  }

  async function enterFolder(f: DriveFile) {
    setFolderStack((s) => [...s, { token: f.token, name: f.name }]);
    await loadFolder(f.token);
  }

  async function popFolder(idx: number) {
    const next = folderStack.slice(0, idx + 1);
    setFolderStack(next);
    await loadFolder(next[next.length - 1].token);
  }

  // Multi-select helpers: ``addSel`` dedupes by (kind, app_token, table_id,
  // document_id) so accidentally clicking the same item twice is a no-op
  // instead of producing duplicate fetch tasks downstream.
  function addSel(s: BitableSelection) {
    const key = (x: BitableSelection) =>
      `${x.kind ?? ""}|${x.app_token ?? ""}|${x.table_id ?? ""}|${x.document_id ?? ""}`;
    const k = key(s);
    setBitables((cur) => (cur.some((c) => key(c) === k) ? cur : [...cur, s]));
  }

  function removeSel(idx: number) {
    setBitables((cur) => cur.filter((_, i) => i !== idx));
  }

  // Click on a bitable card → drill into its tables for sub-table picking.
  // Docx: nothing to drill into; add it to the selection list directly.
  async function pickFile(f: DriveFile) {
    if (f.type === "docx") {
      addSel({ kind: "docx", document_id: f.token, name: f.name, url: f.url });
      return;
    }
    setPickedFile(f);
    setPickerErr(null);
    setTables(null);
    try {
      setTables(await listBitableTables(f.token));
    } catch (e) {
      setPickerErr(String(e));
    }
  }

  // "Select the whole bitable" — analyse all tables under one app_token.
  function pickWholeBitable(f: DriveFile) {
    addSel({ kind: "bitable", app_token: f.token, name: f.name, url: f.url });
  }

  function pickTable(t: BitableTable) {
    if (!pickedFile) return;
    addSel({
      kind: "bitable",
      app_token: pickedFile.token,
      table_id: t.table_id,
      name: `${pickedFile.name} / ${t.name}`,
      url: pickedFile.url,
    });
    // Drop back to the file list so the user can pick another source if they
    // want — closing only happens via the explicit 完成/× buttons.
    setPickedFile(null);
    setTables(null);
  }

  return (
    <div className="flex flex-col h-full max-w-5xl mx-auto px-4 py-4 gap-4">
      <div className="flex-1 overflow-y-auto space-y-3 min-h-0">
        {messages.length === 0 && (
          <div className="text-center text-slate-400 text-sm mt-20">
            <div className="w-12 h-12 rounded-xl bg-blue-50 flex items-center justify-center mx-auto mb-3">
              <svg className="w-6 h-6 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div className="font-medium text-slate-600">文档助手已就绪</div>
            <div className="mt-1 text-xs text-slate-400">例如：把 Q1 销售数据整理成报告</div>
          </div>
        )}

        {/* Execution flow — shown while task runs AND for 2 s after it finishes
            (linger) so late-arriving SSE events can still populate the timeline.
            Only shown as an "above-messages" banner when the user returned to
            the page mid-task (messages.length > 0 and flow not inline yet). */}
        {flowVisible && messages.length > 0 && !loading && (
          <div className="flex justify-start max-w-[85%]">
            <ExecutionFlow key={flowKey} active={false} />
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-xl text-sm ${
              msg.role === "user"
                ? "bg-blue-600 text-white px-4 py-3"
                : msg.error
                ? "bg-red-50 border border-red-200 text-red-700 px-4 py-3"
                : "bg-white border border-slate-200 text-slate-800 overflow-hidden"
            }`}>
              {msg.error ? (
                <div><span className="font-medium">错误：</span>{msg.error}</div>
              ) : msg.role === "user" ? (
                <div className="whitespace-pre-wrap">{msg.text}</div>
              ) : (
                <>
                  {/* Doc content */}
                  <div className="px-4 pt-3 pb-2">
                    <div className="flex items-center gap-2 mb-2">
                      <svg className="w-3.5 h-3.5 text-slate-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <span className="text-xs font-medium text-slate-500">生成文档</span>
                    </div>
                    <div className={`text-slate-700 leading-relaxed ${
                      expanded === i ? "" : "max-h-[18rem] overflow-hidden"
                    }`}>
                      <Markdown>{msg.text}</Markdown>
                    </div>
                    {msg.text.split("\n").length > 6 && (
                      <button
                        onClick={() => setExpanded(expanded === i ? null : i)}
                        className="mt-1 text-xs text-blue-500 hover:text-blue-700"
                      >
                        {expanded === i ? "收起" : "展开全文"}
                      </button>
                    )}
                  </div>

                  {/* Links */}
                  {msg.response && (
                    <div className="border-t border-slate-100 px-4 py-2 flex flex-wrap gap-3 text-xs bg-slate-50">
                      {docUrl(msg.response) && (
                        <a href={docUrl(msg.response)!}
                          className="text-green-600 hover:underline font-medium">
                          文档查看 →
                        </a>
                      )}
                      <a href={`/plans/${msg.response.plan_id}`} className="text-blue-600 hover:underline">DAG →</a>
                      <a href={`/traces/${msg.response.trace_id}`} className="text-blue-600 hover:underline">Trace →</a>
                      <a href={`/audit?trace_id=${msg.response.trace_id}`} className="text-blue-600 hover:underline">审计 →</a>
                      <span className="text-slate-300 ml-auto font-mono">
                        {msg.response.plan_id?.slice(0, 8)}
                      </span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}

        {/* Inline timeline — shown while the task is running AND for the linger
            period after completion so delayed SSE events still appear. */}
        {flowVisible && (
          <div className="flex justify-start max-w-[85%]">
            <ExecutionFlow key={flowKey} active={loading} />
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Selected-file chips (multi) + picker trigger */}
      <div className="flex items-center gap-2 shrink-0 text-xs flex-wrap">
        <button
          onClick={openPicker}
          className="px-2 py-1 rounded-md border border-slate-300 hover:bg-slate-50 text-slate-700"
        >
          📊 选择数据源{bitables.length > 0 ? `（已选 ${bitables.length}）` : ""}
        </button>
        {bitables.map((b, i) => (
          <span
            key={`${b.kind ?? ""}|${b.app_token ?? ""}|${b.table_id ?? ""}|${b.document_id ?? ""}|${i}`}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border ${
              b.kind === "docx"
                ? "bg-violet-50 border-violet-200 text-violet-700"
                : "bg-emerald-50 border-emerald-200 text-emerald-700"
            }`}
          >
            <span>{b.kind === "docx" ? "📄" : b.table_id ? "📊" : "📊✓"}</span>
            <span>{selLabel(b)}</span>
            <a
              href={selUrl(b)}
              target="_blank"
              rel="noreferrer"
              title="在飞书中打开"
              className="hover:underline opacity-80 hover:opacity-100"
              onClick={(e) => e.stopPropagation()}
            >↗</a>
            <button
              onClick={() => removeSel(i)}
              className="hover:text-slate-900"
              aria-label="remove selection"
            >×</button>
          </span>
        ))}
        {bitables.length > 1 && (
          <button
            onClick={() => setBitables([])}
            className="text-slate-400 hover:text-slate-600 underline"
          >清空</button>
        )}
      </div>

      <div className="flex gap-2 shrink-0">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
          placeholder={
            bitables.length > 0
              ? `对 ${bitables.length} 个数据源提问…`
              : "输入你的需求…"
          }
          className="flex-1 border border-slate-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          disabled={loading}
        />
        <button
          onClick={submit}
          disabled={loading || !input.trim()}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          发送
        </button>
        {messages.length > 0 && !loading && (
          <button
            onClick={clearMessages}
            className="text-slate-400 hover:text-slate-600 px-3 py-2 rounded-lg text-sm transition-colors"
            title="清空对话"
          >
            清空
          </button>
        )}
      </div>

      {/* File picker modal */}
      {pickerOpen && (
        <div
          className="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
          onClick={() => setPickerOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[80vh] flex flex-col"
          >
            <div className="px-5 py-3 border-b border-slate-100 flex items-center gap-2 flex-wrap">
              {pickedFile && (
                <button
                  onClick={() => { setPickedFile(null); setTables(null); }}
                  className="text-slate-400 hover:text-slate-600 text-sm"
                >←</button>
              )}
              {pickedFile ? (
                <h2 className="font-medium text-slate-700">
                  选择表格 — {pickedFile.name}
                </h2>
              ) : (
                <div className="flex items-center gap-1 text-sm">
                  {folderStack.map((f, i) => (
                    <span key={i} className="flex items-center gap-1">
                      <button
                        onClick={() => popFolder(i)}
                        disabled={i === folderStack.length - 1}
                        className={i === folderStack.length - 1
                          ? "font-medium text-slate-700"
                          : "text-blue-600 hover:underline"}
                      >{f.name}</button>
                      {i < folderStack.length - 1 && <span className="text-slate-300">/</span>}
                    </span>
                  ))}
                </div>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={() => setPickerOpen(false)}
                  className="px-3 py-1 rounded-md text-sm bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                  disabled={bitables.length === 0}
                  title={bitables.length === 0 ? "请先选择至少一个数据源" : ""}
                >
                  完成{bitables.length > 0 ? `（${bitables.length}）` : ""}
                </button>
                <button
                  onClick={() => setPickerOpen(false)}
                  className="text-slate-400 hover:text-slate-600"
                  aria-label="close"
                >×</button>
              </div>
            </div>
            <div className="overflow-y-auto p-4 flex-1">
              {pickerErr && (
                <div className="bg-red-50 border border-red-200 text-red-600 text-xs rounded px-3 py-2 mb-3">
                  {pickerErr}
                </div>
              )}
              {!pickedFile && files === null && !pickerErr && (
                <div className="text-slate-400 text-sm">加载中…</div>
              )}
              {!pickedFile && files && files.length === 0 && (
                <div className="text-slate-500 text-sm space-y-2">
                  <div>未找到多维表格文件。</div>
                  <div className="text-xs text-slate-400">
                    若连接的是真飞书，请检查应用是否拥有
                    <code className="mx-1 px-1 bg-slate-100 rounded text-[11px]">drive:drive.metadata:readonly</code>
                    与
                    <code className="mx-1 px-1 bg-slate-100 rounded text-[11px]">bitable:app:readonly</code>
                    权限，且租户内至少存在一个该应用可见的多维表格。
                  </div>
                </div>
              )}
              {!pickedFile && files && files.length > 0 && (
                <ul className="space-y-1">
                  {files.map((f) => {
                    const isFolder = f.type === "folder";
                    const isBitable = f.type === "bitable";
                    const isDocx = f.type === "docx";
                    const icon = isFolder ? "📁" : isBitable ? "📊" : isDocx ? "📄" : "📎";
                    const hover = isFolder
                      ? "hover:bg-amber-50"
                      : isDocx
                      ? "hover:bg-violet-50"
                      : "hover:bg-blue-50";
                    // Already-selected indicators: docx matches by document_id;
                    // whole-bitable matches by app_token without table_id.
                    const docxSelected =
                      isDocx && bitables.some(
                        (b) => b.kind === "docx" && b.document_id === f.token
                      );
                    const wholeSelected =
                      isBitable && bitables.some(
                        (b) => b.kind === "bitable" && b.app_token === f.token && !b.table_id
                      );
                    const subSelectedCount = isBitable
                      ? bitables.filter(
                          (b) => b.kind === "bitable" && b.app_token === f.token && !!b.table_id
                        ).length
                      : 0;
                    return (
                      <li key={f.token} className="flex items-stretch gap-1">
                        <button
                          onClick={() => isFolder ? enterFolder(f) : pickFile(f)}
                          className={`flex-1 text-left px-3 py-2 rounded-lg text-sm text-slate-700 flex items-start gap-2 ${hover}`}
                        >
                          <span className="text-base">{icon}</span>
                          <span className="flex-1 min-w-0">
                            <div className="font-medium truncate flex items-center gap-1">
                              {f.name}
                              {docxSelected && <span className="text-violet-600 text-xs">✓ 已选</span>}
                              {wholeSelected && <span className="text-emerald-600 text-xs">✓ 整选</span>}
                              {subSelectedCount > 0 && (
                                <span className="text-emerald-600 text-xs">✓ {subSelectedCount} 表</span>
                              )}
                            </div>
                            <div className="text-xs text-slate-400 font-mono truncate">{f.token}</div>
                          </span>
                          {isFolder && <span className="text-slate-300">›</span>}
                          {isBitable && <span className="text-xs text-slate-400 self-center">选表 ›</span>}
                        </button>
                        {isBitable && (
                          <button
                            onClick={() => pickWholeBitable(f)}
                            disabled={wholeSelected}
                            title="不细选，整个多维表所有 table 都参与分析"
                            className="px-2 rounded-lg text-xs text-emerald-700 hover:bg-emerald-50 border border-emerald-200 whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {wholeSelected ? "✓ 已加入" : "✓ 整选"}
                          </button>
                        )}
                        {fileUrl(f) && (
                          <a
                            href={fileUrl(f)!}
                            target="_blank"
                            rel="noreferrer"
                            title="在飞书中打开"
                            onClick={(e) => e.stopPropagation()}
                            className="self-center px-2 rounded-lg text-sm text-blue-600 hover:bg-blue-50 border border-blue-200"
                          >↗</a>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {pickedFile && tables === null && !pickerErr && (
                <div className="text-slate-400 text-sm">加载表格中…</div>
              )}
              {pickedFile && tables && tables.length > 0 && (
                <ul className="space-y-1">
                  {tables.map((t) => {
                    const selected = bitables.some(
                      (b) =>
                        b.kind === "bitable" &&
                        b.app_token === pickedFile.token &&
                        b.table_id === t.table_id
                    );
                    return (
                      <li key={t.table_id} className="flex items-stretch gap-1">
                        <button
                          onClick={() => pickTable(t)}
                          disabled={selected}
                          className="flex-1 text-left px-3 py-2 rounded-lg hover:bg-emerald-50 text-sm text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                        >
                          <span className="flex-1 min-w-0">
                            <div className="font-medium truncate">{t.name || t.table_id}</div>
                            <div className="text-xs text-slate-400 font-mono truncate">{t.table_id}</div>
                          </span>
                          {selected && <span className="text-emerald-600 text-xs">✓ 已选</span>}
                        </button>
                        <a
                          href={
                            pickedFile.url
                              ? `${_stripQuery(pickedFile.url)}?table=${t.table_id}&from=from_copylink`
                              : `https://feishu.cn/base/${pickedFile.token}?table=${t.table_id}&from=from_copylink`
                          }
                          target="_blank"
                          rel="noreferrer"
                          title="在飞书中打开"
                          onClick={(e) => e.stopPropagation()}
                          className="self-center px-2 rounded-lg text-sm text-blue-600 hover:bg-blue-50 border border-blue-200"
                        >↗</a>
                      </li>
                    );
                  })}
                </ul>
              )}
              {pickedFile && tables && tables.length === 0 && (
                <div className="text-slate-400 text-sm">该多维表格没有可选表</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
