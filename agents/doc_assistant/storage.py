"""Local document store for doc_assistant.

When ``DOC_STORAGE=local`` (the default), :mod:`nodes.doc_writer` persists
generated documents here instead of pushing them to Feishu Docx. The store
is a flat directory of JSON blobs keyed by ``doc_id``; the agent then exposes
``GET /docs`` and ``GET /docs/{id}`` so the web UI can render them without
needing user-space drive permissions on Feishu.

Each record::

    {
      "document_id": "doc_local_<ulid>",
      "title":       "<doc title>",
      "created_at":  <unix seconds>,
      "blocks":      [{"block_type": "...", "text": "..."}, ...]
    }

Storage path is ``DOC_STORAGE_DIR`` (default ``/app/data/docs``). Directory
is created lazily.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from agents.common.ulid import new_ulid


def _store_dir() -> Path:
    """Resolve the on-disk doc store directory.

    Honor ``DOC_STORAGE_DIR`` first; fall back to ``/app/data/docs`` (the
    container path); if neither is writable (e.g. local pytest), drop to
    a temp dir under ``$TMPDIR`` so tests don't need to mock the filesystem.
    """
    explicit = os.environ.get("DOC_STORAGE_DIR")
    candidates = [Path(explicit)] if explicit else []
    candidates.append(Path("/app/data/docs"))
    import tempfile
    candidates.append(Path(tempfile.gettempdir()) / "agent-token-docs")
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            return cand
        except (OSError, PermissionError):
            continue
    raise RuntimeError("no writable DOC_STORAGE_DIR candidate")


def is_local() -> bool:
    return os.environ.get("DOC_STORAGE", "local").lower() == "local"


def save(*, title: str, blocks: list[dict]) -> dict[str, Any]:
    doc_id = f"doc_local_{new_ulid()}"
    record = {
        "document_id": doc_id,
        "title": title,
        "created_at": int(time.time()),
        "blocks": blocks or [],
    }
    path = _store_dir() / f"{doc_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def get(doc_id: str) -> dict[str, Any] | None:
    # Reject path traversal: doc_id is `doc_local_<ulid>` shaped.
    if "/" in doc_id or ".." in doc_id:
        return None
    path = _store_dir() / f"{doc_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_recent(limit: int = 50) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in _store_dir().glob("doc_local_*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "document_id": doc.get("document_id", p.stem),
                    "title": doc.get("title", ""),
                    "created_at": doc.get("created_at", 0),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda d: d.get("created_at", 0), reverse=True)
    return out[:limit]
