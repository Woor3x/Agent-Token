const IDP = process.env.NEXT_PUBLIC_IDP_URL!;
const CLIENT_ID = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID!;
const REDIRECT_URI = `${typeof window !== "undefined" ? window.location.origin : "http://localhost:3000"}/callback`;

function b64url(buf: Uint8Array): string {
  return btoa(String.fromCharCode(...Array.from(buf)))
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

export async function startLogin() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  const challenge = b64url(new Uint8Array(digest));

  sessionStorage.setItem("pkce_verifier", verifier);

  const url = new URL(`${IDP}/oidc/authorize`);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", CLIENT_ID);
  url.searchParams.set("redirect_uri", REDIRECT_URI);
  url.searchParams.set("scope", "openid profile agent:invoke");
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", crypto.randomUUID());

  window.location.href = url.toString();
}

export async function finishLogin(code: string): Promise<void> {
  const verifier = sessionStorage.getItem("pkce_verifier");
  if (!verifier) throw new Error("missing PKCE verifier");

  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    client_id: CLIENT_ID,
    redirect_uri: REDIRECT_URI,
  });

  const resp = await fetch(`${IDP}/oidc/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.error_description ?? `token exchange failed: ${resp.status}`);
  }

  const tok = await resp.json();
  sessionStorage.setItem("access_token", tok.access_token);
  sessionStorage.setItem("id_token", tok.id_token ?? "");
  sessionStorage.setItem("expires_at", String(Date.now() + tok.expires_in * 1000));
  sessionStorage.removeItem("pkce_verifier");
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  const t = sessionStorage.getItem("access_token");
  const exp = Number(sessionStorage.getItem("expires_at") ?? 0);
  return t && Date.now() < exp ? t : null;
}

export function logout() {
  sessionStorage.clear();
  window.location.href = "/login";
}

export function getUserSub(): string | null {
  const tok = sessionStorage.getItem("id_token");
  if (!tok) return null;
  try {
    const payload = JSON.parse(atob(tok.split(".")[1]));
    return payload.sub ?? null;
  } catch {
    return null;
  }
}
