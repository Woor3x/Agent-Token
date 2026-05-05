"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { getAccessToken } from "@/lib/auth";
import { sendChat } from "@/lib/api";
import type { ChatResponse, DagTask } from "@/types";

interface Message {
  role: "user" | "agent";
  text: string;       // extracted display content
  response?: ChatResponse;
  error?: string;
}

function extractContent(resp: ChatResponse): string {
  // Prefer explicit doc content string
  if (typeof resp.doc === "string" && resp.doc) return resp.doc;
  // Gather content/title from dag task params
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // auth check only on mount — don't put router in deps to avoid re-running on every navigation
  useEffect(() => {
    if (!getAccessToken()) router.replace("/login");
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function submit() {
    const prompt = input.trim();
    if (!prompt || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: prompt }]);
    setLoading(true);

    try {
      const resp = await sendChat(prompt);
      setMessages((m) => [
        ...m,
        { role: "agent", text: extractContent(resp), response: resp },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "agent", text: "", error: String(e) },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto px-4 py-4 gap-4">
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
                          d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <span className="text-xs font-medium text-slate-500">生成文档</span>
                    </div>
                    <div className={`whitespace-pre-wrap text-slate-700 leading-relaxed ${
                      expanded === i ? "" : "line-clamp-6"
                    }`}>
                      {msg.text}
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

        {loading && (
          <div className="flex justify-start">
            <div className="bg-white border border-slate-200 rounded-xl px-4 py-3 text-sm text-slate-500 flex items-center gap-2">
              <svg className="w-4 h-4 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <span className="animate-pulse">文档助手处理中…</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2 shrink-0">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
          placeholder="输入你的需求…"
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
      </div>
    </div>
  );
}
