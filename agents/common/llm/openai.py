"""OpenAI-compatible provider — works with OpenAI proper, Azure OpenAI, and any
OpenAI-protocol-compatible endpoint (DeepSeek, Together, vLLM, etc.).

Selected when ``LLM_PROVIDER=openai``. Reads ``OPENAI_API_KEY`` /
``OPENAI_BASE`` (default ``https://api.openai.com/v1``) / ``OPENAI_MODEL``.

Mirrors ``VolcArkProvider`` for the production-grade behaviours: retry/backoff
on 429/5xx/timeout, ``X-Request-ID`` propagation, sanitised error messages and
per-call ``model=`` override.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import httpx

from .base import ChatMessage, ChatResult, LLMError, LLMProvider
from ..logging import _trace_ctx  # type: ignore[attr-defined]

_log = logging.getLogger("agents.llm.openai")

_DEFAULT_BASE = "https://api.openai.com/v1"
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def _ctx_trace_id() -> str | None:
    try:
        return _trace_ctx.get().get("trace_id")  # type: ignore[no-any-return]
    except Exception:
        return None


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base = (base_url or os.environ.get("OPENAI_BASE", _DEFAULT_BASE)).rstrip("/")
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        if not self._api_key:
            raise LLMError("LLM_CONFIG", "OPENAI_API_KEY not set")
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._owns_http = http is None
        self._max_attempts = max(1, max_attempts)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
        timeout: float = 30.0,
        model: str | None = None,
        trace_id: str | None = None,
        top_p: float | None = None,
        stop: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": model or self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            body["top_p"] = top_p
        if stop:
            body["stop"] = stop
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        req_id = trace_id or _ctx_trace_id() or ""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if req_id:
            # OpenAI supports X-Request-ID; downstream proxies (vLLM/DeepSeek)
            # treat any X-* header as opaque, so safe to send unconditionally.
            headers["X-Request-ID"] = req_id
        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._base}/chat/completions"
        resp = await self._post_with_retry(url, body, headers, timeout, req_id)
        return self._parse(resp, body["model"], req_id)

    async def _post_with_retry(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
        req_id: str,
    ) -> httpx.Response:
        last_exc: LLMError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._http.post(
                    url, json=body, headers=headers, timeout=timeout
                )
            except httpx.TimeoutException as e:
                last_exc = LLMError(
                    "LLM_TIMEOUT",
                    f"timeout after {timeout}s",
                    request_id=req_id or None,
                    retryable=True,
                )
                _log.warning(
                    "openai timeout attempt=%d/%d req_id=%s err=%s",
                    attempt, self._max_attempts, req_id, e,
                )
            except httpx.HTTPError as e:
                last_exc = LLMError(
                    "LLM_HTTP",
                    type(e).__name__,
                    request_id=req_id or None,
                    retryable=True,
                )
                _log.warning(
                    "openai transport-error attempt=%d/%d req_id=%s err=%s",
                    attempt, self._max_attempts, req_id, e,
                )
            else:
                if resp.status_code == 200:
                    return resp
                if (
                    resp.status_code in _RETRYABLE_STATUS
                    and attempt < self._max_attempts
                ):
                    delay = self._retry_delay(resp, attempt)
                    _log.warning(
                        "openai retryable status=%d attempt=%d/%d sleep=%.2fs req_id=%s",
                        resp.status_code, attempt, self._max_attempts, delay, req_id,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise self._upstream_error(resp, req_id)

            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff(attempt))

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        base = 0.5 * (2 ** (attempt - 1))
        return base * (1.0 + random.uniform(-0.2, 0.2))

    @classmethod
    def _retry_delay(cls, resp: httpx.Response, attempt: int) -> float:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), 8.0)
            except ValueError:
                pass
        return cls._backoff(attempt)

    @staticmethod
    def _upstream_error(resp: httpx.Response, req_id: str) -> LLMError:
        try:
            payload = resp.json()
        except Exception:
            payload = {"raw": resp.text[:500]}
        err = payload.get("error") if isinstance(payload, dict) else None
        code = (err or {}).get("code") if isinstance(err, dict) else None
        upstream_req_id = resp.headers.get("X-Request-Id") or req_id or None
        msg = f"status={resp.status_code} code={code or 'unknown'}"
        return LLMError(
            "LLM_UPSTREAM",
            msg,
            status_code=resp.status_code,
            upstream=payload if isinstance(payload, dict) else None,
            request_id=upstream_req_id,
            retryable=resp.status_code in _RETRYABLE_STATUS,
        )

    @staticmethod
    def _parse(resp: httpx.Response, model: str, req_id: str) -> ChatResult:
        data = resp.json()
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or ""
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(
                "LLM_PARSE",
                f"unexpected response shape: {type(e).__name__}",
                request_id=req_id or None,
            ) from e
        reasoning = msg.get("reasoning_content") if isinstance(msg, dict) else None
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None
        upstream_req_id = (
            resp.headers.get("X-Request-Id") or data.get("id") or req_id or None
        )
        return ChatResult(
            content=content,
            model=data.get("model", model),
            usage=data.get("usage") or {},
            finish_reason=finish,
            reasoning=reasoning,
            tool_calls=tool_calls if tool_calls else None,
            request_id=upstream_req_id,
            raw=data,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
