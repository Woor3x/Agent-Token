"""Synthesizer node behavior with and without an LLM injected into state."""
from __future__ import annotations

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
    assert headings[0] == "Auto Report"
    # No "执行摘要" because no LLM
    assert "执行摘要" not in headings


@pytest.mark.asyncio
async def test_synthesizer_with_llm_prepends_summary() -> None:
    captured_msgs: list[list[ChatMessage]] = []

    def rule(msgs: list[ChatMessage]) -> str:
        captured_msgs.append(msgs)
        return "Q1 销售北区领跑，南区放缓；行业调研引用 RFC 8693。"

    llm = MockLLMProvider(rule=rule)
    out = await synthesizer_node(_sample_state(with_llm=llm))
    blocks = out["blocks"]

    # The summary heading appears directly after Auto Report.
    assert blocks[0]["text"] == "Auto Report"
    assert blocks[1] == {"block_type": "heading2", "text": "执行摘要"}
    assert blocks[2]["block_type"] == "text"
    assert "RFC 8693" in blocks[2]["text"]

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
    # No 执行摘要 inserted, but tabular section still rendered.
    assert "执行摘要" not in headings
    assert any("t1: feishu.bitable.read" == h for h in headings)
