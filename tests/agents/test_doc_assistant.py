"""DocAssistant orchestrator unit + integration tests.

The integration test wires:
  DocAssistant FastAPI ← AsgiSdkClient → DataAgent FastAPI (in-process)
                                       → WebAgent  FastAPI (in-process)
                                       → Feishu Mock (in-process, via httpx swap)
All mocked, all asyncio, no real network.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.server import AgentServer, sign_mock_token
from agents.data_agent.feishu.oauth import FeishuOAuth
from agents.data_agent.handler import DataAgentHandler
from agents.doc_assistant.handler import DocAssistantHandler
from agents.doc_assistant.nodes.dispatcher import _topo_layers, dispatcher_node
from agents.doc_assistant.nodes.planner import _rule_plan, validate_dag
from agents.doc_assistant.nodes.synthesizer import synthesizer_node
from agents.web_agent.handler import WebAgentHandler
from services.feishu_mock.main import app as feishu_mock_app


# ---------------------- Unit: planner + dispatcher helpers ---------------------


def test_planner_rule_plan_sales() -> None:
    dag = _rule_plan("Summarize Q1 sales of the sales team")
    validate_dag(dag)
    agents = [t["agent"] for t in dag]
    # Must include data_agent read + doc_assistant writer
    assert "data_agent" in agents
    assert dag[-1]["action"] == "feishu.doc.write"


def test_planner_rule_plan_fallback_to_search() -> None:
    dag = _rule_plan("Give me some background info")
    validate_dag(dag)
    assert dag[0]["agent"] == "web_agent"


def test_validate_dag_rejects_bad_action() -> None:
    with pytest.raises(ValueError):
        validate_dag([{"id": "t1", "agent": "data_agent", "action": "evil", "resource": "x"}])


def test_topo_layers_linear() -> None:
    dag = [
        {"id": "a", "deps": []},
        {"id": "b", "deps": ["a"]},
        {"id": "c", "deps": ["b"]},
    ]
    layers = _topo_layers(dag)
    assert [[t["id"] for t in layer] for layer in layers] == [["a"], ["b"], ["c"]]


def test_topo_layers_cycle_detected() -> None:
    with pytest.raises(ValueError):
        _topo_layers([
            {"id": "a", "deps": ["b"]},
            {"id": "b", "deps": ["a"]},
        ])


class _StubSDK:
    """Minimal SDK stub for dispatcher tests — records calls, returns canned outs."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []

    async def invoke(self, *, target_agent, intent, trace_id, plan_id, task_id):
        self.calls.append({
            "agent": target_agent, "intent": intent, "task_id": task_id,
        })
        return self.responses.get(task_id, {"data": {}})


@pytest.mark.asyncio
async def test_dispatcher_auto_fetch_expansion() -> None:
    """web.search with fetch_top_k=2 → dispatcher appends 2 web.fetch tasks
    and patches doc.write deps to wait for them."""
    sdk = _StubSDK(
        responses={
            "t1": {"data": {
                "results": [
                    {"title": "A", "url": "https://a.example.com/x", "snippet": "sa"},
                    {"title": "B", "url": "https://b.example.com/y", "snippet": "sb"},
                    {"title": "C", "url": "https://c.example.com/z", "snippet": "sc"},
                ],
                "count": 3,
            }},
            # Fetch tasks get auto-ids t3, t4 (since dag len=2 → seed=3)
            "t3": {"data": {"url": "https://a.example.com/x", "summary": "sumA", "length": 100}},
            "t4": {"data": {"url": "https://b.example.com/y", "summary": "sumB", "length": 100}},
        }
    )
    state = {
        "sdk": sdk,
        "trace_id": "tr-1", "plan_id": "pl-1",
        "dag": [
            {"id": "t1", "agent": "web_agent", "action": "web.search",
             "resource": "*", "params": {"query": "xss", "fetch_top_k": 2}, "deps": []},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
             "resource": "doc_token:auto", "params": {"title": "Auto"}, "deps": ["t1"]},
        ],
    }
    out = await dispatcher_node(state)

    # 1) DAG mutated: 2 new fetch tasks appended.
    actions = [t["action"] for t in out["dag"]]
    assert actions.count("web.fetch") == 2
    fetch_ids = [t["id"] for t in out["dag"] if t["action"] == "web.fetch"]
    assert fetch_ids == ["t3", "t4"]

    # 2) doc.write deps patched to include both fetch ids.
    doc_task = next(t for t in out["dag"] if t["action"] == "feishu.doc.write")
    assert set(doc_task["deps"]) == {"t1", "t3", "t4"}

    # 3) SDK invoked for search + 2 fetches (doc.write skipped here, doc_writer_node handles).
    invoked_actions = [c["intent"]["action"] for c in sdk.calls]
    # search first, then 2 fetches in any order (asyncio.gather scheduling).
    assert invoked_actions[0] == "web.search"
    assert sorted(invoked_actions[1:]) == ["web.fetch", "web.fetch"]
    fetched_urls = {c["intent"]["resource"] for c in sdk.calls if c["intent"]["action"] == "web.fetch"}
    assert fetched_urls == {"https://a.example.com/x", "https://b.example.com/y"}

    # 4) Results captured for all dispatched tasks.
    assert set(out["results"]) == {"t1", "t3", "t4"}


