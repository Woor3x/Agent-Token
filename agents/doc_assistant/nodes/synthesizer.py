"""Synthesizer node: fuse agent results into document blocks.

Optionally embeds an ASCII flow diagram of the executed DAG between the
executive summary and the per-section breakdown — controlled by env
``DOC_ASCII_FLOW`` (``auto`` / ``always`` / ``off``, default ``auto``) or
``state["ascii_flow_mode"]`` for per-request override. The diagram lives in a
Feishu Docx code block so it renders in a monospace font.

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
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.common.llm import ChatMessage, LLMError, LLMProvider
from agents.common.logging import get_logger

from ._ascii_flow import render_dag_ascii, should_render

_log = get_logger("agents.doc_assistant.synthesizer")

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "synthesizer_system.txt"

# Human-friendly Chinese section headings keyed by action enum.
_SECTION_LABEL = {
    "feishu.bitable.read":     "业务数据",
    "feishu.bitable.read_all": "业务数据（全表）",
    "feishu.contact.read":     "团队结构",
    "feishu.calendar.read":    "近期日程",
    "feishu.docx.read":        "原文摘录",
    "web.search":              "行业调研",
    "web.fetch":                "网页摘要",
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
    # Find first true-numeric column for a total. Exclude bools and unix-ms
    # timestamps — summing dates produces meaningless totals like
    # "完成日期 合计 5.4e+15".
    def _is_real_number(x: Any) -> bool:
        return (
            isinstance(x, (int, float))
            and not isinstance(x, bool)
            and not _looks_like_unix_ms(x)
        )

    numeric_key: str | None = None
    total = 0.0
    for r in records:
        for k, v in (r.get("fields") or {}).items():
            if _is_real_number(v):
                numeric_key = k
                break
        if numeric_key:
            break
    if numeric_key:
        for r in records:
            v = (r.get("fields") or {}).get(numeric_key)
            if _is_real_number(v):
                total += float(v)
    parts: list[str] = [f"共 {n} 行数据"]
    if numeric_key:
        parts.append(f"{numeric_key} 合计 {_fmt_number(total)}")
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


# Bitable cell shapes encountered in real Feishu responses:
#   - Number: int OR float (large floats render as "1.9e+13" via str())
#   - DateTime: int milliseconds since epoch (~1e12, e.g. 1731859200000)
#   - User: list[{name, en_name, email, id}]
#   - Text/RichText: list[{text, type}]
#   - SingleSelect / MultiSelect: str OR list[str]
# ``str(v)`` turns the dict/list shapes into raw Python repr garbage, and
# floats over ~1e10 render in scientific notation. ``_fmt_cell`` normalizes
# all of these into terse human strings before the markdown table is built.

# Plausible unix-ms timestamp window: 2001-09-09 (1e12) to 2286 (1e13). Larger
# than that → not a date; smaller → too noisy to convert. A few cents into the
# range covers all practical Feishu DateTime fields.
_MS_LO = 1_000_000_000_000  # 2001-09-09
_MS_HI = 9_999_999_999_999  # 2286-11-20


def _looks_like_unix_ms(n: float) -> bool:
    return isinstance(n, (int, float)) and not isinstance(n, bool) and _MS_LO <= n <= _MS_HI


def _fmt_number(v: int | float) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    # float: kill scientific notation; collapse integer-valued floats to int.
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    # ``f"{v:.6g}"`` keeps precision but can still produce "1e+15" — fall
    # back to a fixed format with trimmed zeros.
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _fmt_cell(v: Any) -> str:
    """Render one Feishu bitable cell value as a short human string."""
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        if _looks_like_unix_ms(v):
            try:
                return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                pass
        return _fmt_number(v)
    if isinstance(v, str):
        s = v.strip()
        # Collapse internal whitespace runs so multi-line cells don't blow
        # up the markdown table layout.
        return re.sub(r"\s+", " ", s)
    if isinstance(v, list):
        if not v:
            return ""
        # Rich-text / Text element list: [{text, type}, ...]
        if all(isinstance(x, dict) and "text" in x for x in v):
            return _fmt_cell(" ".join(str(x.get("text", "")) for x in v))
        # User / Member list: [{name|en_name|email, ...}, ...]
        if all(isinstance(x, dict) and ("name" in x or "en_name" in x or "email" in x) for x in v):
            names = [
                str(x.get("name") or x.get("en_name") or x.get("email") or "")
                for x in v
            ]
            return ", ".join(s for s in names if s)
        # Attachment / URL list: [{name|file_name|url, ...}, ...]
        if all(isinstance(x, dict) for x in v):
            return ", ".join(
                str(x.get("name") or x.get("file_name") or x.get("url") or json.dumps(x, ensure_ascii=False))
                for x in v
            )
        # Plain primitive list (multi-select strings, etc.)
        return ", ".join(_fmt_cell(x) for x in v)
    if isinstance(v, dict):
        # Single-element shapes that occasionally show up directly (not wrapped in list)
        if "text" in v:
            return _fmt_cell(v.get("text"))
        if "name" in v or "en_name" in v:
            return str(v.get("name") or v.get("en_name") or "")
        if "url" in v:
            return str(v.get("url") or "")
        # Last-resort: compact JSON instead of Python repr.
        return json.dumps(v, ensure_ascii=False)
    return str(v)


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

    def _row(cells: list[str]) -> str:
        # Canonical GFM table row: leading + trailing pipe so remark-gfm parses
        # reliably even when the preceding paragraph lacks a blank-line gap.
        return "| " + " | ".join(cells) + " |"

    def _clean(v: Any) -> str:
        # Cell values must not contain raw pipes (would split the row) or
        # newlines (would terminate the table mid-stream). ``_fmt_cell`` already
        # collapses string whitespace; defend against list/dict shapes that
        # round-tripped through ``str(...)``.
        s = _fmt_cell(v)
        return s.replace("|", "\\|").replace("\n", " ").strip()

    header = _row(keys)
    divider = _row(["---"] * len(keys))
    lines = [header, divider]
    for r in records:
        f = r.get("fields") or {}
        lines.append(_row([_clean(f.get(k, "")) for k in keys]))
    # Wrap with blank lines on both ends. ``blocksToMarkdown`` already joins
    # with "\n\n", but an extra leading/trailing newline guards against
    # adjacent text blocks that get merged into the same paragraph by future
    # changes — markdown tables silently degrade to plain text without a
    # blank line above them.
    return [{"block_type": "text", "text": "\n" + "\n".join(lines) + "\n"}]


def _users_to_block(users: list[dict]) -> list[dict]:
    lines = [f"- {u.get('name','?')} <{u.get('email','')}>" for u in users]
    return [{"block_type": "text", "text": "\n".join(lines) or "(暂无成员)"}]


# Embedded markdown links inside a snippet (Google News etc. dump their nav
# bar into the search snippet, which arrives as ``[新闻](url). [图片](url)...``).
# Strip the wrapper so only the visible label remains — otherwise the rendered
# snippet looks like raw markdown source.
_INLINE_LINK_RE = re.compile(r"\[([^\]\n]{0,80})\]\(\s*[^)\s]{1,300}\s*\)")
_INLINE_IMG_RE = re.compile(r"!\[([^\]\n]{0,80})\]\(\s*[^)\s]{1,300}\s*\)")


def _clean_snippet(s: str, max_len: int = 220) -> str:
    """Normalize a search-result snippet into safe inline markdown.

    1. Strip embedded ``[label](url)`` / ``![alt](url)`` patterns down to the
       label so they don't render as nested links inside our outer
       ``- [title](url) — snippet`` row.
    2. Collapse whitespace runs (incl. newlines) into single spaces.
    3. Defang remaining structural chars (``|`` for tables, leading ``#`` /
       ``>`` / list markers) so the snippet stays inline.
    4. Truncate to ``max_len`` so a single hit can't dominate the layout.
    """
    if not s:
        return ""
    s = _INLINE_IMG_RE.sub(r"\1", s)
    s = _INLINE_LINK_RE.sub(r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Pipe would split markdown tables; backticks would open a code span and
    # eat trailing content. Escape both. Brackets are safe now that link
    # syntax is gone.
    s = s.replace("|", "\\|").replace("`", "\\`")
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _clean_title(s: str, max_len: int = 80) -> str:
    """Snippet rules minus the link-stripping (titles rarely contain links)."""
    if not s:
        return "?"
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("[", "(").replace("]", ")").replace("|", "/").replace("`", "'")
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _search_to_block(hits: list[dict]) -> list[dict]:
    lines: list[str] = []
    for h in hits:
        title = _clean_title(h.get("title", "?"))
        url = (h.get("url") or "").strip()
        snippet = _clean_snippet(h.get("snippet", ""))
        if url:
            head = f"- [{title}]({url})"
        else:
            head = f"- {title}"
        lines.append(f"{head} — {snippet}" if snippet else head)
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
            compact["records"] = [
                {k: _fmt_cell(v) for k, v in (r.get("fields") or {}).items()}
                for r in recs
            ]
            compact["count"] = data.get("count")
        elif action == "feishu.bitable.read_all":
            recs = (data.get("records") or [])[:5]
            compact["records"] = [
                {
                    "_table": r.get("_table"),
                    **{k: _fmt_cell(v) for k, v in (r.get("fields") or {}).items()},
                }
                for r in recs
            ]
            compact["count"] = data.get("count")
            compact["tables"] = data.get("tables") or []
        elif action == "feishu.docx.read":
            blks = (data.get("blocks") or [])[:30]
            compact["text"] = "\n".join(b.get("text", "") for b in blks)[:2000]
            compact["block_count"] = data.get("block_count")
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

    # Optional ASCII flow diagram (monospace code block) — gives readers a
    # quick visual of which agents/tasks contributed to the report.
    flow_mode = state.get("ascii_flow_mode") or os.environ.get(
        "DOC_ASCII_FLOW", "auto"
    )
    dag = state.get("dag", []) or []
    if should_render(dag, flow_mode):
        diagram = render_dag_ascii(dag)
        if diagram:
            blocks.append({"block_type": "heading2", "text": "执行流程图"})
            blocks.append({"block_type": "code", "text": diagram})

    dag_by_id = {t["id"]: t for t in dag}
    # Track the last emitted section heading so that consecutive tasks of the
    # same kind (e.g. multiple ``web.fetch`` legs auto-expanded after one
    # ``web.search``) don't each emit their own ``网页摘要`` heading2 and
    # leave a trail of empty sections between them.
    last_section: str | None = None
    for tid, data in state.get("results", {}).items():
        task = dag_by_id.get(tid, {})
        action = task.get("action", "")
        section_title = _section_label(action, fallback_tid=f"{tid}: {action}")
        if section_title != last_section:
            blocks.append({"block_type": "heading2", "text": section_title})
            last_section = section_title

            # Per-section LLM observation (if matched), else deterministic
            # fallback for tables. Only emit on the first task of a section so
            # repeated fetches don't duplicate the observation block.
            llm_obs = obs_by_label.get(section_title)
            if llm_obs:
                blocks.append({"block_type": "text", "text": llm_obs})
            elif action in ("feishu.bitable.read", "feishu.bitable.read_all"):
                cheap = _bitable_observation(data.get("records") or [])
                if cheap:
                    blocks.append({"block_type": "text", "text": cheap})

        if action == "feishu.bitable.read":
            blocks.extend(_rows_to_table(data.get("records") or []))
        elif action == "feishu.bitable.read_all":
            # Group by ``_table`` and render one sub-section per table so wide
            # bitables stay legible.
            grouped: dict[str, list[dict]] = {}
            for r in data.get("records") or []:
                grouped.setdefault(r.get("_table") or "未命名", []).append(r)
            for tname, rows in grouped.items():
                blocks.append({"block_type": "heading3", "text": tname})
                blocks.extend(_rows_to_table(rows))
        elif action == "feishu.docx.read":
            for b in (data.get("blocks") or [])[:200]:
                blocks.append({"block_type": "text", "text": b.get("text", "")})
        elif action == "feishu.contact.read":
            blocks.extend(_users_to_block(data.get("users") or []))
        elif action == "feishu.calendar.read":
            events = data.get("events") or []
            lines = [f"- {e.get('summary','?')} @ {e.get('start_time','')}" for e in events]
            blocks.append({"block_type": "text", "text": "\n".join(lines) or "(暂无日程)"})
        elif action == "web.search":
            blocks.extend(_search_to_block(data.get("results") or []))
        elif action == "web.fetch":
            url = (data or {}).get("url") or task.get("resource") or ""
            summary = ((data or {}).get("summary") or "").strip()
            err = (data or {}).get("error")
            host = ""
            if url:
                from urllib.parse import urlparse as _urlparse
                try:
                    host = _urlparse(url).hostname or url
                except ValueError:
                    host = url
            if host:
                blocks.append({"block_type": "heading3", "text": host})
            if summary:
                blocks.append({"block_type": "text", "text": summary})
            else:
                msg = "(抓取失败)" if err else "(无内容)"
                blocks.append({"block_type": "text", "text": msg})
        else:
            blocks.append({"block_type": "text", "text": str(data)})

    if recs:
        blocks.append({"block_type": "heading2", "text": "行动建议"})
        rec_text = "\n".join(f"- {r}" for r in recs)
        blocks.append({"block_type": "text", "text": rec_text})

    return {**state, "blocks": blocks, "doc_title": title}
