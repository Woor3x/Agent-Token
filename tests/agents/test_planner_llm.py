"""Planner node — LLM path with rule-based fallback."""
from __future__ import annotations

import json

import pytest

from agents.common.llm import ChatMessage, MockLLMProvider
from agents.doc_assistant.nodes.planner import planner_node, validate_dag


@pytest.mark.asyncio
async def test_planner_no_llm_uses_rule() -> None:
    out = await planner_node({"user_prompt": "汇总 Q1 销售业绩"})
    dag = out["dag"]
    actions = [t["action"] for t in dag]
    assert "feishu.bitable.read" in actions
    assert dag[-1]["action"] == "feishu.doc.write"


@pytest.mark.asyncio
async def test_planner_llm_path_happy() -> None:
    canned = json.dumps({
        "tasks": [
            {"id": "t1", "agent": "data_agent",
             "action": "feishu.bitable.read",
             "resource": "app_token:bascn_alice/table:tbl_q1",
             "params": {"page_size": 50}, "deps": []},
            {"id": "t2", "agent": "web_agent",
             "action": "web.search", "resource": "*",
             "params": {"query": "zero trust", "max_results": 3}, "deps": []},
            {"id": "t3", "agent": "doc_assistant",
             "action": "feishu.doc.write", "resource": "doc_token:auto",
             "params": {"title": "Q1 报告"}, "deps": ["t1", "t2"]},
        ]
    })
    llm = MockLLMProvider(responses=[canned])
    out = await planner_node({"user_prompt": "Q1 销售 + 行业调研", "llm": llm})
    dag = out["dag"]
    assert [t["id"] for t in dag] == ["t1", "t2", "t3"]
    validate_dag(dag)


@pytest.mark.asyncio
async def test_planner_llm_appends_missing_doc_write() -> None:
    """LLM忘了最后一步 doc.write — planner 自动补上。"""
    canned = json.dumps({
        "tasks": [
            {"id": "t1", "agent": "data_agent",
             "action": "feishu.contact.read",
             "resource": "department:sales",
             "params": {}, "deps": []},
        ]
    })
    llm = MockLLMProvider(responses=[canned])
    out = await planner_node({"user_prompt": "列销售团队", "llm": llm})
    dag = out["dag"]
    assert dag[-1]["action"] == "feishu.doc.write"
    assert dag[-1]["deps"] == ["t1"]
    validate_dag(dag)


@pytest.mark.asyncio
async def test_planner_llm_garbage_falls_back_to_rule() -> None:
    llm = MockLLMProvider(responses=["this is not json at all"])
    out = await planner_node({"user_prompt": "汇总 Q1 销售业绩", "llm": llm})
    dag = out["dag"]
    # rule path triggered → contains bitable.read
    assert any(t["action"] == "feishu.bitable.read" for t in dag)


@pytest.mark.asyncio
async def test_planner_llm_invalid_dag_falls_back() -> None:
    """LLM 输出 schema 错（bad action）→ validate_dag 抛 → fallback。"""
    canned = json.dumps({
        "tasks": [
            {"id": "t1", "agent": "data_agent",
             "action": "totally.unknown.action",
             "resource": "x", "params": {}, "deps": []},
        ]
    })
    llm = MockLLMProvider(responses=[canned])
    out = await planner_node({"user_prompt": "Q1 销售", "llm": llm})
    # falls back to rule, sales keyword hits bitable.read
    assert any(t["action"] == "feishu.bitable.read" for t in out["dag"])


@pytest.mark.asyncio
async def test_planner_strips_code_fence() -> None:
    canned = "```json\n" + json.dumps({
        "tasks": [
            {"id": "t1", "agent": "web_agent", "action": "web.search",
             "resource": "*", "params": {"query": "x"}, "deps": []},
        ]
    }) + "\n```"
    llm = MockLLMProvider(responses=[canned])
    out = await planner_node({"user_prompt": "x", "llm": llm})
    assert out["dag"][0]["action"] == "web.search"
    assert out["dag"][-1]["action"] == "feishu.doc.write"
