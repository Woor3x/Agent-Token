"""DataAgent business handler (see 方案-Agents §5.3)."""
from __future__ import annotations

import re
from typing import Any, Callable

import httpx

from agents.common.auth import VerifiedClaims
from agents.common.capability import Capability
from agents.common.logging import get_logger

from .feishu import bitable, calendar, contact
from .feishu.oauth import FeishuOAuth

ClientFactory = Callable[[], httpx.AsyncClient]

_log = get_logger("agents.data_agent")

_APP_TABLE_RE = re.compile(r"^app_token:(?P<app>[^/]+)/table:(?P<table>.+)$")
_DEPT_RE = re.compile(r"^department:(?P<dept>.+)$")
_CAL_RE = re.compile(r"^calendar:(?P<cal>.+)$")


def _sanitize(rows: list[dict]) -> list[dict]:
    """Defang inline instructions in data (prompt-injection mitigation).

    Drop any obvious instruction prefixes before returning to orchestrator.
    """
    cleaned: list[dict] = []
    for r in rows:
        f = dict(r.get("fields") or {})
        for k, v in list(f.items()):
            if isinstance(v, str) and v.lower().startswith(
                ("ignore previous", "system:", "<|system")
            ):
                f[k] = "[sanitized]"
        cleaned.append({**r, "fields": f})
    return cleaned


class DataAgentHandler:
    def __init__(
        self,
        *,
        feishu_base: str,
        oauth: FeishuOAuth | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._base = feishu_base
        self._oauth = oauth or FeishuOAuth(base=feishu_base)
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=5.0))

    async def __call__(
        self, body: dict, claims: VerifiedClaims, cap: Capability
    ) -> dict[str, Any]:
        intent = body.get("intent") or {}
        action = intent.get("action")
        resource = intent.get("resource") or ""
        params = intent.get("params") or {}

        cap_item = cap.find(action, resource)
        if cap_item is None:
            raise PermissionError(f"capability miss: {action} {resource}")
        constraints = cap_item.constraints or {}

        async with self._client_factory() as client:
            token = await self._oauth.get_tenant_token(client=client)
            if action == "feishu.bitable.read":
                m = _APP_TABLE_RE.match(resource)
                if not m:
                    raise ValueError(f"bad bitable resource: {resource}")
                page_size = min(
                    int(params.get("page_size", 100)),
                    int(constraints.get("max_rows_per_call", 1000)),
                )
                rows = await bitable.list_records(
                    base=self._base,
                    token=token,
                    app_token=m.group("app"),
                    table_id=m.group("table"),
                    page_size=page_size,
                    view_id=params.get("view_id"),
                    client=client,
                )
                return {"records": _sanitize(rows), "count": len(rows)}
            if action == "feishu.contact.read":
                m = _DEPT_RE.match(resource)
                if not m:
                    raise ValueError(f"bad contact resource: {resource}")
                users = await contact.list_users(
                    base=self._base, token=token, dept_id=m.group("dept"), client=client
                )
                return {"users": users, "count": len(users)}
            if action == "feishu.calendar.read":
                m = _CAL_RE.match(resource)
                if not m:
                    raise ValueError(f"bad calendar resource: {resource}")
                events = await calendar.list_events(
                    base=self._base, token=token, cal_id=m.group("cal"), client=client
                )
                return {"events": events, "count": len(events)}
        raise ValueError(f"unsupported action: {action}")
