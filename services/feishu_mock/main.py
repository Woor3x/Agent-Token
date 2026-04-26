"""Feishu Mock server entry: FastAPI on :9000."""
from __future__ import annotations

from fastapi import FastAPI

from .routes import auth, bitable, calendar, contact, docx


def create_app() -> FastAPI:
    app = FastAPI(title="feishu-mock", version="1.0.0")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "feishu-mock"}

    app.include_router(auth.router)
    app.include_router(bitable.router)
    app.include_router(contact.router)
    app.include_router(calendar.router)
    app.include_router(docx.router)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
