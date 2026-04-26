"""agent_token_sdk — zero-trust Agent Token SDK.

Public entry points:

* :class:`AgentClient` — orchestrator / caller side (Client Assertion → Token
  Exchange → DPoP → Gateway ``/a2a/invoke``).
* :class:`AgentServer` — callee side FastAPI helper (thin wrapper over
  ``agents.common.server.AgentServer``).
* :class:`AssertionSigner`, :class:`DPoPSigner` — low-level signers.
* Exceptions: :class:`A2AError`, :class:`TokenExchangeError`,
  :class:`AssertionSignError`, :class:`DPoPSignError`.
"""
from __future__ import annotations

from .assertion import AssertionSigner
from .client import AgentClient
from .dpop import DPoPSigner
from .errors import (
    A2AError,
    AssertionSignError,
    DPoPSignError,
    TokenExchangeError,
    is_retryable,
)
from .server import AgentServer

__all__ = [
    "AgentClient",
    "AgentServer",
    "AssertionSigner",
    "DPoPSigner",
    "A2AError",
    "TokenExchangeError",
    "AssertionSignError",
    "DPoPSignError",
    "is_retryable",
]
