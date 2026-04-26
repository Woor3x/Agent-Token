from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GW_", env_file=".env", extra="ignore")

    # Service
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"

    # IdP
    idp_jwks_url: str = "https://idp.local:8443/jwks"
    idp_issuer: str = "https://idp.local"
    jwks_cache_ttl: int = 600          # seconds

    # OPA
    opa_url: str = "http://opa.local:8181/v1/data/agent/authz"
    opa_timeout_ms: int = 5

    # Redis
    redis_url: str = "redis://redis.local:6379/0"
    redis_password: str = ""

    # Delegation
    delegation_max_depth: int = 4

    # DPoP
    dpop_max_iat_skew: int = 60        # seconds
    dpop_jti_ttl: int = 120            # seconds

    # Rate limit (token bucket defaults)
    rate_limit_capacity: int = 100
    rate_limit_refill_rate: float = 10.0   # tokens/sec

    # Circuit breaker
    cb_failure_threshold: int = 5
    cb_open_duration: int = 30         # seconds
    cb_half_open_probes: int = 1

    # mTLS
    mtls_cert: str = "/certs/gw.crt"
    mtls_key: str = "/certs/gw.key"
    mtls_ca: str = "/certs/ca.crt"
    mtls_enabled: bool = False         # disable in dev

    # Audit
    audit_db_path: str = "audit.db"
    audit_flush_interval_ms: int = 100
    audit_flush_batch_size: int = 50

    # Admin
    admin_token: str = Field(default="changeme", min_length=8)

    # NL parser (Anthropic)
    anthropic_api_key: str = ""
    nl_model: str = "claude-haiku-4-5-20251001"

    # Body size limit
    max_body_size: int = 256 * 1024    # 256 KB

    # Registry
    registry_path: str = "registry.yaml"

    # Policy version (informational)
    policy_version: str = "v1.0.0"


settings = Settings()
