"""
coach/main.py
Combined ASGI entrypoint for Railway.

Routes:
  /mcp/*  → FastMCP Streamable HTTP server (Bearer auth required)
  /*      → Existing Flask app (unchanged)

Start command:
  uvicorn coach.main:app --host 0.0.0.0 --port $PORT
"""

from starlette.middleware.wsgi import WSGIMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from coach.api import app as flask_app
from coach.mcp_server import create_mcp_asgi_app

_flask_asgi: ASGIApp = WSGIMiddleware(flask_app)
_mcp_asgi: ASGIApp = create_mcp_asgi_app()


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """
    Path-prefix router — passes the full unmodified path to each sub-app.
    /mcp and /mcp/* → MCP server
    everything else  → Flask
    """
    if scope.get("type") == "http":
        path: str = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await _mcp_asgi(scope, receive, send)
            return
    await _flask_asgi(scope, receive, send)
