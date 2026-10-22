from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from codex_agentops.config import Settings, save_settings
from codex_agentops.hooks import handle_hook, reconcile_session_websearch
from codex_agentops.manifest import create_agent
from codex_agentops.outbox import Outbox
from codex_agentops.paths import outbox_path


def test_tool_failure_recognizes_actual_exit_and_structured_errors():
    from codex_agentops.events import tool_failed

    assert tool_failed({"tool_response": "Process exited with code 2"})
    assert tool_failed({"tool_response": {"success": False}})
    assert tool_failed({"tool_response": {"status": "error"}})
    assert not tool_failed({"tool_response": "Process exited with code 0"})


def test_hook_ignores_non_agent_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    result = handle_hook(
        {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t", "cwd": str(tmp_path)},
        launch_uploader=False,
    )
    assert result == {}
    assert not outbox_path().exists()


def test_hook_is_disabled_without_codex_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("chosen", "https://example.com"))
    root = create_agent("test-agent", tmp_path / "agent")
    assert handle_hook(
        {"hook_event_name": "SessionStart", "session_id": "s", "cwd": str(root)},
        launch_uploader=False,
    ) == {}
    assert not outbox_path().exists()


def test_skill_events_snapshot_shared_tracing_and_mark_changed_environment_as_working(tmp_path):
    from dataclasses import replace

    from codex_agentops.events import from_hook_payload
    from codex_agentops.manifest import load_manifest
    from codex_agentops.release import build_release, write_release_metadata

    root = create_agent("test-agent", tmp_path / "agent")
    settings = Settings(
        "chosen", "https://example.com", registry="main.aops",
        warehouse_id="warehouse", experiment_id="123", codex_enabled=True,
    )
    write_release_metadata(build_release(root, "1.0.0", "main.aops", settings=settings))
    assert not (root / "aops.yaml").exists()
    payload = {
        "hook_event_name": "Stop", "session_id": "s", "turn_id": "t",
        "cwd": str(root), "last_assistant_message": "done",
    }
    event = from_hook_payload(payload, load_manifest(root), settings)
    assert event.payload["tracing"] == settings.tracing
    assert event.payload["release_version"] == "1.0.0"
    changed = from_hook_payload(payload, load_manifest(root), replace(settings, experiment_id="456"))
    assert changed.payload["release_version"] == "working"
    assert event.payload["tracing"]["experiment_id"] == "123"


def test_hook_records_supported_event_in_agent_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com", codex_enabled=True))
    root = create_agent("test-agent", tmp_path / "agent")
    result = handle_hook(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "s",
            "turn_id": "t",
            "cwd": str(root),
            "prompt": "hello user@example.com",
        },
        launch_uploader=False,
    )
    assert result == {}
    stats = Outbox(outbox_path()).stats()
    assert stats["open"] == 1


def test_cli_hook_subprocess_collects_subagent_and_tool_without_content(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com",
                           codex_enabled=True, otel_mode="external"))
    root = create_agent("test-agent", tmp_path / "agent")
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    environment["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{environment['PATH']}"
    hook_script = project_root / "plugins" / "codex-agentops" / "scripts" / "hook.sh"
    command = (["sh", str(hook_script)] if Path(sys.executable).with_name("aops").is_file()
               else [sys.executable, "-m", "codex_agentops.cli", "_hook"])
    for name, details in (
        ("UserPromptSubmit", {"prompt": "sensitive text"}),
        ("SubagentStart", {"agent_id": "child-1", "agent_type": "explorer"}),
        ("PreToolUse", {"agent_id": "child-1", "tool_name": "Bash",
                        "tool_use_id": "call-1", "tool_input": {"command": "sensitive text"}}),
        ("PostToolUse", {"agent_id": "child-1", "tool_name": "Bash",
                         "tool_use_id": "call-1", "tool_response": "Process exited with code 2"}),
        ("SubagentStop", {"agent_id": "child-1", "agent_type": "explorer"}),
    ):
        result = subprocess.run(
            command,
            input=json.dumps({"hook_event_name": name, "session_id": "synthetic-session",
                              "turn_id": "turn-1", "cwd": str(root), **details}),
            text=True, capture_output=True, check=True, env=environment,
        )
        assert json.loads(result.stdout) == {}
    calls = Outbox(outbox_path()).call_inventory("synthetic-session")
    assert len(calls) == 3
    tool = next(item for item in calls if item["tool_name"] == "Bash")
    assert tool["call_id"] == "call-1"
    assert tool["result_observed"] is True
    assert tool["runtime_agent_id"] == "child-1"
    assert "sensitive text" not in json.dumps(calls)


def test_session_end_without_transcript_schedules_session_upload(tmp_path, monkeypatch):
    import codex_agentops.hooks as hooks

    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com",
                           codex_enabled=True))
    root = create_agent("test-agent", tmp_path / "agent")
    uploads = []
    monkeypatch.setattr(hooks, "_launch_uploader", lambda **options: uploads.append(options))
    assert handle_hook({"hook_event_name": "SessionEnd", "session_id": "synthetic-session",
                        "cwd": str(root), "reason": "other"}) == {}
    assert uploads == [{"wait_for_usage_seconds": 2}]
    assert Outbox(outbox_path()).stats()["session_pending"] == 1


def test_invalid_optional_summary_does_not_lose_session_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com",
                           codex_enabled=True))
    root = create_agent("test-agent", tmp_path / "agent")
    (root / "run_summary.json").write_text("not-json", encoding="utf-8")
    warning = handle_hook({"hook_event_name": "SessionEnd", "session_id": "synthetic-session",
                           "cwd": str(root)}, launch_uploader=False)
    assert "run_summary.json" in warning["systemMessage"]
    session = Outbox(outbox_path()).claim_session()
    assert session is not None
    assert session.custom_tags["agentops.run_summary_status"] == "invalid"


