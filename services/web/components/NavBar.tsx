"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getAccessToken, getUserSub, logout } from "@/lib/auth";
import { useEffect, useState } from "react";

const NAV = [
  { href: "/chat", label: "聊天" },
  { href: "/audit", label: "审计" },
  { href: "/revoke", label: "撤销" },
  { href: "/admin/agents", label: "Agents" },
];

export default function NavBar() {
  const pathname = usePathname();
  const [sub, setSub] = useState<string | null>(null);

  useEffect(() => {
    setSub(getUserSub());
  }, [pathname]);

  const isLoggedIn = Boolean(getAccessToken());

  return (
    <header className="h-12 flex items-center px-4 bg-white border-b border-slate-200 gap-6 shrink-0">
      <span className="font-semibold text-slate-800 text-sm">Agent Token</span>

      {isLoggedIn && (
        <nav className="flex gap-1">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                pathname.startsWith(n.href)
                  ? "bg-blue-600 text-white"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {n.label}
            </Link>
          ))}
        </nav>
      )}

      <div className="ml-auto flex items-center gap-3">
        {isLoggedIn ? (
          <>
            <span className="text-xs text-slate-500">{sub}</span>
            <button
              onClick={logout}
              className="text-xs text-slate-500 hover:text-slate-900 transition-colors"
            >
              退出
            </button>
          </>
        ) : (
          <Link href="/login" className="text-sm text-blue-600 hover:underline">
            登录
          </Link>
        )}
      </div>
    </header>
  );
}
