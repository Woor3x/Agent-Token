import { NextRequest, NextResponse } from "next/server";

// Internal IdP URL — server-side only, never exposed to browser
const IDP = process.env.IDP_URL ?? "http://localhost:8000";
// redirect_uri must be in IdP's ALLOWED_REDIRECT_URIS whitelist
const CALLBACK = "http://localhost:3000/callback";

export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;

  const params = new URLSearchParams({
    response_type:          searchParams.get("response_type") ?? "code",
    client_id:              searchParams.get("client_id") ?? "",
    redirect_uri:           CALLBACK,
    scope:                  searchParams.get("scope") ?? "openid profile agent:invoke",
    code_challenge:         searchParams.get("code_challenge") ?? "",
    code_challenge_method:  searchParams.get("code_challenge_method") ?? "S256",
    state:                  searchParams.get("state") ?? "",
  });

  const resp = await fetch(`${IDP}/oidc/authorize?${params}`);
  let html = await resp.text();

  // Rewrite form action so the browser POSTs to our proxy, not the IdP directly
  html = html.replace('action="/oidc/login"', 'action="/api/auth/login"');

  return new NextResponse(html, {
    status: resp.status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
