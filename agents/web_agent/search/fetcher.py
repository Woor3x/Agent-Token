"""URL fetch with SSRF defense + size cap.

Allowlist / blocked-CIDR checks happen before any DNS/HTTP. We also re-check
the resolved IP against blocked CIDRs to defeat DNS-rebind style tricks, then
issue a size-capped streaming GET.
"""
from __future__ import annotations

import ipaddress
import socket
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

_ALLOWLIST_PATH = Path(__file__).resolve().parent.parent / "config" / "allowlist.yaml"


class FetchBlocked(PermissionError):
    pass


def _load_allowlist(path: Path = _ALLOWLIST_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _host_allowed(host: str, patterns: list[str]) -> bool:
    h = host.lower()
    return any(fnmatchcase(h, p.lower()) for p in patterns)


def _ip_blocked(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


def url_allowed(url: str, *, allowlist: dict | None = None) -> tuple[bool, str]:
    al = allowlist or _load_allowlist()
    p = urlparse(url)
    if p.scheme != "https":
        return False, "scheme_not_https"
    host = p.hostname or ""
    if not host:
        return False, "no_host"
    if not _host_allowed(host, al.get("allowed_domains", [])):
        return False, "domain_not_allowed"
    # DNS resolve + CIDR check
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, "dns_failure"
    cidrs = al.get("blocked_cidrs", [])
    for _, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        if _ip_blocked(ip, cidrs):
            return False, f"ip_blocked:{ip}"
    return True, "ok"


def _extract_text(html: str, max_chars: int = 8000) -> str:
    # Lightweight extraction — strip tags without pulling trafilatura for tests.
    import re as _re

    text = _re.sub(r"<script[\s\S]*?</script>", " ", html, flags=_re.I)
    text = _re.sub(r"<style[\s\S]*?</style>", " ", text, flags=_re.I)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


async def http_fetch(
    url: str,
    *,
    timeout: float = 5.0,
    max_size: int = 512 * 1024,
    allowlist: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    ok, reason = url_allowed(url, allowlist=allowlist)
    if not ok:
        raise FetchBlocked(reason)
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for buf in r.aiter_bytes():
                total += len(buf)
                if total > max_size:
                    raise FetchBlocked("size_exceeded")
                chunks.append(buf)
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        if own:
            await c.aclose()


def summarize(text: str, *, max_chars: int = 500) -> str:
    """Stub summarizer (no LLM in tests) — 'only summarize, never execute'."""
    cleaned = text.strip().replace("\n", " ")
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0] + "…"
