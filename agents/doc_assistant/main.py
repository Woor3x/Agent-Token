"""DocAssistant FastAPI entry.

In production the peer ASGI apps are not wired in — the orchestrator uses
``HttpSdkClient`` to go through the Gateway. For the single-process demo / tests
we expose ``build_app(peer_apps)`` so the harness can inject data_agent and
web_agent ASGI apps directly (no Gateway in the loop).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.llm import LLMProvider, make_llm
from agents.common.logging import setup_logging
from agents.common.server import AgentServer

from .handler import DocAssistantHandler

setup_logging()

_CAP_PATH = Path(__file__).with_name("capability.yaml")


def build_app(
    peer_apps: dict[str, Any] | None = None,
    *,
    llm: LLMProvider | None = None,
):
    config = AgentConfig.load("doc_assistant", _CAP_PATH)
    cap = load_capability(_CAP_PATH)
    handler = DocAssistantHandler(
        feishu_base=config.feishu_base,
        peer_apps=peer_apps or {},
        llm=llm if llm is not None else make_llm(),
    )
    return AgentServer(config=config, capability=cap, handler=handler).create_app()


app = build_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8100)
