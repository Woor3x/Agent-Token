"""End-to-end smoke against real Feishu Open Platform.

Flow:
  1. Mint tenant_access_token via FeishuOAuth (env: FEISHU_APP_ID / SECRET).
  2. Read first N rows from the configured bitable.
  3. Render the rows as a compact markdown table and ask the LLM
     (env: LLM_PROVIDER) for an executive summary.
  4. Create a fresh docx in FEISHU_DOCX_FOLDER_TOKEN, then append the
     summary + table as real Feishu Docx blocks.
  5. Print the docx URL — manual visual check.

This script never deletes anything. Run from repo root::

    set -a; source .env; set +a    # load FEISHU_*, ARK_*, LLM_PROVIDER
    python scripts/feishu_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from agents.common.llm import ChatMessage, make_llm  # noqa: E402
from agents.data_agent.feishu.bitable import list_records  # noqa: E402
from agents.data_agent.feishu.oauth import FeishuOAuth  # noqa: E402
from agents.doc_assistant.nodes.doc_writer import _create_and_write  # noqa: E402


def _require_env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        sys.exit(f"missing env: {name}")
    return v


def _records_to_md(records: list[dict], *, max_rows: int = 10) -> str:
    if not records:
        return "(no rows)"
    keys: list[str] = []
    seen: set[str] = set()
    for r in records[:max_rows]:
        for k in (r.get("fields") or {}):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    header = " | ".join(keys)
    sep = " | ".join("---" for _ in keys)
    lines = [header, sep]
    for r in records[:max_rows]:
        f = r.get("fields") or {}
        # Coerce non-scalar field values (User, Formula objects) to short str.
        cells = []
        for k in keys:
            v = f.get(k, "")
            if isinstance(v, list):
                v = ",".join(
                    str(x.get("name") or x.get("text") or x) if isinstance(x, dict) else str(x)
                    for x in v
                )
            cells.append(str(v))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


async def main() -> None:
    base = os.environ.get("FEISHU_BASE", "https://open.feishu.cn").rstrip("/")
    app_id = _require_env("FEISHU_APP_ID")
    _ = _require_env("FEISHU_APP_SECRET")
    bitable_app = _require_env("FEISHU_BITABLE_APP_TOKEN")
    bitable_table = _require_env("FEISHU_BITABLE_TABLE_ID")
    folder_token = os.environ.get("FEISHU_DOCX_FOLDER_TOKEN", "")
    title = f"agent-smoke {time.strftime('%Y-%m-%d %H:%M')}"

    print(f"[smoke] base={base} app={app_id} folder={folder_token or '(root)'}")

    async with httpx.AsyncClient(timeout=15.0) as http:
        oauth = FeishuOAuth(base=base)
        token = await oauth.get_tenant_token(client=http)
        print(f"[smoke] tenant_access_token (truncated): {token[:14]}…")

        records = await list_records(
            base=base, token=token,
            app_token=bitable_app, table_id=bitable_table,
            page_size=10, max_pages=1, client=http,
        )
        print(f"[smoke] read {len(records)} record(s) from {bitable_table}")
        if not records:
            sys.exit("bitable returned 0 records — abort smoke")

        md = _records_to_md(records, max_rows=8)
        llm = make_llm()
        print(f"[smoke] llm provider={llm.name}")
        res = await llm.chat(
            messages=[
                ChatMessage(
                    role="system",
                    content="你是数据助手。基于给定多维表格行，用中文输出 80-150 字执行摘要，"
                            "突出: 进展状态分布 / 重要紧急任务 / 是否有延期。",
                ),
                ChatMessage(role="user", content=md),
            ],
            temperature=0.3,
            max_tokens=400,
        )
        summary = (res.content or "").strip() or "(LLM 返回空)"
        print(f"[smoke] llm summary: {summary[:80]}…")

        blocks = [
            {"block_type": "heading1", "text": title},
            {"block_type": "heading2", "text": "执行摘要"},
            {"block_type": "text", "text": summary},
            {"block_type": "heading2", "text": "原始数据 (前 8 行)"},
            {"block_type": "text", "text": md},
        ]

        out = await _create_and_write(
            base=base, token=token, title=title,
            blocks=blocks, folder_token=folder_token, client=http,
        )

    print(f"[smoke] OK document_id={out['document_id']}")
    print(f"[smoke] URL: {out['url']}")


if __name__ == "__main__":
    asyncio.run(main())
