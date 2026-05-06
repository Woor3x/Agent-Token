"""Synthesizer node: fuse agent results into document blocks.

When ``state["llm"]`` carries an :class:`LLMProvider` instance, the node also
prepends an LLM-generated executive summary block. Without an LLM (the tests'
default) the node is purely template-driven so behavior stays deterministic.

LLM output schema (see ``prompts/synthesizer_system.txt``):

    {
      "title": "<中文报告标题>",
      "summary": "<执行摘要 250-450 字>",
      "observations": [{"section": "<段落标题>", "text": "<解读>"}],
      "recommendations": ["<行动建议>"]
    }

The structured fields are then materialized into Feishu blocks. Output of the
LLM is best-effort: if any field is missing or malformed we fall back to a
plain summary block instead of failing the whole doc.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.common.llm import ChatMessage, LLMError, LLMProvider
from agents.common.logging import get_logger

_log = get_logger("agents.doc_assistant.synthesizer")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "synthesizer_system.txt"

# Human-friendly Chinese section headings keyed by action enum.
_SECTION_LABEL = {
    "feishu.bitable.read": "业务数据",
    "feishu.contact.read": "团队结构",
    "feishu.calendar.read": "近期日程",
    "web.search":           "行业调研",
    "web.fetch":            "网页摘要",
}

# Default doc title when LLM is absent or output unusable.
_FALLBACK_TITLE = "执行报告"


def _section_label(action: str, fallback_tid: str) -> str:
    return _SECTION_LABEL.get(action, fallback_tid)


def _bitable_observation(records: list[dict], top_n: int = 3) -> str | None:
    """Pre-table summary: row count + first numeric field totals + top-N rows.

    Cheap deterministic counterpart to the LLM observations — used as a
    fallback when the LLM didn't produce per-section commentary, or when
    no LLM is wired in.
    """
    if not records:
        return None
    n = len(records)
    # Find first numeric-ish column for a total.
    numeric_key: str | None = None
    total = 0.0
    for r in records:
        for k, v in (r.get("fields") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_key = k
                break
        if numeric_key:
            break
    if numeric_key:
        for r in records:
            v = (r.get("fields") or {}).get(numeric_key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += float(v)
    parts: list[str] = [f"共 {n} 行数据"]
    if numeric_key:
        parts.append(f"{numeric_key} 合计 {total:g}")
    # Top-N: show first 3 rows' first textual field as labels
    label_keys: list[str] = []
    if records:
        for k, v in (records[0].get("fields") or {}).items():
            if isinstance(v, str):
                label_keys.append(k)
                break
    if label_keys:
        labels = [
            str((r.get("fields") or {}).get(label_keys[0], ""))
            for r in records[:top_n]
        ]
        labels = [s for s in labels if s]
        if labels:
            parts.append("Top: " + " / ".join(labels))
    return "；".join(parts) + "。"


def _rows_to_table(records: list[dict]) -> list[dict]:
    if not records:
        return [{"block_type": "text", "text": "(暂无数据)"}]
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
    return [{"block_type": "text", "text": "\n".join(lines) or "(暂无成员)"}]


def _search_to_block(hits: list[dict]) -> list[dict]:
    lines = [f"- [{h.get('title','?')}]({h.get('url','')}) — {h.get('snippet','')}" for h in hits]
    return [{"block_type": "text", "text": "\n".join(lines) or "(无检索结果)"}]


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
        return (
            "你是飞书文档撰写助手。输出严格 JSON 对象 "
            '{"title":"...","summary":"...","observations":[],"recommendations":[]}。'
        )


def _extract_json(raw: str) -> dict:
    """Best-effort JSON extraction (mirrors planner._extract_json)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


async def _llm_compose(llm: LLMProvider, state: dict[str, Any]) -> dict[str, Any]:
    """Ask the LLM for the structured composition payload (title + summary + observations + recommendations)."""
    digest = _digest_results(state)
    msgs = [
        ChatMessage(role="system", content=_load_system_prompt()),
        ChatMessage(role="user", content=digest),
    ]
    res = await llm.chat(messages=msgs, temperature=0.3, max_tokens=900, json_mode=True)
    payload = _extract_json(res.content or "")
    if not isinstance(payload, dict):
        raise ValueError("LLM did not produce a JSON object")
    return payload


def _normalize_observations(payload: dict) -> dict[str, str]:
    """Index observations[*] by action label (best-effort fuzzy match)."""
    obs = payload.get("observations") or []
    by_label: dict[str, str] = {}
    if not isinstance(obs, list):
        return by_label
    for item in obs:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or "").strip()
        text = str(item.get("text") or "").strip()
        if section and text:
            by_label[section] = text
    return by_label


async def synthesizer_node(state: dict[str, Any]) -> dict[str, Any]:
    llm: LLMProvider | None = state.get("llm")

    title = _FALLBACK_TITLE
    summary: str | None = None
    obs_by_label: dict[str, str] = {}
    recs: list[str] = []

    if llm is not None:
        try:
            payload = await _llm_compose(llm, state)
            t = str(payload.get("title") or "").strip()
            if t and "auto report" not in t.lower():
                title = t[:40]  # safety clamp
            s = str(payload.get("summary") or "").strip()
            if s:
                summary = s
            obs_by_label = _normalize_observations(payload)
            r = payload.get("recommendations") or []
            if isinstance(r, list):
                recs = [str(x).strip() for x in r if str(x).strip()][:5]
        except (LLMError, ValueError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            _log.warning(f"llm composition failed, using template fallback: {e}")

    blocks: list[dict] = [{"block_type": "heading1", "text": title}]

    if summary:
        blocks.append({"block_type": "heading2", "text": "执行摘要"})
        blocks.append({"block_type": "text", "text": summary})

    dag_by_id = {t["id"]: t for t in state.get("dag", [])}
    for tid, data in state.get("results", {}).items():
        task = dag_by_id.get(tid, {})
        action = task.get("action", "")
        section_title = _section_label(action, fallback_tid=f"{tid}: {action}")
        blocks.append({"block_type": "heading2", "text": section_title})

        # Per-section LLM observation (if matched), else deterministic fallback for tables.
        llm_obs = obs_by_label.get(section_title)
        if llm_obs:
            blocks.append({"block_type": "text", "text": llm_obs})
        elif action == "feishu.bitable.read":
            cheap = _bitable_observation(data.get("records") or [])
            if cheap:
                blocks.append({"block_type": "text", "text": cheap})

        if action == "feishu.bitable.read":
            blocks.extend(_rows_to_table(data.get("records") or []))
        elif action == "feishu.contact.read":
            blocks.extend(_users_to_block(data.get("users") or []))
        elif action == "feishu.calendar.read":
            events = data.get("events") or []
            lines = [f"- {e.get('summary','?')} @ {e.get('start_time','')}" for e in events]
            blocks.append({"block_type": "text", "text": "\n".join(lines) or "(暂无日程)"})
        elif action == "web.search":
            blocks.extend(_search_to_block(data.get("results") or []))
        elif action == "web.fetch":
            blocks.append({"block_type": "text", "text": data.get("summary", "")})
        else:
            blocks.append({"block_type": "text", "text": str(data)})

    if recs:
        blocks.append({"block_type": "heading2", "text": "行动建议"})
        rec_text = "\n".join(f"- {r}" for r in recs)
        blocks.append({"block_type": "text", "text": rec_text})

    return {**state, "blocks": blocks, "doc_title": title}
