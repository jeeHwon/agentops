from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .manifest import AgentManifest, calculate_definition_checksums
from .redaction import redact_json_body, redact_text


SUPPORTED_EVENTS = {
    "UserPromptSubmit",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
}
TERMINAL_EVENTS = {"Stop", "Interrupt"}


@dataclass(frozen=True)
class HookEvent:
    event_id: str
    turn_key: str
    event_name: str
    session_id: str
    turn_id: str
    agent_id: str
    agent_root: str
    created_ns: int
    payload: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.event_name in TERMINAL_EVENTS


def _stable_hash(*values: str) -> str:
    body = "\x1f".join(values).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _safe_body(value: Any, settings: Settings) -> Any:
    if not settings.capture_content:
        return "[CONTENT_CAPTURE_DISABLED]"
    if isinstance(value, str):
        return redact_text(value)
    return redact_json_body(value)


def from_hook_payload(
    raw: dict[str, Any], manifest: AgentManifest, settings: Settings, *, now_ns: int | None = None
) -> HookEvent:
    event_name = str(raw.get("hook_event_name", ""))
    if event_name not in SUPPORTED_EVENTS:
        raise ValueError(f"Unsupported hook event: {event_name or '<missing>'}")
    session_id = str(raw.get("session_id", "")).strip()
    turn_id = str(raw.get("turn_id", "")).strip()
    if not session_id or not turn_id:
        raise ValueError("Hook payload must include session_id and turn_id.")
    created_ns = now_ns or time.time_ns()
    checksums = calculate_definition_checksums(manifest.root)
    common: dict[str, Any] = {
        "model": str(raw.get("model", "")) or None,
        "permission_mode": str(raw.get("permission_mode", "")) or None,
        "cwd": str(Path(str(raw.get("cwd", manifest.root))).resolve()),
        "codex_turn_id": turn_id,
        "user_name": settings.user_name or None,
        "harness_checksum": checksums.harness,
        "skill_checksums": checksums.skills,
        "skills_checksum": checksums.combined_skills,
    }
    runtime_agent_id = str(raw.get("agent_id", "")).strip()
    runtime_agent_type = str(raw.get("agent_type", "")).strip()
    if runtime_agent_id:
        common["codex_agent_id"] = runtime_agent_id
    if runtime_agent_type:
        common["codex_agent_type"] = runtime_agent_type
    if event_name == "UserPromptSubmit":
        specific = {"prompt": _safe_body(raw.get("prompt", ""), settings)}
    elif event_name == "Stop":
        specific = {
            "last_assistant_message": _safe_body(raw.get("last_assistant_message"), settings),
            "stop_hook_active": bool(raw.get("stop_hook_active", False)),
        }
    elif event_name == "Interrupt":
        specific = {}
    elif event_name == "SubagentStart":
        specific = {}
    elif event_name == "SubagentStop":
        specific = {
            "agent_transcript_path": str(raw.get("agent_transcript_path") or "") or None,
            "last_assistant_message": _safe_body(raw.get("last_assistant_message"), settings),
            "stop_hook_active": bool(raw.get("stop_hook_active", False)),
        }
    else:
        specific = {
            "tool_name": str(raw.get("tool_name", "unknown")),
            "tool_use_id": str(raw.get("tool_use_id", "")) or None,
            "tool_input": _safe_body(raw.get("tool_input"), settings),
            "tool_response": _safe_body(raw.get("tool_response"), settings),
        }
    payload = {**common, **specific}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    discriminator = str(raw.get("tool_use_id", ""))
    turn_key = _stable_hash(manifest.agent_id, session_id, turn_id)
    event_id = _stable_hash(turn_key, event_name, discriminator, canonical)
    return HookEvent(
        event_id=event_id,
        turn_key=turn_key,
        event_name=event_name,
        session_id=session_id,
        turn_id=turn_id,
        agent_id=manifest.agent_id,
        agent_root=str(manifest.root),
        created_ns=created_ns,
        payload=payload,
    )


def tool_failed(payload: dict[str, Any]) -> bool:
    response = payload.get("tool_response")
    if _contains_error(response):
        return True
    if isinstance(response, str):
        lowered = response.lower()
        return "exit code: 1" in lowered or lowered.startswith("error:")
    return False


def _contains_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("is_error") is True:
            return True
        exit_code = value.get("exit_code", value.get("exitCode"))
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        if value.get("error"):
            return True
        return any(_contains_error(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_error(child) for child in value)
    return False
