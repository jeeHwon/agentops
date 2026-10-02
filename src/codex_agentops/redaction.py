from __future__ import annotations

import json
import re
from typing import Any


MAX_BODY_CHARS = 15_000
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


def redact_text(value: str, *, limit: int = MAX_BODY_CHARS) -> str:
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if len(redacted) > limit:
        omitted = len(redacted) - limit
        redacted = f"{redacted[:limit]}\n[TRUNCATED {omitted} CHARS]"
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


def redact_json_body(value: Any, *, limit: int = MAX_BODY_CHARS) -> Any:
    """Redact recursively and cap the serialized body without parsing transcripts."""
    redacted = redact_value(value)
    serialized = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) <= limit:
        return redacted
    return {
        "truncated": True,
        "content": redact_text(serialized, limit=limit),
    }
