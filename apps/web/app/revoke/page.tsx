"use client";

import { useState } from "react";
import { revokeToken, checkRevokeStatus } from "@/lib/api";

const REVOKE_TYPES = ["jti", "sub", "agent", "trace", "plan", "chain"];

interface RevokeEntry {
  type: string;
  value: string;
  reason: string;
  ttl_sec: number;
  ts: string;
  success: boolean;
}

export default function RevokePage() {
  const [type, setType] = useState("jti");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [ttl, setTtl] = useState(3600);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [history, setHistory] = useState<RevokeEntry[]>([]);

  const [checkType, setCheckType] = useState("jti");
  const [checkValue, setCheckValue] = useState("");
  const [checkResult, setCheckResult] = useState<string | null>(null);

  async function submit() {
    if (!value.trim()) return;
    setLoading(true);
    setStatus(null);
    try {
      await revokeToken({ type, value: value.trim(), reason, ttl_sec: ttl });
      setStatus("success");
      setHistory((h) => [
        { type, value: value.trim(), reason, ttl_sec: ttl, ts: new Date().toISOString(), success: true },
        ...h,
      ]);
      setValue("");
      setReason("");
    } catch (e) {
      setStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function doCheck() {
    if (!checkValue.trim()) return;
    try {
      const r = await checkRevokeStatus(checkType, checkValue.trim());
      setCheckResult(r.revoked ? "已撤销 ✓" : "未撤销 ✗");
    } catch (e) {
      setCheckResult(`错误: ${e}`);
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-lg font-semibold text-slate-800">撤销面板</h1>

      {/* Revoke form */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <h2 className="font-medium text-slate-700">执行撤销</h2>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-slate-500 mb-1 block">撤销粒度</label>
            <div className="flex flex-wrap gap-2">
              {REVOKE_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => setType(t)}
                  className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                    type === t
                      ? "bg-blue-600 text-white border-blue-600"
                      : "border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="text-xs text-slate-500 mb-1 block">值</label>
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={`输入 ${type} 值…`}
              className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-xs text-slate-500 mb-1 block">原因</label>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="manual / security / …"
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div className="w-28">
              <label className="text-xs text-slate-500 mb-1 block">TTL (秒)</label>
              <input
                type="number"
                value={ttl}
                onChange={(e) => setTtl(Number(e.target.value))}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>

        {status === "success" && (
          <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-lg px-3 py-2">
            撤销成功
          </div>
        )}
        {status && status !== "success" && (
          <div className="bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg px-3 py-2">
            {status}
          </div>
        )}

        <button
          onClick={submit}
          disabled={loading || !value.trim()}
          className="w-full bg-red-600 hover:bg-red-700 disabled:opacity-40 text-white font-medium py-2 rounded-lg text-sm transition-colors"
        >
          {loading ? "处理中…" : "执行撤销"}
        </button>
      </div>

      {/* Check status */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
        <h2 className="font-medium text-slate-700">查询撤销状态</h2>
        <div className="flex gap-2">
          <select
            value={checkType}
            onChange={(e) => setCheckType(e.target.value)}
            className="border border-slate-200 rounded-lg px-2 py-2 text-sm bg-white"
          >
            {REVOKE_TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <input
            value={checkValue}
            onChange={(e) => setCheckValue(e.target.value)}
            placeholder="值…"
            className="flex-1 border border-slate-200 rounded-lg px-3 py-2 text-sm"
          />
          <button
            onClick={doCheck}
            className="bg-slate-700 hover:bg-slate-800 text-white px-3 py-2 rounded-lg text-sm transition-colors"
          >
            查询
          </button>
        </div>
        {checkResult && (
          <div className="text-sm text-slate-700 bg-slate-50 rounded-lg px-3 py-2">{checkResult}</div>
        )}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-5">
          <h2 className="font-medium text-slate-700 mb-3">本次会话撤销记录</h2>
          <div className="space-y-2">
            {history.map((h, i) => (
              <div key={i} className="flex items-center gap-3 text-sm text-slate-600 py-1.5 border-b border-slate-100 last:border-0">
                <span className="text-xs text-slate-400 w-20 shrink-0">{h.ts.slice(11, 19)}</span>
                <span className="px-1.5 py-0.5 bg-slate-100 rounded text-xs font-mono">{h.type}</span>
                <span className="font-mono text-xs flex-1 truncate">{h.value}</span>
                <span className="text-xs text-slate-400">{h.reason || "—"}</span>
                <span className="text-xs text-slate-400">TTL={h.ttl_sec}s</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
