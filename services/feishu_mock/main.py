"""Feishu Mock server entry: FastAPI on :9000."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import auth, bitable, calendar, contact, docx, drive


def create_app() -> FastAPI:
    app = FastAPI(title="feishu-mock", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "service": "feishu-mock"}

    app.include_router(auth.router)
    app.include_router(bitable.router)
    app.include_router(contact.router)
    app.include_router(calendar.router)
    app.include_router(docx.router)
    app.include_router(drive.router)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
