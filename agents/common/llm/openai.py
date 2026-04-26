"""OpenAI-compatible provider — works with OpenAI proper, Azure OpenAI, and any
OpenAI-protocol-compatible endpoint (DeepSeek, Together, vLLM, etc.).

Selected when ``LLM_PROVIDER=openai``. Reads ``OPENAI_API_KEY`` /
``OPENAI_BASE`` (default ``https://api.openai.com/v1``) / ``OPENAI_MODEL``.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base import ChatMessage, ChatResult, LLMError, LLMProvider

_DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base = (base_url or os.environ.get("OPENAI_BASE", _DEFAULT_BASE)).rstrip("/")
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if not self._api_key:
            raise LLMError("LLM_CONFIG", "OPENAI_API_KEY not set")
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._owns_http = http is None

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        timeout: float = 30.0,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            resp = await self._http.post(
                f"{self._base}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
        except httpx.TimeoutException as e:
            raise LLMError("LLM_TIMEOUT", str(e)) from e
        except httpx.HTTPError as e:
            raise LLMError("LLM_HTTP", str(e)) from e
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = {"raw": resp.text}
            raise LLMError(
                "LLM_UPSTREAM",
                json.dumps(err, ensure_ascii=False),
                status_code=resp.status_code,
            )
        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("LLM_PARSE", str(e)) from e
        return ChatResult(
            content=content or "",
            model=data.get("model", self._model),
            usage=data.get("usage") or {},
            finish_reason=finish,
            raw=data,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
