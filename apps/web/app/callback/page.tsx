"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { finishLogin } from "@/lib/auth";

export default function CallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const errParam = params.get("error");

    if (errParam) {
      setError(params.get("error_description") ?? errParam);
      return;
    }
    if (!code) {
      setError("no code in callback");
      return;
    }

    finishLogin(code)
      .then(() => router.replace("/chat"))
      .catch((e) => setError(String(e)));
  }, [router]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full min-h-96">
        <div className="bg-white rounded-xl border border-red-200 p-8 max-w-sm text-center">
          <div className="text-red-600 font-medium mb-2">登录失败</div>
          <div className="text-sm text-slate-600">{error}</div>
          <a href="/login" className="mt-4 inline-block text-sm text-blue-600 hover:underline">
            重试
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center h-full min-h-96">
      <div className="text-slate-500 text-sm">正在处理登录…</div>
    </div>
  );
}
