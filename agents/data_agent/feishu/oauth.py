"""tenant_access_token 管理 with TTL cache (方案-Agents §8.1).

Fail-fast safety: when ``base`` points at a real Feishu / Lark host but the
credentials are still placeholder (``mock-app-id`` / empty), we raise
:class:`FeishuError` at construction time so a misconfigured deployment
never gets a chance to round-trip prod with bogus identities.
"""
from __future__ import annotations

import os
import time

import httpx

from ._http import request_with_retry, parse_or_raise
from .errors import FeishuError

_MOCK_HOST_TOKENS = ("feishu-mock", "testserver", "127.0.0.1", "localhost")
_MOCK_CRED_VALUES = {"", "mock-app-id", "mock-app-secret"}
_AUTH_ENDPOINT = "/open-apis/auth/v3/tenant_access_token/internal"


class FeishuOAuth:
    def __init__(self, base: str, app_id: str = "", app_secret: str = "") -> None:
        self._base = base.rstrip("/")
        self._app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self._cache: dict = {}
        if self._is_real_feishu() and (
            self._app_id in _MOCK_CRED_VALUES or self._app_secret in _MOCK_CRED_VALUES
        ):
            raise FeishuError(
                code=-1,
                msg=(
                    "FEISHU_BASE points at real Feishu but FEISHU_APP_ID / "
                    "FEISHU_APP_SECRET are still empty / placeholder — refusing "
                    "to call prod with bogus credentials"
                ),
                endpoint=_AUTH_ENDPOINT,
            )

    def _is_real_feishu(self) -> bool:
        host = self._base.lower()
        return not any(tok in host for tok in _MOCK_HOST_TOKENS)

    async def get_tenant_token(self, client: httpx.AsyncClient | None = None) -> str:
        if self._cache.get("expires_at", 0) > time.time() + 60:
            return self._cache["token"]
        own = client is None
        c = client or httpx.AsyncClient(timeout=5.0)
        try:
            r = await request_with_retry(
                c,
                "POST",
                f"{self._base}{_AUTH_ENDPOINT}",
                headers={"Content-Type": "application/json"},
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = parse_or_raise(r, endpoint=_AUTH_ENDPOINT)
        finally:
            if own:
                await c.aclose()
        self._cache = {
            "token": data["tenant_access_token"],
            "expires_at": time.time() + int(data.get("expire", 7200)),
        }
        return self._cache["token"]
