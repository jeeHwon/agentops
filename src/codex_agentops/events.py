from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .manifest import AgentManifest, calculate_definition_checksums, read_release_metadata
from .redaction import redact_json_body


SUPPORTED_EVENTS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
}
TERMINAL_EVENTS = {"Stop", "Interrupt"}
CONTENT_CAPTURE_DISABLED = "[CONTENT_CAPTURE_DISABLED]"


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
        return CONTENT_CAPTURE_DISABLED
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
    release = read_release_metadata(manifest.root) or {}
    from .project import resolve_project

    tracing = resolve_project(manifest.root, settings)["tracing"]
    release_version = (
        release.get("version")
        if release.get("harness_sha256") == checksums.harness
        and release.get("skills_sha256") == checksums.combined_skills
        and (
            not tracing.get("experiment_id")
            or release.get("project", {}).get("tracing") == tracing
        )
        else None
    )
    common: dict[str, Any] = {
        "model": str(raw.get("model", "")) or None,
        "permission_mode": str(raw.get("permission_mode", "")) or None,
        "cwd": str(Path(str(raw.get("cwd", manifest.root))).resolve()),
        "codex_turn_id": turn_id,
        "user_name": settings.user_name or None,
        "harness_checksum": checksums.harness,
        "skill_checksums": checksums.skills,
        "skills_checksum": checksums.combined_skills,
        "release_version": str(release_version or "working"),
        "agent_type": manifest.agent_type,
        "agent_name": manifest.name,
        "agent_version": manifest.agent_version,
        "build_type": manifest.build_type,
        "serving_type": manifest.serving_type,
        "origin": manifest.origin,
        "security_level": manifest.security_level,
        "uc_permissions": list(manifest.uc_permissions),
        "flagship_model": manifest.flagship_model,
        "model_service": manifest.model_service,
        "success_criteria": manifest.success_criteria,
        "service_criticality": manifest.service_criticality,
        "data_sensitivity": manifest.data_sensitivity,
        "owner": manifest.owner or None,
        "owner_group": manifest.owner_group or None,
        "runtime": "codex",
        "project": manifest.agent_id,
        "source": "codex-plugin-hooks",
    }
    if event_name == "UserPromptSubmit":
        common["git_commit"] = _git_commit(manifest.root)
        agents_root = manifest.root / "agents"
        common["subagent_defs"] = sorted(
            item.name for item in agents_root.iterdir() if item.is_dir()
        ) if agents_root.is_dir() else []
    if tracing.get("experiment_id"):
        common["tracing"] = tracing
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
    elif event_name in {"PreToolUse", "PostToolUse"}:
        specific = {
            "tool_name": str(raw.get("tool_name", "unknown")),
            "tool_use_id": str(raw.get("tool_use_id", "")) or None,
            "tool_failed": tool_failed(raw),
            "tool_input": _safe_body(raw.get("tool_input"), settings),
            "tool_response": _safe_body(raw.get("tool_response"), settings),
        }
    else:
        specific = {}
    if event_name in TERMINAL_EVENTS:
        specific["end_reason"] = event_name
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
    if payload.get("tool_failed") is True:
        return True
    response = payload.get("tool_response")
    if _contains_error(response):
        return True
    if isinstance(response, str):
        lowered = response.lower()
        exit_codes = re.findall(
            r"(?im)^\s*(?:exit\s*code|exitcode|return\s*code|"
            r"(?:process|command)\s+exited\s+with\s+code)\s*[:=]?\s*(-?\d+)\s*$",
            response,
        )
        return any(int(code) != 0 for code in exit_codes) or lowered.startswith("error:")
    return False


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _contains_error(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("is_error") is True:
            return True
        status = value.get("status")
        if value.get("success") is False or (
            isinstance(status, str) and status.lower() in {"error", "failed"}
        ):
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
