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


Role = Literal["system", "user", "assistant", "tool"]


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
    # CoT trace for reasoning models (Doubao seed-pro / o1-style). None when the
    # provider does not surface one. Never log without redaction — may echo the
    # user prompt verbatim.
    reasoning: str | None = None
    # Future-proofing for tool/function-calling. Populated when the model emits
    # ``message.tool_calls``; left None for plain chat completions.
    tool_calls: list[dict[str, Any]] | None = None
    # Upstream-supplied id for distributed tracing (e.g. Ark X-Request-Id, or
    # the X-Client-Request-Id we sent if the upstream did not echo back).
    request_id: str | None = None
    raw: dict[str, Any] | None = None


class LLMError(Exception):
    """Any provider-side failure (HTTP, parse, content-policy).

    The message is *sanitised* — it never contains the upstream raw body to
    avoid leaking the prompt back into logs/exceptions. Full upstream payload
    is attached as ``upstream`` for opt-in DEBUG inspection.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        upstream: dict[str, Any] | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.upstream = upstream
        self.request_id = request_id
        self.retryable = retryable
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
        # Per-call overrides — let nodes pick a model/budget without
        # constructing a new provider instance.
        model: str | None = None,
        trace_id: str | None = None,
        # Optional knobs forwarded when the provider supports them.
        top_p: float | None = None,
        stop: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatResult: ...

    async def aclose(self) -> None:
        """Override if the provider owns a connection pool."""
        return None
