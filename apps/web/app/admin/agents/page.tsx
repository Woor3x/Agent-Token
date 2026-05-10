"use client";

import { useEffect, useState } from "react";
import { listAgents } from "@/lib/api";
import type { AgentInfo } from "@/types";

function statusBadge(s: string) {
  if (s === "active") return "bg-green-50 text-green-700 border-green-200";
  if (s === "revoked") return "bg-red-50 text-red-600 border-red-200";
  return "bg-slate-50 text-slate-500 border-slate-200";
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AgentInfo | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    listAgents()
      .then(({ agents }) => setAgents(agents))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = agents.filter((a) =>
    !filter || a.agent_id.includes(filter) || a.role.includes(filter) || a.status.includes(filter)
  );

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-800">Agent 注册表</h1>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="搜索…"
          className="border border-slate-200 rounded px-2 py-1.5 text-sm bg-white w-48"
        />
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">Agent ID</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">角色</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">KID</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">状态</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">注册时间</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400 text-sm animate-pulse">
                  加载中…
                </td>
              </tr>
            )}
            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-slate-400 text-sm">
                  暂无数据
                </td>
              </tr>
            )}
            {filtered.map((a) => (
              <tr
                key={a.agent_id}
                onClick={() => setSelected(selected?.agent_id === a.agent_id ? null : a)}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 font-medium font-mono text-xs">{a.agent_id}</td>
                <td className="px-4 py-3 text-slate-600 text-xs">{a.role}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-500 max-w-48 truncate">{a.kid}</td>
                <td className="px-4 py-3">
                  <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${statusBadge(a.status)}`}>
                    {a.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">
                  {a.registered_at?.slice(0, 16).replace("T", " ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Detail panel */}
      {selected && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-medium text-slate-800">{selected.agent_id}</h2>
            <button
              onClick={() => setSelected(null)}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              关闭
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {[
              ["Agent ID", selected.agent_id],
              ["Role", selected.role],
              ["Status", selected.status],
              ["KID", selected.kid],
              ["Display Name", selected.display_name ?? "—"],
              ["Contact", selected.contact ?? "—"],
              ["Registered At", selected.registered_at],
              ["Registered By", selected.registered_by ?? "—"],
            ].map(([k, v]) => (
              <div key={k}>
                <div className="text-xs text-slate-400 mb-0.5">{k}</div>
                <div className="font-mono text-xs text-slate-700 break-all">{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
