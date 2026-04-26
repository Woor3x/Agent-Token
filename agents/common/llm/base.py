"""LLM provider abstraction.

All concrete providers (Volc Ark, OpenAI, mock) implement ``LLMProvider.chat``
and return a uniform ``ChatResult``. Callers code against the abstract type and
choose the implementation via ``factory.make_llm()`` driven by environment
variables — no provider-specific imports leak into business code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


class LLMError(Exception):
    """Any provider-side failure (HTTP, parse, content-policy)."""

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(f"[{code}] {message}")


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        timeout: float = 30.0,
    ) -> ChatResult: ...

    async def aclose(self) -> None:
        """Override if the provider owns a connection pool."""
        return None
