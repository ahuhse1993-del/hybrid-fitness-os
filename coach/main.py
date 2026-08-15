"""
coach/main.py
Combined ASGI entrypoint for Railway.

Routes:
  /mcp/*  → FastMCP Streamable HTTP server (Bearer auth required)
  /*      → Existing Flask app (unchanged)

Start command:
  uvicorn coach.main:app --host 0.0.0.0 --port $PORT
"""

import json
import logging

from starlette.middleware.wsgi import WSGIMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from coach.api import app as flask_app
from coach.mcp_server import create_mcp_asgi_app

logger = logging.getLogger(__name__)

_flask_asgi: ASGIApp = WSGIMiddleware(flask_app)
_mcp_asgi: ASGIApp = create_mcp_asgi_app()


async def _call_mcp_with_guard(scope: Scope, receive: Receive, send: Send) -> None:
    """
    Call the MCP sub-app, catching anything it raises (including
    ExceptionGroup/BaseExceptionGroup) instead of letting it crash the
    connection with a raw "Internal Server Error".

    Known upstream issue (mcp==2.0.0, latest available on PyPI — no newer
    version fixes this): the StreamableHTTPSessionManager's internal
    anyio TaskGroup can die from an unrelated failure (see
    github.com/modelcontextprotocol/python-sdk#1104 — "Task group is not
    initialized" / ExceptionGroup after the manager's run() context exits
    unexpectedly). Once that happens, EVERY subsequent request to /mcp
    raises "ExceptionGroup: unhandled errors in a TaskGroup" until the
    process restarts. We cannot patch the third-party SDK's internals, so
    this converts that crash into a clean JSON-RPC error response with a
    distinct message, instead of an opaque 500 that looks identical to
    every other failure. It does NOT self-heal the dead session manager —
    if this fires, the underlying fix is still a process restart.
    """
    response_started = False

    async def tracking_send(message: dict) -> None:
        nonlocal response_started
        if message.get("type") == "http.response.start":
            response_started = True
        await send(message)

    try:
        await _mcp_asgi(scope, receive, tracking_send)
    except BaseException as exc:  # noqa: BLE001 — must catch ExceptionGroup too
        logger.error("MCP sub-app raised %s: %s", type(exc).__name__, exc)
        if response_started:
            # Partial response already sent — nothing safe to do but stop.
            return
        body = json.dumps({
            "jsonrpc": "2.0",
            "error": {
                "code": -32000,
                "message": (
                    "MCP session manager error (upstream mcp SDK TaskGroup failure). "
                    "This endpoint needs a process restart to recover."
                ),
            },
            "id": None,
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """
    Path-prefix router — passes the full unmodified path to each sub-app.
    /mcp and /mcp/* → MCP server
    everything else  → Flask

    Lifespan: forwarded to the MCP sub-app only. mcp.streamable_http_app()
    starts its internal StreamableHTTPSessionManager during ASGI lifespan
    startup — without this, every POST /mcp request crashes with an
    Internal Server Error even though routing itself works. Flask/WSGI has
    no lifespan concept, so it must not receive this scope type. Lifespan
    failures are intentionally NOT guarded here — uvicorn needs to see them
    to report startup failure correctly; the guard below only wraps
    per-request HTTP handling.
    """
    scope_type = scope.get("type")
    if scope_type == "lifespan":
        await _mcp_asgi(scope, receive, send)
        return
    if scope_type == "http":
        path: str = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await _call_mcp_with_guard(scope, receive, send)
            return
    await _flask_asgi(scope, receive, send)
