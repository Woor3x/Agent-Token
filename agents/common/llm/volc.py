"""Volcengine Ark provider (火山方舟 / Doubao Seed family).

Ark exposes an OpenAI-compatible Chat Completions endpoint:

  POST {base}/chat/completions
  Authorization: Bearer <ARK_API_KEY>
  body: {"model": "<endpoint_id_or_model>", "messages": [...], "temperature": ...,
         "response_format": {"type": "json_object"}? }

``model`` accepts either the inference endpoint id (e.g. ``ep-xxxxxxxx``) created
in the Ark console, or a public model name like ``doubao-seed-1-6-250615``. The
caller passes whichever the deployment uses; per-call ``model=`` overrides the
provider default so different LangGraph nodes can route to different endpoints
(e.g. cheap planner ep + expensive synthesizer ep) without instantiating a new
client.

Production hardening on top of the bare HTTP shape:

* **Retry with backoff** — 3 attempts on 429 / 5xx / network timeout, honouring
  ``Retry-After`` when the upstream sets it.
* **Trace propagation** — ``X-Client-Request-Id`` is sent on every call
  (explicit ``trace_id=`` arg, falling back to the logging context).
* **Error sanitisation** — ``LLMError.message`` never carries the upstream raw
  body (prompts can echo back). The full payload is attached on
  ``LLMError.upstream`` for opt-in DEBUG.
* **Reasoning capture** — ``message.reasoning_content`` (Doubao seed-pro CoT)
  is surfaced on ``ChatResult.reasoning`` rather than silently discarded.
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

_log = logging.getLogger("agents.llm.volc")

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def _ctx_trace_id() -> str | None:
    try:
        return _trace_ctx.get().get("trace_id")  # type: ignore[no-any-return]
    except Exception:
        return None


class VolcArkProvider(LLMProvider):
    name = "volc"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http: httpx.AsyncClient | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._api_key = api_key or os.environ.get("ARK_API_KEY", "")
        self._base = (base_url or os.environ.get("ARK_BASE", _DEFAULT_BASE)).rstrip("/")
        self._model = model or os.environ.get("ARK_MODEL", "")
        if not self._api_key:
            raise LLMError("LLM_CONFIG", "ARK_API_KEY is not set")
        if not self._model:
            raise LLMError(
                "LLM_CONFIG",
                "ARK_MODEL (endpoint id or model name) not set",
            )
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._owns_http = http is None
        self._max_attempts = max(1, max_attempts)
        # Per-(base+model) capability cache — currently only tracks json_mode
        # support. Survives provider lifetime, reset on process restart.
        self._caps_no_json: set[str] = set()

    # ----------------------------------------------------------- capability cache

    def _cap_key(self, model: str) -> str:
        return f"{self._base}|{model}"

    def _json_mode_unsupported(self, model: str) -> bool:
        return self._cap_key(model) in self._caps_no_json

    def _mark_json_mode_unsupported(self, model: str) -> None:
        self._caps_no_json.add(self._cap_key(model))

    @staticmethod
    def _is_json_mode_rejection(upstream: dict | None) -> bool:
        if not isinstance(upstream, dict):
            return False
        err = upstream.get("error") or {}
        param = err.get("param") if isinstance(err, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        return param == "response_format.type" and code == "InvalidParameter"

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
        # Some Doubao endpoints (e.g. seed-pro CoT models) reject
        # ``response_format=json_object`` with 400 InvalidParameter. Cache the
        # negative result per (base+model) so we only pay the round-trip once.
        used_model = body["model"]
        wants_json = json_mode and not self._json_mode_unsupported(used_model)
        if wants_json:
            body["response_format"] = {"type": "json_object"}

        req_id = trace_id or _ctx_trace_id() or ""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if req_id:
            headers["X-Client-Request-Id"] = req_id
        if extra_headers:
            headers.update(extra_headers)

        url = f"{self._base}/chat/completions"
        try:
            resp = await self._post_with_retry(url, body, headers, timeout, req_id)
        except LLMError as exc:
            if (
                wants_json
                and exc.code == "LLM_UPSTREAM"
                and exc.status_code == 400
                and self._is_json_mode_rejection(exc.upstream)
            ):
                _log.info(
                    "ark model=%s rejects response_format=json_object — "
                    "caching + retrying without it (req_id=%s)",
                    used_model, req_id,
                )
                self._mark_json_mode_unsupported(used_model)
                body.pop("response_format", None)
                resp = await self._post_with_retry(url, body, headers, timeout, req_id)
            else:
                raise
        return self._parse(resp, used_model, req_id)

    # ------------------------------------------------------------------ helpers

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
                    "ark timeout attempt=%d/%d req_id=%s err=%s",
                    attempt, self._max_attempts, req_id, e,
                )
            except httpx.HTTPError as e:
                # Network-level error before any HTTP response (DNS, refused,
                # reset). Treat as retryable — Ark sees nothing on its side.
                last_exc = LLMError(
                    "LLM_HTTP",
                    type(e).__name__,
                    request_id=req_id or None,
                    retryable=True,
                )
                _log.warning(
                    "ark transport-error attempt=%d/%d req_id=%s err=%s",
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
                        "ark retryable status=%d attempt=%d/%d sleep=%.2fs req_id=%s",
                        resp.status_code, attempt, self._max_attempts, delay, req_id,
                    )
                    await asyncio.sleep(delay)
                    continue
                # Non-retryable or attempts exhausted — raise sanitised error.
                raise self._upstream_error(resp, req_id)

            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff(attempt))

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Exponential 0.5s → 1s → 2s with ±20% jitter.
        base = 0.5 * (2 ** (attempt - 1))
        return base * (1.0 + random.uniform(-0.2, 0.2))

    @classmethod
    def _retry_delay(cls, resp: httpx.Response, attempt: int) -> float:
        # Honour Retry-After if upstream provides it (seconds form only — Ark
        # does not return HTTP-date).
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
        # Sanitised message — only the upstream error code + status, never the
        # message body (may echo prompt content).
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
