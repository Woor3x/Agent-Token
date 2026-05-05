"use client";

import { use, useEffect, useState } from "react";
import { getFeishuDoc, type FeishuBlock, type FeishuDoc } from "@/lib/api";

function renderBlock(block: FeishuBlock, i: number) {
  const text = block.text ?? "";
  switch (block.block_type) {
    case "heading1":
      return <h1 key={i} className="text-2xl font-bold text-slate-900 mt-6 mb-3">{text}</h1>;
    case "heading2":
      return <h2 key={i} className="text-lg font-semibold text-slate-800 mt-5 mb-2 pb-1 border-b border-slate-100">{text}</h2>;
    case "heading3":
      return <h3 key={i} className="text-base font-semibold text-slate-700 mt-4 mb-1">{text}</h3>;
    default:
      // markdown-like: lines starting with - are list items, | are tables
      if (text.includes(" | ")) {
        const lines = text.split("\n");
        const header = lines[0]?.split(" | ") ?? [];
        const rows = lines.slice(2).map(l => l.split(" | "));
        return (
          <div key={i} className="overflow-x-auto my-3">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-slate-50">
                  {header.map((h, j) => (
                    <th key={j} className="text-left px-3 py-1.5 text-xs font-medium text-slate-500 border border-slate-200">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, j) => (
                  <tr key={j} className="even:bg-slate-50/50">
                    {row.map((cell, k) => (
                      <td key={k} className="px-3 py-1.5 text-xs text-slate-700 border border-slate-200">{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      return (
        <div key={i} className="my-2 text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
          {text.split("\n").map((line, j) =>
            line.startsWith("- ") ? (
              <div key={j} className="flex gap-2 my-0.5">
                <span className="text-slate-400 shrink-0">•</span>
                <span>{line.slice(2)}</span>
              </div>
            ) : (
              <p key={j} className={line ? "" : "h-2"}>{line}</p>
            )
          )}
        </div>
      );
  }
}

export default function DocPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [doc, setDoc] = useState<FeishuDoc | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getFeishuDoc(id)
      .then(setDoc)
      .catch(e => setError(String(e)));
  }, [id]);

  return (
    <div className="max-w-3xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <a href="/chat" className="text-slate-400 hover:text-slate-600 text-sm">← 返回聊天</a>
        <div className="flex items-center gap-2 ml-auto">
          <span className="text-xs font-mono text-slate-400 bg-slate-100 px-2 py-0.5 rounded">{id}</span>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg px-4 py-3">{error}</div>
      )}

      {!doc && !error && (
        <div className="space-y-3 animate-pulse">
          <div className="h-8 bg-slate-100 rounded w-2/3" />
          <div className="h-4 bg-slate-100 rounded w-full" />
          <div className="h-4 bg-slate-100 rounded w-5/6" />
          <div className="h-4 bg-slate-100 rounded w-4/6" />
        </div>
      )}

      {doc && (
        <article className="bg-white border border-slate-200 rounded-2xl px-8 py-6 shadow-sm">
          {doc.blocks.map((block, i) => renderBlock(block, i))}
          {doc.blocks.length === 0 && (
            <p className="text-slate-400 text-sm text-center py-8">文档内容为空</p>
          )}
        </article>
      )}
    </div>
  );
}
