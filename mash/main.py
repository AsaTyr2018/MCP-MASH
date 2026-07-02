from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .db import migrate
from .mcp_server import mcp
from .runtime_config import runtime_config
from .scheduler import scheduler


mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app_: FastAPI):
    migrate()
    async with mcp.session_manager.run():
        await runtime_config.start()
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            await runtime_config.stop()


app = FastAPI(title="MCP-MASH", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    is_mcp_path = path == "/mcp" or path.startswith("/mcp/")
    if is_mcp_path and settings.mcp_token:
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or token != settings.mcp_token:
            return JSONResponse({"detail": "invalid MASH bearer token"}, status_code=401)
    return await call_next(request)


app.mount("/mcp", mcp_app)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "MCP-MASH",
        "status": "ok",
        "mcp_url": f"{settings.public_url.rstrip('/')}/mcp/",
        "web_ui": False,
    }
