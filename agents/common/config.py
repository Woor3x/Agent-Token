"""Env-driven config. No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(key: str, default: str | None = None) -> str:
    val = os.environ.get(key, default)
    if val is None:
        raise RuntimeError(f"env {key} required")
    return val


@dataclass(frozen=True)
class AgentConfig:
    agent_id: str
    idp_jwks_url: str
    idp_issuer: str
    gateway_url: str
    verify_dpop: bool
    capability_path: Path
    feishu_base: str
    feishu_mock: bool

    @classmethod
    def load(cls, agent_id: str, capability_path: Path) -> "AgentConfig":
        return cls(
            agent_id=agent_id,
            idp_jwks_url=_env("IDP_JWKS_URL", "http://idp.local:8000/jwks"),
            idp_issuer=_env("IDP_ISSUER", "https://idp.local"),
            gateway_url=_env("GATEWAY_URL", "http://gateway.local:8001"),
            verify_dpop=_env("VERIFY_DPOP", "false").lower() == "true",
            capability_path=capability_path,
            feishu_base=_env("FEISHU_BASE", "http://feishu-mock.local:9000"),
            feishu_mock=_env("FEISHU_MOCK", "true").lower() == "true",
        )
