import type { Metadata } from "next";
import "./globals.css";
import NavBar from "@/components/NavBar";

export const metadata: Metadata = {
  title: "Agent Token Demo",
  description: "A2A Authorization System Demo",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="h-full flex flex-col bg-slate-50 text-slate-900">
        <NavBar />
        <main className="flex-1 overflow-auto">{children}</main>
      </body>
    </html>
  );
}