@pytest.mark.asyncio
async def test_dispatcher_skips_expansion_when_top_k_zero() -> None:
    """Per-task ``fetch_top_k=0`` opt-out overrides the global default."""
    sdk = _StubSDK(responses={"t1": {"data": {"results": [
        {"title": "A", "url": "https://a.example.com/x", "snippet": "sa"},
    ]}}})
    state = {
        "sdk": sdk,
        "trace_id": "tr", "plan_id": "pl",
        "dag": [
            {"id": "t1", "agent": "web_agent", "action": "web.search",
             "resource": "*", "params": {"query": "x", "fetch_top_k": 0}, "deps": []},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
             "resource": "doc_token:auto", "params": {}, "deps": ["t1"]},
        ],
    }
    out = await dispatcher_node(state)
    assert all(t["action"] != "web.fetch" for t in out["dag"])
    assert [c["intent"]["action"] for c in sdk.calls] == ["web.search"]


@pytest.mark.asyncio
async def test_dispatcher_default_top_k_applies_when_task_unset(monkeypatch) -> None:
    """When a search task omits fetch_top_k, the global default kicks in."""
    monkeypatch.setenv("WEB_AUTO_FETCH_TOP_K", "2")
    sdk = _StubSDK(
        responses={
            "t1": {"data": {"results": [
                {"title": "A", "url": "https://a.example.com/x", "snippet": "sa"},
                {"title": "B", "url": "https://b.example.com/y", "snippet": "sb"},
                {"title": "C", "url": "https://c.example.com/z", "snippet": "sc"},
            ]}},
            "t3": {"data": {"url": "https://a.example.com/x", "summary": "sA", "length": 1}},
            "t4": {"data": {"url": "https://b.example.com/y", "summary": "sB", "length": 1}},
        }
    )
    state = {
        "sdk": sdk,
        "trace_id": "tr", "plan_id": "pl",
        "dag": [
            {"id": "t1", "agent": "web_agent", "action": "web.search",
             "resource": "*", "params": {"query": "x"}, "deps": []},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
             "resource": "doc_token:auto", "params": {}, "deps": ["t1"]},
        ],
    }
    out = await dispatcher_node(state)
    assert sum(1 for t in out["dag"] if t["action"] == "web.fetch") == 2


@pytest.mark.asyncio
async def test_dispatcher_drops_unsafe_fetch_urls() -> None:
    """Non-https or hostless URLs from search results must not get fetched."""
    sdk = _StubSDK(responses={"t1": {"data": {"results": [
        {"title": "bad", "url": "http://insecure.example.com/", "snippet": "x"},
        {"title": "ok",  "url": "https://ok.example.com/",      "snippet": "y"},
        {"title": "junk", "url": "ftp://nope/",                 "snippet": "z"},
    ]}}})
    state = {
        "sdk": sdk,
        "trace_id": "tr", "plan_id": "pl",
        "dag": [
            {"id": "t1", "agent": "web_agent", "action": "web.search",
             "resource": "*", "params": {"query": "x", "fetch_top_k": 3}, "deps": []},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
             "resource": "doc_token:auto", "params": {}, "deps": ["t1"]},
        ],
    }
    out = await dispatcher_node(state)
    fetched = [t["resource"] for t in out["dag"] if t["action"] == "web.fetch"]
    assert fetched == ["https://ok.example.com/"]


