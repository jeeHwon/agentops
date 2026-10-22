from __future__ import annotations

import re
from collections import defaultdict, deque

from .outbox import StoredEvent


def metric_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:64] or "unknown"


def observed_calls(
    events: tuple[StoredEvent, ...],
) -> tuple[tuple[StoredEvent, StoredEvent | None], ...]:
    by_id: dict[tuple[str, str], deque[StoredEvent]] = defaultdict(deque)
    missing_ids: dict[tuple[str, str], deque[StoredEvent]] = defaultdict(deque)
    started: set[tuple[str, str]] = set()
    completed: set[tuple[str, str]] = set()
    calls: list[tuple[StoredEvent, StoredEvent | None]] = []
    for event in sorted(events, key=lambda item: item.created_ns):
        key = (str(event.payload.get("codex_agent_id") or ""), str(event.payload.get("tool_use_id") or ""))
        tool_key = (key[0], str(event.payload.get("tool_name") or "unknown"))
        if event.event_name == "PreToolUse":
            if key[1]:
                if key in started or key in completed:
                    continue
                started.add(key)
            (by_id[key] if key[1] else missing_ids[tool_key]).append(event)
            continue
        if event.event_name != "PostToolUse":
            continue
        if key[1] and key in completed:
            continue
        known = by_id[key] if key[1] else deque()
        unnamed = missing_ids[tool_key]
        earlier = known.popleft() if known else unnamed.popleft() if unnamed else None
        calls.append((earlier or event, event))
        if key[1]:
            completed.add(key)
    for remaining in (*by_id.values(), *missing_ids.values()):
        calls.extend((event, None) for event in remaining)
    return tuple(sorted(calls, key=lambda pair: (pair[0].created_ns, pair[0].event_id)))
