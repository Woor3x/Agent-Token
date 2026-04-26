"""Thin ULID facade (falls back to uuid4 if python-ulid absent)."""
from __future__ import annotations


def new_ulid() -> str:
    try:
        from ulid import ULID

        return str(ULID())
    except Exception:
        import uuid

        return uuid.uuid4().hex
