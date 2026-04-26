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
from agents.doc_assistant.nodes.dispatcher import _topo_layers
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
