"use client";

import { startLogin } from "@/lib/auth";

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Card */}
        <div className="bg-white rounded-2xl shadow-md border border-slate-200/80 overflow-hidden">
          {/* Header band */}
          <div className="bg-gradient-to-r from-blue-600 to-blue-500 px-8 py-7">
            <div className="flex items-center gap-3 mb-1">
              <div className="w-8 h-8 rounded-lg bg-white/20 flex items-center justify-center">
                <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </div>
              <span className="text-white font-semibold text-base tracking-tight">Agent Token</span>
            </div>
            <p className="text-blue-100 text-xs mt-1">A2A 鉴权系统管理控制台</p>
          </div>

          {/* Body */}
          <div className="px-8 py-7 space-y-5">
            <div>
              <p className="text-slate-700 text-sm font-medium mb-1">单点登录</p>
              <p className="text-slate-400 text-xs leading-relaxed">
                通过 OIDC PKCE 流程跳转至身份提供方完成认证，无需在此输入密码。
              </p>
            </div>

            <button
              onClick={startLogin}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white font-medium py-2.5 px-4 rounded-xl transition-colors text-sm shadow-sm"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
              </svg>
              跳转至 IdP 登录
            </button>

            {/* Divider */}
            <div className="border-t border-slate-100 pt-4">
              <p className="text-xs text-slate-400 mb-2 font-medium">演示账号</p>
              <div className="bg-slate-50 rounded-lg px-3 py-2.5 flex items-center justify-between">
                <div className="flex items-center gap-3 text-xs font-mono">
                  <span className="text-slate-600">alice</span>
                  <span className="text-slate-300">/</span>
                  <span className="text-slate-500">alice123</span>
                </div>
                <span className="text-xs text-blue-500 bg-blue-50 px-1.5 py-0.5 rounded font-medium">orchestrator</span>
              </div>
            </div>
          </div>
        </div>

        <p className="text-center text-xs text-slate-400 mt-4">
          Agent-Token · A2A Authorization Demo
        </p>
      </div>
    </div>
  );
}
