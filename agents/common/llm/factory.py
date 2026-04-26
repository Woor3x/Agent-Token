"""Pick a concrete ``LLMProvider`` by environment.

Env vars
--------
LLM_PROVIDER : "volc" | "openai" | "mock"   (default "mock")
ARK_API_KEY  : required for volc
ARK_MODEL    : endpoint id (ep-xxx) or model name (doubao-seed-1-6-250615)
ARK_BASE     : default https://ark.cn-beijing.volces.com/api/v3
OPENAI_*     : standard OpenAI envs

Falls back to ``MockLLMProvider`` so tests and offline demos always work.
"""
from __future__ import annotations

import os

from .base import LLMProvider
from .mock import MockLLMProvider


def make_llm(*, provider: str | None = None) -> LLMProvider:
    name = (provider or os.environ.get("LLM_PROVIDER", "mock")).lower()
    if name == "volc":
        from .volc import VolcArkProvider

        return VolcArkProvider()
    if name == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider()
    if name == "mock":
        return MockLLMProvider()
    raise ValueError(f"unknown LLM_PROVIDER: {name}")
