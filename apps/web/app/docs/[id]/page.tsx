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
// Split the synthesizer's free-form text block on bare line breaks while
// preserving structural lines (markdown lists ``- foo`` / ``* foo`` / ``1. foo``
// and table rows starting with ``|``) which already have their own line-break
// semantics. Without this, the synthesizer's multi-sentence summary was
// emitted as a single ``\n``-separated paragraph and CommonMark folded every
// newline into a single space.
function _expandSoftBreaks(t: string): string {
  if (!t) return t;
  const lines = t.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    out.push(line);
    if (i === lines.length - 1) break;
    const next = lines[i + 1];
    const isStruct = (s: string): boolean =>
      /^\s*([-*+]\s|\d+[.)]\s|\|)/.test(s) || /^\s*$/.test(s);
    // Only inflate to "\n\n" when neither side is a list/table/blank line —
    // those are already valid block separators on their own.
    if (!isStruct(line) && !isStruct(next)) out.push("");
  }
  return out.join("\n");
}

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
        // Tag the fence with a language so ReactMarkdown's renderer recognises
        // the code node as a *block* (className "language-…") and routes it
        // through the <pre> branch in components/Markdown.tsx; without a
        // language the renderer treats it as inline <code> and collapses
        // newlines, which mangles the ASCII flow diagram.
        out.push("```text\n" + t + "\n```");
        break;
      default:
        out.push(_expandSoftBreaks(t));
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

  // Only treat external https URLs as jump targets — internal "/docs/..."
  // paths would just bounce back to this same page.
  const feishuUrl =
    doc?.url && /^https?:\/\//i.test(doc.url) ? doc.url : null;

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <a href="/chat" className="text-slate-400 hover:text-slate-600 text-sm">← 返回聊天</a>
        <div className="flex items-center gap-2 ml-auto">
          {feishuUrl && (
            <a
              href={feishuUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs inline-flex items-center gap-1 bg-indigo-600 text-white hover:bg-indigo-700 px-3 py-1 rounded-md font-medium"
              title="在飞书云文档中打开"
            >
              打开飞书文档 <span aria-hidden>↗</span>
            </a>
          )}
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
        <>
          {feishuUrl && (
            // The in-app preview renders the Markdown-ish blocks the
            // synthesizer emitted; Feishu Docx uses a richer block tree
            // (callouts, embeds, comments, ...) so the cloud view may
            // differ visually from this preview. Tell the user so a
            // "why doesn't it look the same" question doesn't surface.
            <div className="mb-3 text-xs text-slate-500 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
              本地预览基于 Markdown 渲染 · 飞书云文档为富文档块结构，最终版式以
              <a
                href={feishuUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-600 hover:underline mx-1"
              >飞书原文</a>
              为准。
            </div>
          )}
          <article className="bg-white border border-slate-200 rounded-2xl px-8 py-6 shadow-sm">
            {doc.blocks.length === 0 ? (
              <p className="text-slate-400 text-sm text-center py-8">文档内容为空</p>
            ) : (
              <Markdown>{blocksToMarkdown(doc.blocks)}</Markdown>
            )}
          </article>
        </>
      )}
    </div>
  );
}
