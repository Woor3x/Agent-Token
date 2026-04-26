"""Agent registry — YAML hot-reload, agent_id → upstream config."""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    upstream: str
    transport: str = "http"
    timeout_ms: int = 30_000
    retry: dict = field(default_factory=lambda: {"max": 0})
    mtls: dict = field(default_factory=dict)


class Registry:
    def __init__(self, path: str = settings.registry_path) -> None:
        self._path = Path(path)
        self._agents: dict[str, AgentConfig] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        async with self._lock:
            self._load_sync()

    def _load_sync(self) -> None:
        if not self._path.exists():
            logger.warning("registry file not found: %s", self._path)
            return
        with self._path.open() as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        agents_raw = data.get("agents", {})
        new_agents: dict[str, AgentConfig] = {}
        for agent_id, cfg in agents_raw.items():
            new_agents[agent_id] = AgentConfig(
                upstream=cfg["upstream"],
                transport=cfg.get("transport", "http"),
                timeout_ms=cfg.get("timeout_ms", 30_000),
                retry=cfg.get("retry", {"max": 0}),
                mtls=cfg.get("mtls", {}),
            )
        self._agents = new_agents
        logger.info("registry loaded: %d agents", len(self._agents))

    async def reload(self) -> int:
        await self.load()
        return len(self._agents)

    def get(self, agent_id: str) -> AgentConfig:
        cfg = self._agents.get(agent_id)
        if cfg is None:
            from errors import UpstreamError
            raise UpstreamError("UPSTREAM_FAIL", f"unknown agent: {agent_id}")
        return cfg

    def all(self) -> dict[str, AgentConfig]:
        return dict(self._agents)


registry = Registry()
