"""Gateway Mock standalone server (FastAPI on :9200).

Reads peer agent locations from env. Lookup table built at startup:

    AGENT_DATA_AGENT_URL=http://data-agent:8101
    AGENT_WEB_AGENT_URL=http://web-agent:8102
    AGENT_DOC_ASSISTANT_URL=http://doc-assistant:8100

Forwards the inbound ``Authorization`` and ``DPoP`` plus the standard trace
headers (``Traceparent``, ``X-Plan-Id``, ``X-Task-Id``, ``X-Idempotency-Key``)
to the agent's ``/invoke`` endpoint. The Gateway intentionally does **not**
inspect tokens — that is the receiving agent's responsibility (zero-trust).
"""
from __future__ import annotations

import os
from typing import Dict

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

_AGENTS = ("data_agent", "web_agent", "doc_assistant")
_FORWARDED_HEADERS = (
    "Traceparent",
    "X-Plan-Id",
    "X-Task-Id",
    "X-Idempotency-Key",
    "X-Subject-Token",
)


def _peers_from_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in _AGENTS:
        val = os.environ.get(f"AGENT_{name.upper()}_URL")
        if val:
            out[name] = val.rstrip("/")
    return out


def create_app() -> FastAPI:
    app = FastAPI(title="gateway-mock", version="1.0.0")
    peers = _peers_from_env()
    timeout = float(os.environ.get("GATEWAY_TIMEOUT", "15"))

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "gateway-mock", "peers": peers}

    @app.post("/a2a/invoke")
    async def a2a_invoke(
        request: Request,
        x_target_agent: str = Header(...),
        authorization: str = Header(...),
        dpop: str = Header(default=""),
    ) -> JSONResponse:
        target = peers.get(x_target_agent)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"no such agent {x_target_agent}",
            )
        body = await request.json()
        headers = {
            "Authorization": authorization,
            "DPoP": dpop,
            "Content-Type": "application/json",
        }
        for h in _FORWARDED_HEADERS:
            v = request.headers.get(h)
            if v:
                headers[h] = v
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.post(f"{target}/invoke", headers=headers, json=body)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"error": {"code": "UPSTREAM_NON_JSON", "message": resp.text[:200]}}
        return JSONResponse(status_code=resp.status_code, content=payload)

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    port = int(os.environ.get("PORT", "9200"))
    uvicorn.run(app, host="0.0.0.0", port=port)
