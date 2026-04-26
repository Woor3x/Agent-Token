"""Shared LLM abstraction (multi-provider).

Default provider is the deterministic mock so tests / offline demo work without
secrets. Set ``LLM_PROVIDER=volc`` + ``ARK_API_KEY`` + ``ARK_MODEL`` to use the
Volcengine Ark Doubao Seed model in real demos.
"""
from .base import ChatMessage, ChatResult, LLMError, LLMProvider
from .factory import make_llm
from .mock import MockLLMProvider

__all__ = [
    "ChatMessage",
    "ChatResult",
    "LLMError",
    "LLMProvider",
    "MockLLMProvider",
    "make_llm",
]