def test_ascii_flow_render_layered() -> None:
    from agents.doc_assistant.nodes._ascii_flow import render_dag_ascii

    dag = [
        {"id": "t1", "agent": "web_agent", "action": "web.search",
         "resource": "*", "params": {"query": "xss", "fetch_top_k": 2}, "deps": []},
        {"id": "t3", "agent": "web_agent", "action": "web.fetch",
         "resource": "https://owasp.org/x", "params": {}, "deps": ["t1"]},
        {"id": "t4", "agent": "web_agent", "action": "web.fetch",
         "resource": "https://acunetix.com/y", "params": {}, "deps": ["t1"]},
        {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
         "resource": "doc_token:auto", "params": {}, "deps": ["t1", "t3", "t4"]},
    ]
    out = render_dag_ascii(dag)
    assert "执行流程" in out
    assert "[t1] web_agent.web.search" in out
    assert 'query="xss"' in out
    # Layer 2 has 2 fetch tasks → must be rendered as fan-out branches.
    assert out.count("├─▶") >= 2
    assert "owasp.org" in out and "acunetix.com" in out
    assert "[t2] doc_assistant.feishu.doc.write" in out


def test_ascii_flow_should_render_modes() -> None:
    from agents.doc_assistant.nodes._ascii_flow import should_render

    one_task = [
        {"id": "t1", "agent": "web_agent", "action": "web.search", "deps": []},
        {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write", "deps": ["t1"]},
    ]
    two_tasks = one_task + [
        {"id": "t3", "agent": "web_agent", "action": "web.fetch", "deps": ["t1"]},
    ]
    # auto: needs ≥2 non-writer tasks
    assert should_render(one_task, "auto") is False
    assert should_render(two_tasks, "auto") is True
    # always: render whenever DAG non-empty
    assert should_render(one_task, "always") is True
    # off: never
    assert should_render(two_tasks, "off") is False


@pytest.mark.asyncio
async def test_synthesizer_inserts_ascii_flow_block(monkeypatch) -> None:
    monkeypatch.setenv("DOC_ASCII_FLOW", "always")
    state = {
        "dag": [
            {"id": "t1", "agent": "data_agent", "action": "feishu.bitable.read",
             "resource": "app_token:bascn/table:tbl1", "deps": []},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write",
             "resource": "doc_token:auto", "deps": ["t1"]},
        ],
        "results": {
            "t1": {"records": [{"fields": {"region": "N", "sales": 100}}], "count": 1},
        },
    }
    out = await synthesizer_node(state)
    blocks = out["blocks"]
    code_blocks = [b for b in blocks if b.get("block_type") == "code"]
    assert len(code_blocks) == 1
    assert "执行流程" in code_blocks[0]["text"]
    assert "[t1] data_agent.feishu.bitable.read" in code_blocks[0]["text"]
    # Heading present too
    assert any(b.get("text") == "执行流程图" for b in blocks)


@pytest.mark.asyncio
async def test_synthesizer_skips_ascii_flow_when_off(monkeypatch) -> None:
    monkeypatch.setenv("DOC_ASCII_FLOW", "off")
    state = {
        "dag": [
            {"id": "t1", "agent": "web_agent", "action": "web.search", "resource": "*", "deps": []},
            {"id": "t3", "agent": "web_agent", "action": "web.fetch", "resource": "https://x.example.com/", "deps": ["t1"]},
            {"id": "t2", "agent": "doc_assistant", "action": "feishu.doc.write", "resource": "doc_token:auto", "deps": ["t1", "t3"]},
        ],
        "results": {
            "t1": {"results": [], "count": 0, "query": "x"},
            "t3": {"url": "https://x.example.com/", "summary": "ok", "length": 1},
        },
    }
    out = await synthesizer_node(state)
    assert all(b.get("block_type") != "code" for b in out["blocks"])


def test_feishu_blocks_translates_code_block() -> None:
    from agents.doc_assistant.nodes._feishu_blocks import to_feishu_children

    out = to_feishu_children([
        {"block_type": "heading1", "text": "T"},
        {"block_type": "code", "text": "[t1] x\n[t2] y"},
    ])
    assert out[0]["block_type"] == 3  # H1
    assert out[1]["block_type"] == 14  # CODE
    assert out[1]["code"]["style"]["language"] == 1  # PLAIN_TEXT
    assert out[1]["code"]["elements"][0]["text_run"]["content"] == "[t1] x\n[t2] y"


