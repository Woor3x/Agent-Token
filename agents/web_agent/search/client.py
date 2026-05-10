"""Search client — Tavily live backend or in-process mock.

Default ``mock`` keeps demo/tests offline. Set ``WEB_SEARCH_BACKEND=tavily``
and provide ``TAVILY_API_KEY`` (or ``SEARCH_API_KEY`` for backward compat) to
hit https://api.tavily.com/search.
"""
from __future__ import annotations

import os

import httpx

_TAVILY_URL = "https://api.tavily.com/search"

_MOCK_CORPUS: list[dict] = [
    {
        "title": "Agent Token Architecture (wiki)",
        "url": "https://en.wikipedia.org/wiki/Software_agent",
        "snippet": "An agent is a program that acts on behalf of a user with delegated credentials.",
    },
    {
        "title": "RFC 8693 Token Exchange",
        "url": "https://arxiv.org/abs/2106.12345",
        "snippet": "Token exchange protocol lets a client trade one token for another, preserving delegation chain.",
    },
    {
        "title": "DPoP: Demonstrating Proof of Possession",
        "url": "https://github.com/danielfett/draft-dpop",
        "snippet": "DPoP binds access tokens to a client-held key to prevent token replay.",
    },
    {
        "title": "Zero Trust Agent Design",
        "url": "https://example.com/zero-trust-agents",
        "snippet": "Capability-based authorization + SoD + single-executor binding eliminates confused-deputy risk.",
    },
]


def _mock_search(query: str, max_results: int) -> list[dict]:
    q = query.lower()
    scored = [
        (sum(1 for w in q.split() if w in (d["title"] + d["snippet"]).lower()), d)
        for d in _MOCK_CORPUS
    ]
    scored.sort(key=lambda p: p[0], reverse=True)
    picked = [d for s, d in scored if s > 0] or [d for _, d in scored]
    return picked[:max_results]


async def _tavily_search(
    query: str,
    *,
    max_results: int,
    api_key: str,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": os.environ.get("TAVILY_SEARCH_DEPTH", "basic"),
        "include_answer": False,
        "include_images": False,
        "include_raw_content": False,
    }
    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await c.post(_TAVILY_URL, json=payload)
        r.raise_for_status()
        data = r.json() or {}
    finally:
        if own:
            await c.aclose()
    out: list[dict] = []
    for item in (data.get("results") or [])[:max_results]:
        out.append(
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "snippet": item.get("content") or "",
            }
        )
    return out


async def search(query: str, *, max_results: int = 5) -> list[dict]:
    backend = os.environ.get("WEB_SEARCH_BACKEND", "mock").lower()
    if backend == "mock":
        return _mock_search(query, max_results)
    if backend == "tavily":
        api_key = os.environ.get("TAVILY_API_KEY") or os.environ.get("SEARCH_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY (or SEARCH_API_KEY) required for tavily backend")
        timeout = float(os.environ.get("WEB_SEARCH_TIMEOUT", "10"))
        return await _tavily_search(
            query, max_results=max_results, api_key=api_key, timeout=timeout
        )
    raise NotImplementedError(f"unsupported search backend: {backend}")
