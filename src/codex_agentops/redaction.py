from __future__ import annotations

import json
import os
import re
from typing import Any


MAX_BODY_CHARS = 15_000
MAX_CONFIGURED_BODY_CHARS = 65_536
MIN_CONFIGURED_BODY_CHARS = 64
REDACTED = "[REDACTED]"


_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
            r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]{12,}"), f"Bearer {REDACTED}"),
    (re.compile(r"\bdapi[a-zA-Z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)\b"
            r"\s*[:=]\s*)([^\s,;\]}]+|\"[^\"]*\"|'[^']*')"
        ),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"), "[REDACTED_EMAIL]"),
    (
        re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
        "[REDACTED_PHONE]",
    ),
)


def trace_maxlen() -> int:
    raw = os.getenv("AGENTOPS_TRACE_MAXLEN", "")
    try:
        configured = int(raw) if raw else MAX_BODY_CHARS
    except ValueError:
        return MAX_BODY_CHARS
    return (configured if MIN_CONFIGURED_BODY_CHARS <= configured <= MAX_CONFIGURED_BODY_CHARS
            else MAX_BODY_CHARS)


def redact_text(value: str, *, limit: int | None = None) -> str:
    limit = trace_maxlen() if limit is None else limit
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if len(redacted) > limit:
        suffix = f"[TRUNCATED {len(redacted) - limit} CHARS]"
        redacted = f"{redacted[:limit - len(suffix)]}{suffix}" if len(suffix) < limit else redacted[:limit]
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if re.search(r"(?i)(?:password|passwd|secret|token|api[_-]?key)", key_text):
                result[key_text] = REDACTED
            else:
                result[key_text] = redact_value(child)
        return result
    if isinstance(value, list):
        return [redact_value(child) for child in value]
    if isinstance(value, tuple):
        return [redact_value(child) for child in value]
    return value


def redact_json_body(value: Any, *, limit: int | None = None) -> Any:
    """Redact recursively and cap the serialized body without parsing transcripts."""
    limit = trace_maxlen() if limit is None else limit
    redacted = redact_value(value)
    serialized = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) <= limit:
        return redacted
    overhead = len(json.dumps({"truncated": True, "content": ""}, ensure_ascii=False))
    if limit < overhead:
        return redact_text(serialized, limit=limit)
    budget = max(0, limit - overhead)
    while True:
        result = {"truncated": True, "content": redact_text(serialized, limit=budget)}
        excess = len(json.dumps(result, ensure_ascii=False)) - limit
        if excess <= 0:
            return result
        budget = max(0, budget - excess)
