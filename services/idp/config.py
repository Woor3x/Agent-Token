import re
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    idp_issuer: str = "https://idp.local"
    idp_kms_passphrase: str = "dev-passphrase-change-in-prod"
    redis_url: str = "redis://localhost:6379"
    sqlite_path: str = "/data/idp.db"
    opa_url: str = "http://localhost:8181"
    admin_token: str = "admin-secret-token"
    policy_version: str = "v1.2.0"
    capabilities_dir: str = "/app/capabilities"
    users_dir: str = "/app/users"
    allowed_redirect_uris: str = "http://localhost:3000/callback,https://web-ui.local/callback"
    allowed_source_nets: str = "0.0.0.0/0"
    kms_keys_dir: str = "/app/kms/keys"

    @property
    def redirect_uris_list(self) -> list[str]:
        return [u.strip() for u in self.allowed_redirect_uris.split(",") if u.strip()]

    @property
    def source_nets_list(self) -> list[str]:
        return [n.strip() for n in self.allowed_source_nets.split(",") if n.strip()]

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()

ACTION_ENUM: set[str] = {
    "feishu.bitable.read",
    "feishu.contact.read",
    "feishu.calendar.read",
    "feishu.doc.write",
    "web.search",
    "web.fetch",
    "a2a.invoke",
}

RESOURCE_REGEX: dict[str, re.Pattern] = {
    "feishu.bitable.read": re.compile(r"^app_token:[^/]+/table:[^/]+$|^app_token:\*/table:\*$|^app_token:[^/]*/table:\*$"),
    "feishu.contact.read": re.compile(r"^department:.+$|^department:\*$"),
    "feishu.calendar.read": re.compile(r"^calendar:.+$|^calendar:\*$"),
    "feishu.doc.write": re.compile(r"^doc_token:.+$|^doc_token:\*$"),
    "web.search": re.compile(r"^\*$|^https?://.+"),
    "web.fetch": re.compile(r"^https://.+|^https://\*$"),
    "a2a.invoke": re.compile(r"^agent:[a-z_]+$"),
}

EXECUTOR_MAP: dict[str, str] = {
    "feishu.bitable.read": "data_agent",
    "feishu.contact.read": "data_agent",
    "feishu.calendar.read": "data_agent",
    "feishu.doc.write": "doc_assistant",
    "web.search": "web_agent",
    "web.fetch": "web_agent",
}

WRITE_ACTIONS: set[str] = {"feishu.doc.write", "feishu.bitable.write"}

TOKEN_EXCHANGE_TTL_SEC = 120
AUTH_CODE_TTL_SEC = 300
REFRESH_TOKEN_TTL_SEC = 86400
ASSERTION_JTI_TTL_SEC = 120
DPOP_JTI_TTL_SEC = 120
KEY_ROTATION_GRACE_SEC = 900
MAX_USER_CALLS_PER_MIN = 200
