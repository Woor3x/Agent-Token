"""tenant_access_token管理 with TTL cache (方案-Agents §8.1)."""
from __future__ import annotations

import os
import time

import httpx


class FeishuOAuth:
    def __init__(self, base: str, app_id: str = "", app_secret: str = "") -> None:
        self._base = base.rstrip("/")
        self._app_id = app_id or os.environ.get("FEISHU_APP_ID", "mock-app-id")
        self._app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "mock-app-secret")
        self._cache: dict = {}

    async def get_tenant_token(self, client: httpx.AsyncClient | None = None) -> str:
        if self._cache.get("expires_at", 0) > time.time() + 60:
            return self._cache["token"]
        own = client is None
        c = client or httpx.AsyncClient(timeout=5.0)
        try:
            r = await c.post(
                f"{self._base}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            r.raise_for_status()
            data = r.json()
        finally:
            if own:
                await c.aclose()
        if data.get("code") != 0:
            raise RuntimeError(f"feishu auth failed: {data}")
        self._cache = {
            "token": data["tenant_access_token"],
            "expires_at": time.time() + int(data.get("expire", 7200)),
        }
        return self._cache["token"]
