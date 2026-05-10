"""Translate the agent's intermediate block dicts to real Feishu Docx blocks.

Internal block shape (emitted by ``synthesizer.py``)::

    {"block_type": "heading1" | "heading2" | "text", "text": "..."}

Real Docx ``children`` payload uses numeric ``block_type`` and structured
``elements`` arrays. See:
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create
"""
from __future__ import annotations

from typing import Any

# Real Feishu Docx block_type ints.
_BT_TEXT = 2
_BT_H1 = 3
_BT_H2 = 4
_BT_CODE = 14

# Feishu language enum for code blocks. 1 = PLAIN_TEXT.
# https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/data-structure/block#code
_LANG_PLAIN = 1

_KIND_TO_INT = {
    "heading1": _BT_H1,
    "heading2": _BT_H2,
    "text": _BT_TEXT,
    "code": _BT_CODE,
}


def _text_run(content: str) -> dict[str, Any]:
    # Feishu rejects empty ``content``; pad with a single space so a heading
    # with an empty body still renders rather than 400-ing the batch.
    return {"text_run": {"content": content or " ", "text_element_style": {}}}


def _build_block(kind: str, text: str) -> dict[str, Any]:
    bt = _KIND_TO_INT.get(kind, _BT_TEXT)
    if bt == _BT_CODE:
        # Code blocks render in monospace with line wrapping disabled — ideal
        # for ASCII flow diagrams. Language defaults to plain text.
        return {
            "block_type": bt,
            "code": {
                "elements": [_text_run(text)],
                "style": {"language": _LANG_PLAIN, "wrap": False},
            },
        }
    field = {_BT_H1: "heading1", _BT_H2: "heading2", _BT_TEXT: "text"}[bt]
    return {
        "block_type": bt,
        field: {"elements": [_text_run(text)], "style": {}},
    }


def to_feishu_children(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for b in blocks:
        kind = b.get("block_type", "text")
        text = str(b.get("text", ""))
        out.append(_build_block(kind, text))
    return out
