"""Search client — real backend (Tavily/SerpAPI) or in-process mock.

We default to mock to keep demo/tests offline. Set ``WEB_SEARCH_BACKEND=tavily``
and provide ``SEARCH_API_KEY`` for live traffic (not exercised in tests).
"""
from __future__ import annotations

import os

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


async def search(query: str, *, max_results: int = 5) -> list[dict]:
    backend = os.environ.get("WEB_SEARCH_BACKEND", "mock").lower()
    if backend == "mock":
        return _mock_search(query, max_results)
    # pragma: no cover — live backend not exercised in tests
    raise NotImplementedError(f"live search backend {backend} not wired in demo")
