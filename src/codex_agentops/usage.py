from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TokenUsage":
        input_tokens = _non_negative_int(raw.get("input_tokens"))
        output_tokens = _non_negative_int(raw.get("output_tokens"))
        total_tokens = _non_negative_int(raw.get("total_tokens"))
        if total_tokens == 0 and input_tokens + output_tokens:
            total_tokens = input_tokens + output_tokens
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=_non_negative_int(
                raw.get("cached_input_tokens", raw.get("cached_token_count"))
            ),
            cache_write_input_tokens=_non_negative_int(
                raw.get("cache_write_input_tokens", raw.get("cache_write_token_count"))
            ),
            reasoning_output_tokens=_non_negative_int(
                raw.get("reasoning_output_tokens", raw.get("reasoning_token_count"))
            ),
            total_tokens=total_tokens,
        )

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens + other.cache_write_input_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens + other.reasoning_output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


def parse_transcript_usage(
    path: str | Path, *, session_id: str, turn_id: str
) -> TokenUsage | None:
    """Read only versioned token records; never reconstruct prompt or response bodies."""
    return parse_transcript_usages(path, session_id=session_id).get(turn_id)


def parse_transcript_usages(path: str | Path, *, session_id: str) -> dict[str, TokenUsage]:
    """Return the final cumulative usage record for every turn in one session."""
    target = Path(path).expanduser().resolve()
    result: dict[str, TokenUsage] = {}
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if row.get("type") != "token_usage_record":
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            payload_sessions = {
                str(value)
                for value in (payload.get("session_id"), payload.get("thread_id"))
                if value
            }
            payload_turn = str(payload.get("turn_id") or "")
            if session_id not in payload_sessions or not payload_turn:
                continue
            raw_usage = payload.get("turn_token_usage") or payload.get("usage")
            if isinstance(raw_usage, dict):
                result[payload_turn] = TokenUsage.from_dict(raw_usage)
    return result


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