def test_session_end_snapshots_numeric_summary_and_masks_consented_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com",
                           codex_enabled=True, capture_content=True,
                           content_capture_consent=True))
    root = create_agent("test-agent", tmp_path / "agent")
    (root / "run_summary.json").write_text(
        '{"checks_passed": 2, "note": "user@example.com", "employee_id": 123456}',
        encoding="utf-8",
    )
    assert handle_hook({"hook_event_name": "SessionEnd", "session_id": "synthetic-session",
                        "cwd": str(root), "reason": "other"}, launch_uploader=False) == {}
    session = Outbox(outbox_path()).claim_session()
    assert session is not None
    assert session.custom_metrics["agentops.metrics.custom.checks_passed"] == 2
    assert "agentops.metrics.custom.employee_id" not in session.custom_metrics
    assert session.custom_tags["custom.note"] == "[REDACTED_EMAIL]"


def test_stop_hook_attaches_exact_transcript_token_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(
        Settings("test-profile", "https://example.cloud.databricks.com", otel_mode="external", codex_enabled=True)
    )
    root = create_agent("test-agent", tmp_path / "agent")
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "token_usage_record",
                "payload": {
                    "session_id": "s",
                    "turn_id": "t",
                    "turn_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_input_tokens": 30,
                        "cache_write_input_tokens": 40,
                        "reasoning_output_tokens": 5,
                        "total_tokens": 120,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = handle_hook(
        {
            "hook_event_name": "Stop",
            "session_id": "s",
            "turn_id": "t",
            "cwd": str(root),
            "last_assistant_message": "done",
            "transcript_path": str(transcript),
        },
        launch_uploader=False,
    )
    assert result == {}
    turn = Outbox(outbox_path()).claim(require_usage=True)
    assert turn is not None
    assert turn.token_usage_source == "transcript"
    assert turn.token_usage is not None
    assert turn.token_usage.total_tokens == 120


def test_stop_reconciles_hosted_websearch_without_query_or_token_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com", otel_mode="external", codex_enabled=True))
    root = create_agent("test-agent", tmp_path / "agent")
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": "s", "turn_id": "t",
        "completed_at_ms": 1700000000000,
        "item": {"type": "WebSearch", "id": "search-1", "query": "secret"},
    }}) + "\n", encoding="utf-8")
    assert handle_hook({"hook_event_name": "Stop", "session_id": "s", "turn_id": "t",
                        "cwd": str(root), "transcript_path": str(transcript)},
                       launch_uploader=False) == {}
    outbox = Outbox(outbox_path())
    assert reconcile_session_websearch(str(transcript), "s", outbox) == 0
    turn = outbox.claim()
    assert turn is not None
    assert turn.token_usage is None
    tools = [event for event in turn.events if event.event_name == "PostToolUse"]
    assert len(tools) == 1
    assert tools[0].payload["tool_use_id"] == "search-1"
    assert tools[0].payload["agentops.capture_source"] == "codex-transcript"
    assert "secret" not in str(tools[0].payload)


def test_subagent_hooks_attach_child_usage_to_root_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(
        Settings("test-profile", "https://example.cloud.databricks.com", otel_mode="external", codex_enabled=True)
    )
    root = create_agent("test-agent", tmp_path / "agent")
    common = {"session_id": "root-session", "cwd": str(root)}
    handle_hook(
        {
            **common,
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "root-turn",
            "prompt": "delegate",
        },
        launch_uploader=False,
    )
    handle_hook(
        {
            **common,
            "hook_event_name": "SubagentStart",
            "turn_id": "child-turn",
            "agent_id": "child-1",
            "agent_type": "explorer",
        },
        launch_uploader=False,
    )
    child_transcript = tmp_path / "child.jsonl"
    child_transcript.write_text(
        json.dumps(
            {
                "type": "token_usage_record",
                "payload": {
                    "session_id": "root-session",
                    "thread_id": "child-1",
                    "root_turn_id": "root-turn",
                    "turn_id": "child-turn",
                    "turn_token_usage": {
                        "input_tokens": 30,
                        "output_tokens": 10,
                        "total_tokens": 40,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    handle_hook(
        {
            **common,
            "hook_event_name": "SubagentStop",
            "turn_id": "child-turn",
            "agent_id": "child-1",
            "agent_type": "explorer",
            "agent_transcript_path": str(child_transcript),
            "last_assistant_message": "found it",
        },
        launch_uploader=False,
    )
    root_transcript = tmp_path / "root.jsonl"
    root_transcript.write_text(
        json.dumps(
            {
                "type": "token_usage_record",
                "payload": {
                    "session_id": "root-session",
                    "turn_id": "root-turn",
                    "turn_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    handle_hook(
        {
            **common,
            "hook_event_name": "Stop",
            "turn_id": "root-turn",
            "transcript_path": str(root_transcript),
            "last_assistant_message": "done",
        },
        launch_uploader=False,
    )

    turn = Outbox(outbox_path()).claim(require_usage=True)
    assert turn is not None
    assert turn.token_usage is not None
    assert turn.token_usage.total_tokens == 160
    assert len(turn.runtime_agents) == 1
    child = turn.runtime_agents[0]
    assert child.agent_id == "child-1"
    assert child.agent_type == "explorer"
    assert child.token_usage is not None
    assert child.token_usage.total_tokens == 40
