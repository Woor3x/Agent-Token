"""Feishu Mock server config: fixtures + runtime state."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_FIXTURES_PATH = Path(
    os.environ.get(
        "FEISHU_MOCK_FIXTURES",
        str(Path(__file__).resolve().parent / "fixtures.yaml"),
    )
)


def load_fixtures() -> dict[str, Any]:
    return yaml.safe_load(_FIXTURES_PATH.read_text(encoding="utf-8")) or {}


# In-process doc store (ephemeral, reset per process).
DOC_STORE: dict[str, dict[str, Any]] = {}
