"""Synthesizer node behavior with and without an LLM injected into state.

Updated for the structured-output contract: the LLM now emits a JSON object
``{title, summary, observations[], recommendations[]}`` (see
``prompts/synthesizer_system.txt``).  Section headings render as
human-friendly Chinese labels keyed by the action enum, with mechanical
``tid: action`` only used for unknown actions.
"""
from __future__ import annotations

import json

import pytest

from agents.common.llm import ChatMessage, MockLLMProvider
from agents.doc_assistant.nodes.synthesizer import synthesizer_node


def _sample_state(*, with_llm) -> dict:
    return {
        "user_prompt": "汇总 Q1 销售并写报告",
        "dag": [
            {"id": "t1", "agent": "data_agent", "action": "feishu.bitable.read",
             "resource": "app_token:bascn_alice/table:tbl_q1", "deps": []},
            {"id": "t2", "agent": "web_agent", "action": "web.search",
             "resource": "*", "deps": []},
        ],
        "results": {
            "t1": {
                "records": [
                    {"fields": {"region": "North", "amount": 120}},
                    {"fields": {"region": "South", "amount": 80}},
                ],
                "count": 2,
            },
            "t2": {
                "query": "agent token",
                "results": [
                    {"title": "RFC 8693", "url": "https://x.example", "snippet": "token exchange"},
                ],
                "count": 1,
            },
        },
        "llm": with_llm,
    }


@pytest.mark.asyncio
async def test_synthesizer_without_llm_keeps_template_only() -> None:
    out = await synthesizer_node(_sample_state(with_llm=None))
    blocks = out["blocks"]
    headings = [b["text"] for b in blocks if b.get("block_type", "").startswith("heading")]
    # Default Chinese title (no LLM means no derived title).
    assert headings[0] == "执行报告"
    # No 执行摘要 heading without LLM.
    assert "执行摘要" not in headings
    # Human-friendly section labels keyed by action.
    assert "业务数据" in headings
    assert "行业调研" in headings
    # state["doc_title"] should mirror the heading.
    assert out["doc_title"] == "执行报告"


@pytest.mark.asyncio
async def test_synthesizer_with_llm_prepends_summary() -> None:
    captured_msgs: list[list[ChatMessage]] = []

    def rule(msgs: list[ChatMessage]) -> str:
        captured_msgs.append(msgs)
        return json.dumps({
            "title": "Q1 销售复盘",
            "summary": "Q1 销售北区领跑，南区放缓。引用 RFC 8693 做参照。",
            "observations": [
                {"section": "业务数据", "text": "北区 120 / 南区 80，差距 50%。"},
            ],
            "recommendations": ["复制北区打法到南区"],
        }, ensure_ascii=False)

    llm = MockLLMProvider(rule=rule)
    out = await synthesizer_node(_sample_state(with_llm=llm))
    blocks = out["blocks"]

    # LLM-derived title leads the doc.
    assert blocks[0]["text"] == "Q1 销售复盘"
    assert out["doc_title"] == "Q1 销售复盘"

    # 执行摘要 heading + summary text follow immediately.
    assert blocks[1] == {"block_type": "heading2", "text": "执行摘要"}
    assert blocks[2]["block_type"] == "text"
    assert "RFC 8693" in blocks[2]["text"]

    # Per-section observation appears under its human-friendly heading.
    headings_with_idx = [(i, b["text"]) for i, b in enumerate(blocks)
                        if b.get("block_type") == "heading2"]
    biz_idx = next(i for i, t in headings_with_idx if t == "业务数据")
    assert blocks[biz_idx + 1]["text"].startswith("北区 120")

    # 行动建议 section materialized.
    assert any(b["text"] == "行动建议" for b in blocks if b.get("block_type") == "heading2")
    rec_block = next(b for b in blocks if b.get("text", "").startswith("- 复制北区"))
    assert "复制北区打法到南区" in rec_block["text"]

    # System prompt + user digest reach the LLM.
    assert captured_msgs and captured_msgs[0][0].role == "system"
    user_payload = captured_msgs[0][1].content
    assert '"region": "North"' in user_payload
    assert "agent token" in user_payload


@pytest.mark.asyncio
async def test_synthesizer_llm_failure_falls_back_silently() -> None:
    class _Boom(MockLLMProvider):
        async def chat(self, *a, **kw):
            raise RuntimeError("upstream down")

    out = await synthesizer_node(_sample_state(with_llm=_Boom()))
    blocks = out["blocks"]
    headings = [b["text"] for b in blocks if b.get("block_type", "").startswith("heading")]
    # No 执行摘要 inserted (LLM failed), no 行动建议 (no recs).
    assert "执行摘要" not in headings
    assert "行动建议" not in headings
    # Default title still leads.
    assert headings[0] == "执行报告"
    # Tabular section still rendered with human label.
    assert "业务数据" in headings
    # Deterministic bitable observation appears (cheap fallback when LLM is gone).
    bitable_obs_text = next(
        b["text"] for b in blocks
        if b.get("block_type") == "text" and "共 2 行数据" in b.get("text", "")
    )
    assert "amount 合计 200" in bitable_obs_text
