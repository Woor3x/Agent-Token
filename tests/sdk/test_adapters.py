"""LangGraph / LangChain / AutoGen adapter smoke tests.

We don't actually require any of those upstream packages; the adapters degrade
to plain callables / stub bases. The key thing is they wire ``AgentClient.invoke``
correctly given a context dict.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from agent_token_sdk import AgentClient
from agent_token_sdk.adapters.autogen import A2AAgent
from agent_token_sdk.adapters.langchain import make_a2a_tool
from agent_token_sdk.adapters.langgraph import make_a2a_node

from .helpers import build_mock_gateway, build_mock_idp, build_sdk_http, mint_user_token


def _echo_app() -> FastAPI:
    app = FastAPI()

    @app.post("/invoke")
    async def invoke(body: dict) -> dict:
        return {"status": "ok", "data": {"echo": body.get("intent")}, "trace_id": "t-x"}

    return app


async def _make_client() -> AgentClient:
    idp = build_mock_idp()
    gw = build_mock_gateway({"data_agent": _echo_app()})
    http = build_sdk_http(idp, gw)
    return AgentClient(
        agent_id="doc_assistant",
        idp_url="https://idp.mock",
        gateway_url="https://gateway.mock",
        kid="k-1",
        mock_secret="mock-secret",
        http=http,
    )


@pytest.mark.asyncio
async def test_langgraph_node_invokes_agent() -> None:
    client = await _make_client()
    async with client as c:
        node = make_a2a_node(c, "data_agent")
        out = await node({
            "intent": {"action": "feishu.bitable.read", "resource": "app_token:x/table:y"},
            "user_token": mint_user_token(),
            "purpose": "p",
            "plan_id": "plan-1",
            "task_id": "t1",
            "trace_id": "trace-1",
        })
    assert out["a2a_result"]["echo"]["action"] == "feishu.bitable.read"
    await c._http.aclose()


@pytest.mark.asyncio
async def test_langchain_tool_returns_json_string() -> None:
    client = await _make_client()
    user_tok = mint_user_token()

    def ctx() -> dict:
        return {"on_behalf_of": user_tok, "purpose": "p"}

    async with client as c:
        tool = make_a2a_tool(
            c, target="data_agent", description="call data agent", ctx_provider=ctx
        )
        assert tool.name == "call_data_agent"
        # When langchain_core is installed `tool` is a StructuredTool whose
        # `.coroutine` runs the underlying coroutine; otherwise it's the raw
        # async fn. Handle both.
        if hasattr(tool, "ainvoke"):
            out = await tool.ainvoke({
                "action": "feishu.bitable.read",
                "resource": "app_token:x/table:y",
                "params": {},
            })
        else:
            out = await tool(
                "feishu.bitable.read", "app_token:x/table:y", {}
            )
    decoded = json.loads(out)
    assert decoded["echo"]["action"] == "feishu.bitable.read"
    await c._http.aclose()


@pytest.mark.asyncio
async def test_autogen_a2a_agent_invokes() -> None:
    client = await _make_client()
    user_tok = mint_user_token()
    async with client as c:
        agent = A2AAgent(
            agent_id="caller",
            target="data_agent",
            client=c,
            ctx_provider=lambda: {"on_behalf_of": user_tok},
        )
        out = await agent.a2a_invoke(
            intent={"action": "feishu.bitable.read", "resource": "app_token:x/table:y"},
            purpose="p",
        )
    assert out["data"]["echo"]["resource"] == "app_token:x/table:y"
    await c._http.aclose()
