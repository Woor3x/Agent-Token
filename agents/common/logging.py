"""Structured JSON logging with trace context."""
from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar

_trace_ctx: ContextVar[dict] = ContextVar("trace_ctx", default={})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = _trace_ctx.get()
        if ctx:
            payload.update(ctx)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def set_trace_context(**kw: str) -> None:
    _trace_ctx.set({**_trace_ctx.get(), **{k: v for k, v in kw.items() if v}})


def clear_trace_context() -> None:
    _trace_ctx.set({})


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
