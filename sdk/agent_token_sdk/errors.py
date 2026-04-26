"""SDK exception hierarchy + retry classifier (see 方案-SDK §9)."""
from __future__ import annotations


class SDKError(Exception):
    """Base class for all SDK-raised exceptions."""


class AssertionSignError(SDKError):
    pass


class DPoPSignError(SDKError):
    pass


class TokenExchangeError(SDKError):
    def __init__(self, status_code: int, message: str, *, body: str | None = None) -> None:
        self.status_code = status_code
        self.message = message
        self.body = body
        super().__init__(f"token_exchange {status_code}: {message}")


class A2AError(SDKError):
    def __init__(self, code: str, message: str, *, trace_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.trace_id = trace_id
        super().__init__(f"[{code}] {message} (trace={trace_id})")


# ---- retry classification ---------------------------------------------------

_NO_RETRY: set[str] = {
    "AUTHN_TOKEN_INVALID",
    "AUTHN_REVOKED",
    "AGENT_REVOKED",
    "TOKEN_REPLAYED",
    "AUTHZ_SCOPE_EXCEEDED",
    "AUTHZ_CAPABILITY_MISSING",
    "AUTHZ_DELEGATION_DEPTH_EXCEEDED",
    "AUTHZ_AUDIENCE_MISMATCH",
    "AUTHZ_POLICY_DENIED",
    "INTENT_INVALID",
    "AGENT_FORBIDDEN",
}

_RETRYABLE: set[str] = {
    "RATE_LIMITED",
    "CIRCUIT_OPEN",
    "UPSTREAM_TIMEOUT",
    "AGENT_INTERNAL_ERROR",
}


def is_retryable(code: str) -> bool:
    """Return True if callers should retry (with their own backoff)."""
    if code in _NO_RETRY:
        return False
    return code in _RETRYABLE
