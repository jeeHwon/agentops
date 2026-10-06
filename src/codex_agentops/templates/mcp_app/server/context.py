from __future__ import annotations

import contextvars


request_headers: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "request_headers", default={}
)
