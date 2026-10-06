from __future__ import annotations

from fastapi import FastAPI, Request
from fastmcp import FastMCP

from .context import request_headers
from .runtime import runtime
from .tools import load_tools


mcp_server = FastMCP(name=f"{runtime.agent_id}-mcp")
load_tools(mcp_server)
mcp_app = mcp_server.http_app(stateless_http=True)

api = FastAPI(lifespan=mcp_app.lifespan)


@api.get("/")
async def index() -> dict:
    return {
        "status": "healthy",
        "agent_id": runtime.agent_id,
        "release_version": runtime.release_version,
        "mcp_endpoint": "/mcp",
    }


app = FastAPI(
    title=f"{runtime.agent_id} MCP",
    routes=[*mcp_app.routes, *api.routes],
    lifespan=mcp_app.lifespan,
)


@app.middleware("http")
async def capture_request_headers(request: Request, call_next):
    token = request_headers.set(dict(request.headers))
    try:
        return await call_next(request)
    finally:
        request_headers.reset(token)
