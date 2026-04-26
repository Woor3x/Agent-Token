"""Load and validate capability.yaml per 方案-细化 §3.1."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CapabilityItem:
    action: str
    resource_pattern: str
    constraints: dict = field(default_factory=dict)

    def matches(self, action: str, resource: str) -> bool:
        if self.action != action:
            return False
        return fnmatch.fnmatch(resource, self.resource_pattern)


@dataclass(frozen=True)
class Delegation:
    accept_from: list[str]
    max_depth: int


@dataclass(frozen=True)
class Capability:
    agent_id: str
    role: str
    public_key_kid: str
    capabilities: list[CapabilityItem]
    delegation: Delegation
    underlying_credentials: list[str]

    def find(self, action: str, resource: str) -> CapabilityItem | None:
        for cap in self.capabilities:
            if cap.matches(action, resource):
                return cap
        return None

    def actions(self) -> set[str]:
        return {c.action for c in self.capabilities}


def load_capability(path: Path) -> Capability:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"capability yaml malformed: {path}")
    caps = [
        CapabilityItem(
            action=c["action"],
            resource_pattern=c["resource_pattern"],
            constraints=c.get("constraints") or {},
        )
        for c in data.get("capabilities", [])
    ]
    deleg = data.get("delegation") or {}
    return Capability(
        agent_id=data["agent_id"],
        role=data["role"],
        public_key_kid=(data.get("public_key_jwk") or {}).get("kid", ""),
        capabilities=caps,
        delegation=Delegation(
            accept_from=list(deleg.get("accept_from", [])),
            max_depth=int(deleg.get("max_depth", 1)),
        ),
        underlying_credentials=list(data.get("underlying_credentials", [])),
    )