@pytest.mark.asyncio
async def test_synthesizer_emits_blocks() -> None:
    state = {
        "dag": [
            {"id": "t1", "agent": "data_agent", "action": "feishu.bitable.read",
             "resource": "app_token:bascn_alice/table:tbl_q1"},
        ],
        "results": {
            "t1": {"records": [{"fields": {"region": "North", "sales": 100}}], "count": 1},
        },
    }
    out = await synthesizer_node(state)
    blocks = out["blocks"]
    assert blocks[0]["block_type"] == "heading1"
    assert any("region" in b.get("text", "") for b in blocks)


# ---------------------- Integration: full orchestrate ---------------------


def _feishu_factory():
    transport = httpx.ASGITransport(app=feishu_mock_app)
    return lambda: httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _build_data_agent_app():
    cap_path = Path(__file__).resolve().parents[2] / "agents" / "data_agent" / "capability.yaml"
    cfg = AgentConfig.load("data_agent", cap_path)
    cap = load_capability(cap_path)
    handler = DataAgentHandler(
        feishu_base="http://testserver",
        oauth=FeishuOAuth(base="http://testserver"),
        client_factory=_feishu_factory(),
    )
    return AgentServer(config=cfg, capability=cap, handler=handler).create_app()


def _build_web_agent_app():
    cap_path = Path(__file__).resolve().parents[2] / "agents" / "web_agent" / "capability.yaml"
    cfg = AgentConfig.load("web_agent", cap_path)
    cap = load_capability(cap_path)
    return AgentServer(config=cfg, capability=cap, handler=WebAgentHandler()).create_app()


def _build_doc_assistant_app(peer_apps):
    cap_path = Path(__file__).resolve().parents[2] / "agents" / "doc_assistant" / "capability.yaml"
    cfg = AgentConfig.load("doc_assistant", cap_path)
    cap = load_capability(cap_path)
    handler = DocAssistantHandler(
        feishu_base="http://testserver",
        peer_apps=peer_apps,
        client_factory=_feishu_factory(),
    )
    return AgentServer(config=cfg, capability=cap, handler=handler).create_app()


@pytest.mark.asyncio
async def test_orchestrate_end_to_end() -> None:
    data_app = _build_data_agent_app()
    web_app = _build_web_agent_app()
    doc_app = _build_doc_assistant_app({"data_agent": data_app, "web_agent": web_app})

    tok = sign_mock_token(
        sub="user:alice",
        actor_sub=None,  # user delegates directly
        aud="agent:doc_assistant",
        scope=["orchestrate:plan:*", "feishu.doc.write:*", "a2a.invoke:*"],
    )
    transport = httpx.ASGITransport(app=doc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={
                "intent": {
                    "action": "orchestrate",
                    "resource": "plan:auto",
                    "params": {"prompt": "Summarize Q1 sales from the sales team"},
                }
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["doc"]["document_id"].startswith("doc_")
    # results should hold entries for all non-writer tasks
    non_writer = [t for t in body["dag"] if t["action"] != "feishu.doc.write"]
    assert set(body["results"].keys()) == {t["id"] for t in non_writer}
    # data_agent returned real mock rows
    data_tid = next(t["id"] for t in non_writer if t["agent"] == "data_agent")
    assert body["results"][data_tid]["count"] == 4


@pytest.mark.asyncio
async def test_orchestrate_denied_on_missing_scope() -> None:
    doc_app = _build_doc_assistant_app({})

    tok = sign_mock_token(
        sub="user:alice", actor_sub=None,
        aud="agent:doc_assistant",
        scope=["feishu.doc.write:*"],  # no orchestrate scope
    )
    transport = httpx.ASGITransport(app=doc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/invoke",
            headers={"Authorization": f"DPoP {tok}"},
            json={
                "intent": {
                    "action": "orchestrate",
                    "resource": "plan:auto",
                    "params": {"prompt": "sales"},
                }
            },
        )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "AUTHZ_SCOPE_EXCEEDED"
