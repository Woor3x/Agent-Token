"""Deterministic mock LLM — for tests and offline demos.

Behavior:
 * If ``responses`` is provided, returns them in order (cycles).
 * If ``rule`` callable is provided, it gets the messages and returns content.
 * Otherwise echoes the last user message under a "[mock]" prefix.

Never makes a network call. Safe to use in CI without secrets.
"""
from __future__ import annotations

from typing import Callable

from .base import ChatMessage, ChatResult, LLMProvider


class MockLLMProvider(LLMProvider):
    name = "mock"

    def __init__(
        self,
        *,
        responses: list[str] | None = None,
        rule: Callable[[list[ChatMessage]], str] | None = None,
        model: str = "mock-model",
    ) -> None:
        self._responses = list(responses) if responses else None
        self._rule = rule
        self._model = model
        self._idx = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        timeout: float = 30.0,
    ) -> ChatResult:
        if self._rule is not None:
            content = self._rule(messages)
        elif self._responses:
            content = self._responses[self._idx % len(self._responses)]
            self._idx += 1
        else:
            last_user = next(
                (m.content for m in reversed(messages) if m.role == "user"), ""
            )
            content = f"[mock] {last_user[:200]}"
        return ChatResult(
            content=content,
            model=self._model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop",
        )
