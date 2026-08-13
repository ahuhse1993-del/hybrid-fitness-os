"""
coach/main.py
Combined ASGI entrypoint for Railway.

Serves:
  /mcp/*  → FastMCP Streamable HTTP server (Bearer auth required)
  /*      → Existing Flask app (unchanged)

Start command:
  uvicorn coach.main:app --host 0.0.0.0 --port $PORT
"""

from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount

from coach.api import app as flask_app
from coach.mcp_server import create_mcp_asgi_app

app = Starlette(
    routes=[
        Mount("/mcp", app=create_mcp_asgi_app()),
        Mount("/", app=WSGIMiddleware(flask_app)),
    ]
)
