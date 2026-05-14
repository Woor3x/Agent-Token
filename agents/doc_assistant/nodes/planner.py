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
import os
import re
from pathlib import Path
from typing import Any

from agents.common.llm import ChatMessage, LLMError, LLMProvider
from agents.common.logging import get_logger

_log = get_logger("agents.doc_assistant.planner")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "planner_system.txt"

_ACTION_ENUM = {
    "feishu.bitable.read",
    "feishu.bitable.read_all",
    "feishu.contact.read",
    "feishu.calendar.read",
    "feishu.docx.read",
    "web.search",
    "web.fetch",
    "feishu.doc.write",
}
# Allow URL-safe chars in addition to capability-style resource ids — required
# for ``web.fetch`` tasks whose resource is a full https URL with query string.
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9._:/*@\-?=&#%~+]+$")


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


# ---- env-driven default resources -------------------------------------------
# Real Feishu deployments override these via env so the planner stops handing
# out mock identifiers (`bascn_alice/tbl_q1`) that 91402 NOTEXIST upstream.


def _default_bitable_resource(state: dict[str, Any] | None = None) -> str | None:
    """Resolve bitable resource id from the user's front-end selection.

    Returns ``None`` when no explicit selection — callers must skip emitting
    a bitable task in that case. We deliberately do **not** fall back to env
    defaults: the front-end picker is the only source of truth, so the
    planner never silently reads a stale, env-pinned bitable that the user
    didn't ask for.
    """
    if state:
        sel = state.get("bitable") or {}
        app = sel.get("app_token")
        tbl = sel.get("table_id")
        if app and tbl:
            return f"app_token:{app}/table:{tbl}"
        if app:
            # Whole-bitable selection — handled via read_all elsewhere; the
            # legacy single-table resource string isn't applicable here.
            return None
    return None


def _build_one_source_task(sel: dict, idx: int) -> dict | None:
    """Convert a single front-end selection to a data-fetch task.

    Three shapes (legacy ``{app_token, table_id}`` accepted as bitable):
    single-table read, whole-bitable read, and docx read. Returns ``None`` if
    selection is incomplete.
    """
    kind = (sel.get("kind") or "").lower()
    app = sel.get("app_token")
    tbl = sel.get("table_id")
    doc_id = sel.get("document_id")

    if not kind and app:
        kind = "bitable"

    if kind == "bitable" and app and tbl:
        return {
            "id": f"t{idx}", "agent": "data_agent",
            "action": "feishu.bitable.read",
            "resource": f"app_token:{app}/table:{tbl}",
            "params": {"page_size": 100}, "deps": [],
        }
    if kind == "bitable" and app:
        return {
            "id": f"t{idx}", "agent": "data_agent",
            "action": "feishu.bitable.read_all",
            "resource": f"app_token:{app}",
            "params": {"page_size": 100}, "deps": [],
        }
    if kind == "docx" and doc_id:
        return {
            "id": f"t{idx}", "agent": "data_agent",
            "action": "feishu.docx.read",
            "resource": f"document_id:{doc_id}",
            "params": {}, "deps": [],
        }
    return None


def _data_source_tasks(state: dict[str, Any] | None, start_idx: int) -> list[dict]:
    """Build leading data-fetch tasks from front-end selections.

    Reads ``state['bitables']`` (multi-select list, preferred) or falls back to
    legacy ``state['bitable']`` singleton. Each valid selection becomes one
    task, with sequential ids starting at ``start_idx``. Empty/invalid entries
    are skipped silently — caller falls back to keyword heuristics if the
    returned list is empty.
    """
    if not state:
        return []
    raw_list = state.get("bitables")
    selections: list[dict]
    if isinstance(raw_list, list):
        selections = [s for s in raw_list if isinstance(s, dict)]
    else:
        legacy = state.get("bitable")
        selections = [legacy] if isinstance(legacy, dict) else []

    out: list[dict] = []
    idx = start_idx
    for sel in selections:
        task = _build_one_source_task(sel, idx)
        if task:
            out.append(task)
            idx += 1
    return out


def _data_source_task(state: dict[str, Any] | None, idx: int) -> dict | None:
    """Back-compat shim: returns the first data-source task, if any."""
    tasks = _data_source_tasks(state, idx)
    return tasks[0] if tasks else None


def _default_contact_dept() -> str | None:
    """None → planner skips contact tasks (no real dept_id configured)."""
    return os.environ.get("FEISHU_CONTACT_DEPT_ID") or None


def _default_calendar_id() -> str | None:
    """None → planner skips calendar tasks (no real calendar_id configured)."""
    return os.environ.get("FEISHU_CALENDAR_ID") or None


# ---- rule-based fallback ----------------------------------------------------


def _rule_plan(prompt: str, state: dict[str, Any] | None = None) -> list[dict]:
    p = prompt.lower()
    tasks: list[dict] = []
    # Front-end picker selections → leading data-fetch tasks (one per source).
    # Wins over keyword heuristics: when the user explicitly chose docx/bitable
    # source(s), that's what they want analysed regardless of prompt wording.
    explicit = _data_source_tasks(state, 1)
    if explicit:
        tasks.extend(explicit)
    elif (
        "sales" in p or "q1" in p or "bitable" in p
        or "销售" in p or "业绩" in p
    ):
        # Only emit a bitable task when the user explicitly picked one in
        # state; without a selection we'd be guessing at an app_token.
        res = _default_bitable_resource(state)
        if res:
            tasks.append({
                "id": "t1", "agent": "data_agent",
                "action": "feishu.bitable.read",
                "resource": res,
                "params": {"page_size": 100},
                "deps": [],
            })
    if "team" in p or "member" in p or "sales team" in p or "成员" in p:
        dept = _default_contact_dept()
        if dept:
            tasks.append({
                "id": f"t{len(tasks)+1}", "agent": "data_agent",
                "action": "feishu.contact.read",
                "resource": f"department:{dept}",
                "params": {},
                "deps": [],
            })
    if "industry" in p or "research" in p or "zero trust" in p or "行业" in p or "调研" in p:
        tasks.append({
            "id": f"t{len(tasks)+1}", "agent": "web_agent",
            "action": "web.search",
            "resource": "*",
            "params": {"query": prompt, "max_results": 3, "fetch_top_k": 2},
            "deps": [],
        })
    if not tasks:
        tasks.append({
            "id": "t1", "agent": "web_agent",
            "action": "web.search",
            "resource": "*",
            "params": {"query": prompt, "max_results": 3, "fetch_top_k": 2},
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


def _load_system_prompt(state: dict[str, Any] | None = None) -> str:
    try:
        raw = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "你是任务规划器，输出 JSON {tasks:[...]}, action 取自固定枚举, "
            "最后一步必须为 doc_assistant.feishu.doc.write。"
        )
    # Inject the user's front-end pick into the prompt. When no selection,
    # tell the LLM bitable is off-limits so it stops fabricating tokens.
    bitable_res = _default_bitable_resource(state)
    raw = raw.replace(
        "{{BITABLE_RESOURCE}}",
        bitable_res if bitable_res
        else "（用户未选择多维表格 — 禁止生成 feishu.bitable.read / read_all 任务）",
    )
    dept = _default_contact_dept()
    cal = _default_calendar_id()
    raw = raw.replace(
        "{{CONTACT_HINT}}",
        f"department:{dept}" if dept else "（未配置 FEISHU_CONTACT_DEPT_ID — 禁止生成 feishu.contact.read 任务）",
    )
    raw = raw.replace(
        "{{CALENDAR_HINT}}",
        f"calendar:{cal}" if cal else "（未配置 FEISHU_CALENDAR_ID — 禁止生成 feishu.calendar.read 任务）",
    )
    return raw


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


async def _llm_plan(
    prompt: str, llm: LLMProvider, state: dict[str, Any] | None = None
) -> list[dict]:
    msgs = [
        ChatMessage(role="system", content=_load_system_prompt(state)),
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

    # When the user explicitly selected any data source(s), force the rule
    # path: it deterministically emits exactly the right task(s) per selection.
    # The LLM path is only used when the user gave no explicit selection, so it
    # can freely plan web searches or other tasks.
    # Previously this was `> 1`, which let single-selection cases fall through
    # to the LLM, causing hallucinated tokens (e.g. app_token:default) and
    # duplicate/spurious read_all tasks.
    explicit_tasks = _data_source_tasks(state, 1)

    dag: list[dict] | None = None
    if llm is not None and not explicit_tasks:
        try:
            dag = await _llm_plan(prompt, llm, state)
            validate_dag(dag)
            _log.info(f"planner=llm tasks={len(dag)}")
        except (LLMError, ValueError, json.JSONDecodeError) as e:
            _log.warning(f"llm planner failed, falling back: {e}")
            dag = None

    if dag is None:
        dag = _rule_plan(prompt, state)
        validate_dag(dag)
        _log.info(f"planner=rule tasks={len(dag)} explicit={len(explicit_tasks)}")

    return {**state, "dag": dag}
