"""Synthesizer node: fuse agent results into document blocks.

When ``state["llm"]`` carries an :class:`LLMProvider` instance, the node also
prepends an LLM-generated executive summary block. Without an LLM (the tests'
default) the node is purely template-driven so behavior stays deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.common.llm import ChatMessage, LLMError, LLMProvider
from agents.common.logging import get_logger

_log = get_logger("agents.doc_assistant.synthesizer")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "synthesizer_system.txt"


def _rows_to_table(records: list[dict]) -> list[dict]:
    if not records:
        return [{"block_type": "text", "text": "(no rows)"}]
    keys: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in (r.get("fields") or {}):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    header = " | ".join(keys)
    lines = [header, " | ".join("---" for _ in keys)]
    for r in records:
        f = r.get("fields") or {}
        lines.append(" | ".join(str(f.get(k, "")) for k in keys))
    return [{"block_type": "text", "text": "\n".join(lines)}]


def _users_to_block(users: list[dict]) -> list[dict]:
    lines = [f"- {u.get('name','?')} <{u.get('email','')}>" for u in users]
    return [{"block_type": "text", "text": "\n".join(lines) or "(no members)"}]


def _search_to_block(hits: list[dict]) -> list[dict]:
    lines = [f"- [{h.get('title','?')}]({h.get('url','')}) — {h.get('snippet','')}" for h in hits]
    return [{"block_type": "text", "text": "\n".join(lines) or "(no hits)"}]


def _digest_results(state: dict[str, Any]) -> str:
    """Compact JSON-ish text view of every task's output for the LLM prompt.

    Truncates large lists to the first few items so the prompt stays bounded.
    """
    dag_by_id = {t["id"]: t for t in state.get("dag", [])}
    chunks: list[str] = []
    user_prompt = state.get("user_prompt", "")
    if user_prompt:
        chunks.append(f"用户请求: {user_prompt}")
    for tid, data in state.get("results", {}).items():
        task = dag_by_id.get(tid, {})
        action = task.get("action", "")
        compact: dict[str, Any] = {"task": tid, "action": action}
        if action == "feishu.bitable.read":
            recs = (data.get("records") or [])[:5]
            compact["records"] = [r.get("fields") for r in recs]
            compact["count"] = data.get("count")
        elif action == "feishu.contact.read":
            users = (data.get("users") or [])[:8]
            compact["users"] = users
            compact["count"] = data.get("count")
        elif action == "feishu.calendar.read":
            compact["events"] = (data.get("events") or [])[:5]
            compact["count"] = data.get("count")
        elif action == "web.search":
            compact["query"] = data.get("query")
            compact["results"] = (data.get("results") or [])[:5]
        elif action == "web.fetch":
            compact["url"] = data.get("url")
            compact["summary"] = data.get("summary", "")[:500]
        else:
            compact["data"] = data
        chunks.append(json.dumps(compact, ensure_ascii=False))
    return "\n".join(chunks)


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "你是飞书文档撰写助手，请用中文输出 80–200 字执行摘要。"


async def _llm_summarize(llm: LLMProvider, state: dict[str, Any]) -> str:
    digest = _digest_results(state)
    msgs = [
        ChatMessage(role="system", content=_load_system_prompt()),
        ChatMessage(role="user", content=digest),
    ]
    res = await llm.chat(messages=msgs, temperature=0.3, max_tokens=400)
    return (res.content or "").strip()


async def synthesizer_node(state: dict[str, Any]) -> dict[str, Any]:
    blocks: list[dict] = [{"block_type": "heading1", "text": "Auto Report"}]

    llm: LLMProvider | None = state.get("llm")
    if llm is not None:
        try:
            summary = await _llm_summarize(llm, state)
            if summary:
                blocks.append({"block_type": "heading2", "text": "执行摘要"})
                blocks.append({"block_type": "text", "text": summary})
        except (LLMError, Exception) as e:  # noqa: BLE001 — never fail the doc on LLM
            _log.warning(f"llm summary skipped: {e}")

    dag_by_id = {t["id"]: t for t in state.get("dag", [])}
    for tid, data in state.get("results", {}).items():
        task = dag_by_id.get(tid, {})
        action = task.get("action", "")
        blocks.append({"block_type": "heading2", "text": f"{tid}: {action}"})
        if action == "feishu.bitable.read":
            blocks.extend(_rows_to_table(data.get("records") or []))
        elif action == "feishu.contact.read":
            blocks.extend(_users_to_block(data.get("users") or []))
        elif action == "feishu.calendar.read":
            events = data.get("events") or []
            lines = [f"- {e.get('summary','?')} @ {e.get('start_time','')}" for e in events]
            blocks.append({"block_type": "text", "text": "\n".join(lines) or "(no events)"})
        elif action == "web.search":
            blocks.extend(_search_to_block(data.get("results") or []))
        elif action == "web.fetch":
            blocks.append({"block_type": "text", "text": data.get("summary", "")})
        else:
            blocks.append({"block_type": "text", "text": str(data)})
    return {**state, "blocks": blocks}
