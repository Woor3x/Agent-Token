import { NextRequest, NextResponse } from "next/server";

const IDP      = process.env.IDP_URL ?? "http://localhost:8000";
const CALLBACK = "http://localhost:3000/callback";

export async function POST(req: NextRequest) {
  const raw = await req.text();
  const params = new URLSearchParams(raw);

  // redirect_uri must match what was sent to /oidc/authorize (our fixed CALLBACK)
  params.set("redirect_uri", CALLBACK);

  const resp = await fetch(`${IDP}/oidc/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params.toString(),
  });

  const json = await resp.json();
  return NextResponse.json(json, { status: resp.status });
}
