import { NextRequest, NextResponse } from "next/server";

const IDP = process.env.IDP_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const body = await req.text();

  const resp = await fetch(`${IDP}/oidc/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    redirect: "manual",
  });

  const location = resp.headers.get("location");
  if (resp.status === 302 && location) {
    // IdP redirects to http://localhost:3000/callback?code=xxx
    // Reconstruct using the Host header so it works on any origin (local or remote)
    const loc = new URL(location);
    const host  = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "localhost:3000";
    const proto = (req.headers.get("x-forwarded-proto") ?? "http").split(",")[0].trim();
    return NextResponse.redirect(
      `${proto}://${host}/callback${loc.search}`,
      { status: 302 }
    );
  }

  // Login failed — IdP returned an error page
  const html = await resp.text();
  return new NextResponse(html, {
    status: resp.status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
