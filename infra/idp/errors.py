from typing import Optional


class IdPError(Exception):
    http_status: int = 500
    code: str = "server_error"
    message: str = "Internal server error"

    def __init__(self, message: Optional[str] = None, detail: Optional[str] = None):
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)


class InvalidRequest(IdPError):
    http_status = 400
    code = "invalid_request"
    message = "Invalid request"


class InvalidClient(IdPError):
    http_status = 401
    code = "invalid_client"
    message = "Invalid client"


class InvalidGrant(IdPError):
    http_status = 400
    code = "invalid_grant"
    message = "Invalid grant"


class AssertionReplay(IdPError):
    http_status = 400
    code = "assertion_replay"
    message = "Assertion JTI already used"


class AssertionTooLong(IdPError):
    http_status = 400
    code = "assertion_too_long"
    message = "Assertion lifetime exceeds maximum"


class SubIssMismatch(IdPError):
    http_status = 400
    code = "sub_iss_mismatch"
    message = "Assertion sub must equal iss"


class DpopInvalid(IdPError):
    http_status = 400
    code = "dpop_invalid"
    message = "DPoP proof validation failed"


class DelegationNotAllowed(IdPError):
    http_status = 403
    code = "delegation_not_allowed"
    message = "Delegation not permitted for this agent pair"


class ExecutorMismatch(IdPError):
    http_status = 403
    code = "executor_mismatch"
    message = "Requesting agent is not the designated executor for this action"


class EmptyEffectiveScope(IdPError):
    http_status = 403
    code = "empty_effective_scope"
    message = "Effective scope is empty after intersection"


class ContextDenied(IdPError):
    http_status = 403
    code = "context_denied"
    message = "Request denied by context policy"


class AgentRevoked(IdPError):
    http_status = 403
    code = "agent_revoked"
    message = "Agent has been revoked"


class TokenRevoked(IdPError):
    http_status = 403
    code = "token_revoked"
    message = "Token has been revoked"


class RateLimited(IdPError):
    http_status = 429
    code = "rate_limited"
    message = "Rate limit exceeded"


class ServerError(IdPError):
    http_status = 500
    code = "server_error"
    message = "Internal server error"


class OpaUnavailable(IdPError):
    http_status = 503
    code = "opa_unavailable"
    message = "Policy engine unavailable"


class Unauthorized(IdPError):
    http_status = 401
    code = "unauthorized"
    message = "Authentication required"


class NotFound(IdPError):
    http_status = 404
    code = "not_found"
    message = "Resource not found"
