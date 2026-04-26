"""Planner node: prompt → DAG JSON.

Two paths:

* **LLM path** — when ``state["llm"]`` is set, the planner asks the model
  (Volc Ark Doubao Seed by default in production) to emit a JSON DAG following
  ``prompts/planner_system.txt``. Output is parsed and run through
  ``validate_dag`` for safety.

* **Rule path (fallback)** — deterministic keyword matcher used when no LLM is
  injected, when the LLM call fails, or when the LLM emits an invalid DAG. Keeps
  tests reproducible and the demo runnable offline.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.common.llm import ChatMessage, LLMError, LLMProvider
from agents.common.logging import get_logger

_log = get_logger("agents.doc_assistant.planner")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "planner_system.txt"

_ACTION_ENUM = {
    "feishu.bitable.read",
    "feishu.contact.read",
    "feishu.calendar.read",
    "web.search",
    "web.fetch",
    "feishu.doc.write",
}
_RESOURCE_RE = re.compile(r"^[a-zA-Z0-9._:/*@-]+$")


def validate_dag(dag: list[dict]) -> None:
    seen: set[str] = set()
    for t in dag:
        tid = t.get("id")
        if not tid or tid in seen:
            raise ValueError(f"bad task id: {tid}")
        seen.add(tid)
        if t.get("agent") not in {"data_agent", "web_agent", "doc_assistant"}:
            raise ValueError(f"bad agent: {t.get('agent')}")
        if t.get("action") not in _ACTION_ENUM:
            raise ValueError(f"action not in enum: {t.get('action')}")
        res = t.get("resource", "")
        if not _RESOURCE_RE.match(res):
            raise ValueError(f"bad resource: {res}")
        deps = t.get("deps") or []
        for d in deps:
            if d not in seen:
                raise ValueError(f"dep {d} of {tid} not declared before")


# ---- rule-based fallback ----------------------------------------------------


def _rule_plan(prompt: str) -> list[dict]:
    p = prompt.lower()
    tasks: list[dict] = []
    if "sales" in p or "q1" in p or "bitable" in p or "销售" in p or "业绩" in p:
        tasks.append({
            "id": "t1", "agent": "data_agent",
            "action": "feishu.bitable.read",
            "resource": "app_token:bascn_alice/table:tbl_q1",
            "params": {"page_size": 100},
            "deps": [],
        })
    if "team" in p or "member" in p or "sales team" in p or "成员" in p:
        tasks.append({
            "id": f"t{len(tasks)+1}", "agent": "data_agent",
            "action": "feishu.contact.read",
            "resource": "department:sales",
            "params": {},
            "deps": [],
        })
    if "industry" in p or "research" in p or "zero trust" in p or "行业" in p or "调研" in p:
        tasks.append({
            "id": f"t{len(tasks)+1}", "agent": "web_agent",
            "action": "web.search",
            "resource": "*",
            "params": {"query": prompt, "max_results": 3},
            "deps": [],
        })
    if not tasks:
        tasks.append({
            "id": "t1", "agent": "web_agent",
            "action": "web.search",
            "resource": "*",
            "params": {"query": prompt, "max_results": 3},
            "deps": [],
        })
    deps = [t["id"] for t in tasks]
    tasks.append({
        "id": f"t{len(tasks)+1}", "agent": "doc_assistant",
        "action": "feishu.doc.write",
        "resource": "doc_token:auto",
        "params": {"title": "Auto Report"},
        "deps": deps,
    })
    return tasks


# ---- LLM-based path ---------------------------------------------------------


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "你是任务规划器，输出 JSON {tasks:[...]}, action 取自固定枚举, "
            "最后一步必须为 doc_assistant.feishu.doc.write。"
        )


def _extract_json(raw: str) -> dict:
    """Pull a JSON object out of an LLM response.

    The model usually obeys ``response_format=json_object`` and emits a clean
    object. Some providers wrap it in fences or add prose; strip whatever we
    can before failing.
    """
    s = raw.strip()
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


async def _llm_plan(prompt: str, llm: LLMProvider) -> list[dict]:
    msgs = [
        ChatMessage(role="system", content=_load_system_prompt()),
        ChatMessage(role="user", content=prompt),
    ]
    res = await llm.chat(messages=msgs, temperature=0.1, max_tokens=800, json_mode=True)
    payload = _extract_json(res.content or "")
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("LLM did not produce tasks[]")
    # If the model forgot the final doc.write, append it.
    if not any(t.get("action") == "feishu.doc.write" for t in tasks):
        deps = [t["id"] for t in tasks if t.get("id")]
        tasks.append({
            "id": f"t{len(tasks)+1}", "agent": "doc_assistant",
            "action": "feishu.doc.write",
            "resource": "doc_token:auto",
            "params": {"title": "Auto Report"},
            "deps": deps,
        })
    return tasks


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    prompt = state.get("user_prompt", "")
    llm: LLMProvider | None = state.get("llm")

    dag: list[dict] | None = None
    if llm is not None:
        try:
            dag = await _llm_plan(prompt, llm)
            validate_dag(dag)
            _log.info(f"planner=llm tasks={len(dag)}")
        except (LLMError, ValueError, json.JSONDecodeError) as e:
            _log.warning(f"llm planner failed, falling back: {e}")
            dag = None

    if dag is None:
        dag = _rule_plan(prompt)
        validate_dag(dag)
        _log.info(f"planner=rule tasks={len(dag)}")

    return {**state, "dag": dag}
