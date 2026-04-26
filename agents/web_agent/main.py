"""WebAgent FastAPI entry."""
from __future__ import annotations

from pathlib import Path

from agents.common.capability import load_capability
from agents.common.config import AgentConfig
from agents.common.logging import setup_logging
from agents.common.server import AgentServer

from .handler import WebAgentHandler

setup_logging()

_CAP_PATH = Path(__file__).with_name("capability.yaml")
_config = AgentConfig.load("web_agent", _CAP_PATH)
_cap = load_capability(_CAP_PATH)
_handler = WebAgentHandler()

app = AgentServer(config=_config, capability=_cap, handler=_handler).create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8102)
