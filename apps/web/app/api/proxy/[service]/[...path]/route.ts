import { NextRequest, NextResponse } from "next/server";

const SERVICES: Record<string, string> = {
  "doc-assistant": process.env.DOC_ASSISTANT_URL ?? "http://localhost:8100",
  "audit":         process.env.AUDIT_URL         ?? "http://localhost:8090",
  "feishu":        process.env.FEISHU_URL         ?? "http://localhost:9000",
  "idp":           process.env.IDP_URL            ?? "http://localhost:8000",
};

const FORWARD_HEADERS = ["authorization", "content-type", "accept", "dpop"];

// Paths that require admin token injection (served server-side, never sent to browser).
// Format: "service" or "service/path-prefix"
const ADMIN_PATHS: Array<{ service: string; prefix?: string }> = [
  { service: "audit" },               // all audit-api routes
  { service: "idp", prefix: "revoke" },
  { service: "idp", prefix: "agents" },
];

function needsAdminToken(service: string, path: string): boolean {
  return ADMIN_PATHS.some(
    (r) => r.service === service && (r.prefix === undefined || path === r.prefix || path.startsWith(r.prefix + "/"))
  );
}

async function proxy(req: NextRequest, service: string, path: string): Promise<Response> {
  const base = SERVICES[service];
  if (!base) return NextResponse.json({ error: "unknown service" }, { status: 404 });

  const url = `${base}/${path}${req.nextUrl.search}`;

  const headers: Record<string, string> = {};
  for (const key of FORWARD_HEADERS) {
    const val = req.headers.get(key);
    if (val) headers[key] = val;
  }

  if (needsAdminToken(service, path)) {
    const adminToken = process.env.ADMIN_TOKEN;
    if (!adminToken) {
      return NextResponse.json({ error: "admin token not configured" }, { status: 503 });
    }
    // Require caller to be authenticated (has a user session token).
    const callerAuth = req.headers.get("authorization") ?? "";
    if (!callerAuth.startsWith("Bearer ")) {
      return NextResponse.json({ error: "authentication required" }, { status: 401 });
    }
    // Inject server-side admin token; the user's token is not forwarded upstream.
    headers["authorization"] = `Bearer ${adminToken}`;
    headers["content-type"] = headers["content-type"] ?? "application/json";
  }

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const body = hasBody ? await req.arrayBuffer() : undefined;

  const upstream = await fetch(url, { method: req.method, headers, body });

  // Stream SSE responses through without buffering
  if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  }

  const ct = upstream.headers.get("content-type") ?? "application/json";
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": ct },
  });
}

type Ctx = { params: Promise<{ service: string; path: string[] }> };

export async function GET(req: NextRequest, { params }: Ctx) {
  const { service, path } = await params;
  return proxy(req, service, path.join("/"));
}
export async function POST(req: NextRequest, { params }: Ctx) {
  const { service, path } = await params;
  return proxy(req, service, path.join("/"));
}
export async function PUT(req: NextRequest, { params }: Ctx) {
  const { service, path } = await params;
  return proxy(req, service, path.join("/"));
}
export async function DELETE(req: NextRequest, { params }: Ctx) {
  const { service, path } = await params;
  return proxy(req, service, path.join("/"));
}
export async function PATCH(req: NextRequest, { params }: Ctx) {
  const { service, path } = await params;
  return proxy(req, service, path.join("/"));
}
