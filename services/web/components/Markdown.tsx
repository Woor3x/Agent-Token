"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  children: string;
  className?: string;
}

/**
 * Tailwind-styled GFM markdown renderer used by chat messages and the
 * standalone doc viewer. We don't pull in `@tailwindcss/typography` because
 * the project already targets Tailwind v4 and avoids extra plugins; instead
 * each block element gets explicit utility classes here.
 */
export default function Markdown({ children, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 className="text-2xl font-bold text-slate-900 mt-6 mb-3">{children}</h1>,
          h2: ({ children }) => <h2 className="text-xl font-semibold text-slate-800 mt-5 mb-2 pb-1 border-b border-slate-100">{children}</h2>,
          h3: ({ children }) => <h3 className="text-base font-semibold text-slate-700 mt-4 mb-1">{children}</h3>,
          h4: ({ children }) => <h4 className="text-sm font-semibold text-slate-700 mt-3 mb-1">{children}</h4>,
          p: ({ children }) => <p className="my-2 text-sm text-slate-700 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="list-disc pl-6 my-2 text-sm text-slate-700 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-6 my-2 text-sm text-slate-700 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer"
               className="text-blue-600 hover:underline">{children}</a>
          ),
          strong: ({ children }) => <strong className="font-semibold text-slate-900">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          code: ({ children, className }) => {
            const isBlock = className?.includes("language-");
            if (isBlock) {
              return (
                <pre className="bg-slate-900 text-slate-100 rounded-lg px-4 py-3 my-3 text-xs overflow-x-auto">
                  <code>{children}</code>
                </pre>
              );
            }
            return <code className="bg-slate-100 text-slate-800 px-1.5 py-0.5 rounded text-xs font-mono">{children}</code>;
          },
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-slate-200 pl-4 my-3 text-slate-600 italic text-sm">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-4 border-slate-200" />,
          table: ({ children }) => (
            <div className="overflow-x-auto my-3">
              <table className="w-full text-sm border-collapse border border-slate-200">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-slate-50">{children}</thead>,
          th: ({ children }) => (
            <th className="text-left px-3 py-2 text-xs font-medium text-slate-500 border border-slate-200">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-1.5 text-xs text-slate-700 border border-slate-200">{children}</td>
          ),
          tr: ({ children }) => <tr className="even:bg-slate-50/50">{children}</tr>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
