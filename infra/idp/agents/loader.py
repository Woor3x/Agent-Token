import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator


class CapabilityEntry(BaseModel):
    action: str
    resource_pattern: str
    constraints: dict = {}


class DelegationConfig(BaseModel):
    accept_from: list[str] = []
    max_depth: int = 1


class AgentCapability(BaseModel):
    agent_id: str
    display_name: str = ""
    role: str
    capabilities: list[CapabilityEntry] = []
    delegation: DelegationConfig = DelegationConfig()
    underlying_credentials: list[str] = []

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("orchestrator", "executor"):
            raise ValueError(f"role must be orchestrator or executor, got: {v}")
        return v


_capabilities: dict[str, AgentCapability] = {}


def load_capabilities(capabilities_dir: str) -> dict[str, AgentCapability]:
    global _capabilities
    _capabilities = {}
    path = Path(capabilities_dir)
    if not path.exists():
        return _capabilities

    for yaml_file in path.glob("*.yaml"):
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        cap = AgentCapability.model_validate(data)
        _capabilities[cap.agent_id] = cap

    return _capabilities


def get_capabilities() -> dict[str, AgentCapability]:
    return _capabilities


def get_agent_capability(agent_id: str) -> Optional[AgentCapability]:
    return _capabilities.get(agent_id)
