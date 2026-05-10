"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { listFeishuDocs } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import type { FeishuDoc } from "@/lib/api";

function fmtTime(ts?: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000 + 8 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 16).replace("T", " ");
}

export default function DocsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<FeishuDoc[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getAccessToken()) { router.replace("/login"); return; }
    listFeishuDocs()
      .then(setDocs)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-800">文档列表</h1>
        <span className="text-sm text-slate-400">{docs.length} 篇</span>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg px-4 py-3">{error}</div>
      )}

      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        {loading && (
          <div className="px-4 py-8 text-center text-slate-400 text-sm animate-pulse">加载中…</div>
        )}

        {!loading && docs.length === 0 && !error && (
          <div className="px-4 py-12 text-center text-slate-400 text-sm">
            暂无文档 — 先去
            <Link href="/chat" className="text-blue-600 hover:underline mx-1">聊天</Link>
            生成一篇吧
          </div>
        )}

        {docs.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {docs.map((doc) => (
              <li key={doc.document_id}>
                <Link
                  href={`/docs/${doc.document_id}`}
                  className="flex items-center gap-4 px-5 py-3.5 hover:bg-slate-50 transition-colors group"
                >
                  {/* 文档图标 */}
                  <div className="shrink-0 w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
                    <svg className="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                  </div>

                  {/* 标题 + id */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate group-hover:text-blue-600 transition-colors">
                      {doc.title || "(无标题)"}
                    </p>
                    <p className="text-xs text-slate-400 font-mono mt-0.5 truncate">{doc.document_id}</p>
                  </div>

                  {/* 时间 */}
                  <span className="shrink-0 text-xs text-slate-400 tabular-nums">
                    {fmtTime(doc.created_at)}
                  </span>

                  {/* 箭头 */}
                  <svg className="w-4 h-4 text-slate-300 group-hover:text-blue-400 shrink-0 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
