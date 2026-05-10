"use client";

import { use, useEffect, useState } from "react";
import { getFeishuDoc, type FeishuBlock, type FeishuDoc } from "@/lib/api";
import Markdown from "@/components/Markdown";

/**
 * Flatten the saved block list (heading1/heading2/heading3/text/code/...) back
 * into a single markdown string. The synthesizer already emits markdown-like
 * text inside each block; we just prepend # / ## / ### for headings and wrap
 * code blocks in fenced markdown so the GFM renderer takes over from there.
 */
function blocksToMarkdown(blocks: FeishuBlock[]): string {
  const out: string[] = [];
  for (const b of blocks) {
    const t = b.text ?? "";
    switch (b.block_type) {
      case "heading1":
        out.push(`# ${t}`);
        break;
      case "heading2":
        out.push(`## ${t}`);
        break;
      case "heading3":
        out.push(`### ${t}`);
        break;
      case "code":
        out.push("```\n" + t + "\n```");
        break;
      default:
        out.push(t);
    }
  }
  return out.join("\n\n");
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
    <div className="max-w-4xl mx-auto px-4 py-6">
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
          {doc.blocks.length === 0 ? (
            <p className="text-slate-400 text-sm text-center py-8">文档内容为空</p>
          ) : (
            <Markdown>{blocksToMarkdown(doc.blocks)}</Markdown>
          )}
        </article>
      )}
    </div>
  );
}
