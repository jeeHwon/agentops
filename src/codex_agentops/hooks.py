from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
from typing import Any

from .config import ConfigError, load_settings
from .collector import ensure_collector
from .events import from_hook_payload
from .manifest import ManifestError, find_agent_root, load_manifest
from .outbox import Outbox
from .paths import heartbeat_path, outbox_path, uploader_log_path
from .usage import parse_transcript_usage, parse_transcript_usages


def handle_hook(raw: dict[str, Any], *, launch_uploader: bool = True) -> dict[str, Any]:
    cwd = str(raw.get("cwd") or os.getcwd())
    root = find_agent_root(cwd)
    if root is None:
        return {}
    try:
        manifest = load_manifest(root)
        settings = load_settings()
        outbox = Outbox(outbox_path())
        event_name = str(raw.get("hook_event_name") or "")
        if event_name in {"SessionStart", "SessionEnd"}:
            session_id = str(raw.get("session_id") or "").strip()
            if not session_id:
                raise ValueError("Hook payload must include session_id.")
            outbox.record_session(
                session_id=session_id,
                agent_id=manifest.agent_id,
                agent_root=str(manifest.root),
                model=str(raw.get("model") or "") or None,
                ended=event_name == "SessionEnd",
            )
            if event_name == "SessionStart" and settings.otel_mode == "local":
                ensure_collector(settings)
            elif event_name == "SessionEnd":
                transcript_path = raw.get("transcript_path")
                if launch_uploader and transcript_path:
                    _launch_reconciler(str(transcript_path), session_id)
            _write_heartbeat(event_name, manifest.agent_id)
            return {}
        event = from_hook_payload(raw, manifest, settings)
        outbox.append(event)
        if event.terminal:
            _record_transcript_usage(raw, event, outbox)
        elif event.event_name == "SubagentStop":
            _record_subagent_transcript_usage(raw, event, outbox)
        _write_heartbeat(event.event_name, manifest.agent_id)
        previous_failures = outbox.stats()["failed"]
        if event.terminal and launch_uploader:
            _launch_uploader(wait_for_usage_seconds=5)
        if previous_failures:
            return {
                "systemMessage": (
                    f"Codex AgentOps has {previous_failures} pending MLflow upload(s). "
                    "Run `codex-agentops flush` or `codex-agentops doctor`."
                )
            }
        return {}
    except (ConfigError, ManifestError, OSError, ValueError) as exc:
        return {"systemMessage": f"Codex AgentOps telemetry warning: {exc}"}
    except Exception as exc:  # hooks must never stop the user's turn
        return {"systemMessage": f"Codex AgentOps telemetry warning: {type(exc).__name__}"}


def _write_heartbeat(event_name: str, agent_id: str) -> None:
    target = heartbeat_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"event_name": event_name, "agent_id": agent_id, "observed_at_ns": time.time_ns()},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _record_transcript_usage(raw: dict[str, Any], event: Any, outbox: Outbox) -> None:
    transcript_path = raw.get("transcript_path")
    if not transcript_path:
        return
    try:
        usage = parse_transcript_usage(
            str(transcript_path), session_id=event.session_id, turn_id=event.turn_id
        )
    except (OSError, ValueError):
        return
    if usage is None:
        return
    canonical = json.dumps(
        {
            "source": "transcript",
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "usage": usage.__dict__,
        },
        sort_keys=True,
    )
    outbox.append_token_usage(
        event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        session_id=event.session_id,
        turn_id=event.turn_id,
        observed_ns=event.created_ns,
        source="transcript",
        usage=usage,
    )


def _record_subagent_transcript_usage(
    raw: dict[str, Any], event: Any, outbox: Outbox
) -> None:
    transcript_path = raw.get("agent_transcript_path")
    runtime_agent_id = str(raw.get("agent_id") or "").strip()
    if not transcript_path or not runtime_agent_id:
        return
    try:
        usage = parse_transcript_usage(
            str(transcript_path), session_id=runtime_agent_id, turn_id=event.turn_id
        )
    except (OSError, ValueError):
        return
    if usage is None:
        return
    canonical = json.dumps(
        {
            "source": "transcript",
            "session_id": runtime_agent_id,
            "turn_id": event.turn_id,
            "usage": usage.__dict__,
        },
        sort_keys=True,
    )
    outbox.append_token_usage(
        event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        session_id=runtime_agent_id,
        turn_id=event.turn_id,
        observed_ns=event.created_ns,
        source="transcript",
        usage=usage,
    )


def reconcile_session_transcript(
    transcript_path: str, session_id: str, outbox: Outbox
) -> int:
    try:
        usages = parse_transcript_usages(str(transcript_path), session_id=session_id)
    except (OSError, ValueError):
        return 0
    inserted = 0
    for turn_id, usage in usages.items():
        canonical = json.dumps(
            {
                "source": "transcript",
                "session_id": session_id,
                "turn_id": turn_id,
                "usage": usage.__dict__,
            },
            sort_keys=True,
        )
        if outbox.append_token_usage(
            event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            session_id=session_id,
            turn_id=turn_id,
            observed_ns=time.time_ns(),
            source="transcript",
            usage=usage,
        ):
            inserted += 1
    return inserted


def _launch_reconciler(transcript_path: str, session_id: str) -> None:
    log_path = uploader_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "codex_agentops.cli",
                "_reconcile",
                "--session-id",
                session_id,
                "--transcript-path",
                transcript_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )


def _launch_uploader(*, wait_for_usage_seconds: float = 0) -> None:
    log_path = uploader_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        command = [sys.executable, "-m", "codex_agentops.cli", "_upload"]
        if wait_for_usage_seconds:
            command.extend(["--wait-for-usage", str(wait_for_usage_seconds)])
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
