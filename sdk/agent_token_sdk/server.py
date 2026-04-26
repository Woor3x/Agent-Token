"""AgentServer — thin wrapper around ``agents.common.server.AgentServer``.

The SDK exposes a user-facing ``AgentServer`` that takes just
``(agent_id, idp_jwks_url, handler, capability_path)`` to avoid leaking internal
``AgentConfig`` plumbing. It reuses the canonical implementation in
``agents.common.server`` so there is a single source of truth for ``/invoke``
semantics (token re-verification, deny reasons, trace context, JSON envelope).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from agents.common.capability import Capability, load_capability
from agents.common.config import AgentConfig
from agents.common.server import AgentServer as _CoreAgentServer

Handler = Callable[[dict[str, Any], Any, Capability], Awaitable[dict[str, Any]]]


class AgentServer:
    """User-facing callee-side helper.

    Parameters
    ----------
    agent_id:
        Identifier exposed as ``aud=agent:<agent_id>`` on incoming tokens.
    capability_path:
        Path to this agent's ``capability.yaml`` (see 方案-Agents §3).
    handler:
        ``async def handler(body, claims, capability) -> dict`` executing the
        business intent. Raising ``PermissionError`` → 403, ``ValueError`` → 400.
    idp_jwks_url / idp_issuer:
        Optional overrides; defaults come from the process environment.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        capability_path: str | Path,
        handler: Handler,
        idp_jwks_url: str | None = None,
        idp_issuer: str | None = None,
    ) -> None:
        cap_path = Path(capability_path)
        self._cap = load_capability(cap_path)
        self._config = AgentConfig.load(agent_id, cap_path)
        if idp_jwks_url:
            object.__setattr__(self._config, "idp_jwks_url", idp_jwks_url)
        if idp_issuer:
            object.__setattr__(self._config, "idp_issuer", idp_issuer)
        self._core = _CoreAgentServer(
            config=self._config, capability=self._cap, handler=handler
        )

    def create_app(self) -> FastAPI:
        return self._core.create_app()

    @property
    def capability(self) -> Capability:
        return self._cap

    @property
    def config(self) -> AgentConfig:
        return self._config
