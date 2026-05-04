"""Audit API configuration — all fields settable via AUDIT_* env vars."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8090
    log_level: str = "info"

    # SQLite storage
    db_path: str = "./audit.db"

    # Batch writer
    flush_interval_ms: int = 100   # flush every 100ms
    batch_size: int = 50           # or every 50 events

    # Auth — admin token for read endpoints
    admin_token: str = "change-me-in-production"
    # Comma-separated list of valid service tokens (gateway, idp, anomaly)
    service_tokens: str = ""

    # SSE heartbeat interval (seconds)
    sse_heartbeat_sec: int = 30

    # JSONL backup directory for flush failures
    backup_dir: str = "./backup"

    model_config = {
        "env_prefix": "AUDIT_",
        "case_sensitive": False,
    }

    @property
    def service_token_set(self) -> set[str]:
        return {t.strip() for t in self.service_tokens.split(",") if t.strip()}


settings = Settings()
