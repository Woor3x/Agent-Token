"""Typed Feishu OpenAPI errors.

The clients raise :class:`FeishuError` instead of bare ``RuntimeError`` so
upstream handlers can branch on ``code`` (Feishu business code) and
``status`` (HTTP status). The :pyattr:`msg` is truncated and the original
``X-Request-Id`` is captured for log correlation, so the exception's string
form never echoes large response bodies (which can include user data).
"""
from __future__ import annotations


class FeishuError(RuntimeError):
    def __init__(
        self,
        *,
        code: int,
        msg: str,
        status: int | None = None,
        endpoint: str = "",
        request_id: str | None = None,
    ) -> None:
        self.code = int(code)
        self.msg = msg
        self.status = status
        self.endpoint = endpoint
        self.request_id = request_id
        super().__init__(
            f"feishu {endpoint} status={status} code={self.code} msg={msg[:120]}"
            + (f" req_id={request_id}" if request_id else "")
        )
