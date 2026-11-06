"""aops API server (`aops serve`). Needs the `serve` extra (FastAPI, uvicorn)."""

from .app import create_app

__all__ = ["create_app"]
