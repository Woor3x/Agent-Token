import { createHash, randomBytes } from "crypto";
import { NextResponse } from "next/server";

export async function GET() {
  const verifier  = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const state     = randomBytes(16).toString("hex");
  return NextResponse.json({ verifier, challenge, state });
}
