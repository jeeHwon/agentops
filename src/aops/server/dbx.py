"""Connects API routes to the dbx lifecycle when the server is configured for a workspace.

Without AOPS_REGISTRY and AOPS_WAREHOUSE_ID the routes keep returning 501 (contract only).
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Request

from ..dbx.service import Caller, Config, Service
from .common import NotImplementedYet


@lru_cache(maxsize=1)
def _service() -> Service | None:
    config = Config.from_env()
    return Service(config) if config else None


def service_for(command: str | None) -> Service:
    svc = _service()
    if svc is None:
        raise NotImplementedYet(command)
    return svc


LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _same_machine(request: Request) -> bool:
    """A browser on this PC talking to this PC's server, not another site (CSRF) or a rebound name."""
    client = getattr(getattr(request, "client", None), "host", None)
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    origin = request.headers.get("origin")
    origin_host = origin.split("://", 1)[-1].rsplit(":", 1)[0].strip("[]") if origin else None
    return client in LOOPBACK and host in LOOPBACK and (origin_host is None or origin_host in LOOPBACK)


def caller_of(request: Request, svc: Service) -> Caller:
    """Databricks Apps forwards the user's email and token; elsewhere the bearer token tells who it is.

    A server started on a PC with a Databricks CLI profile (`aops serve` for trying the console) has
    neither: the browser on that PC is the profile's own user.
    """
    in_apps = bool(os.environ.get("DATABRICKS_APP_NAME"))
    token = request.headers.get("x-forwarded-access-token") if in_apps else None
    if not token:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
    email = request.headers.get("x-forwarded-email") if in_apps else None  # only the Apps proxy sets these
    if not token and not email and svc.config.profile and not in_apps and _same_machine(request):
        auth = svc.w.config.authenticate().get("Authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
    return svc.identify(token, email)
