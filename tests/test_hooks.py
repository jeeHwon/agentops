from __future__ import annotations

import json

from codex_agentops.config import Settings, save_settings
from codex_agentops.hooks import handle_hook
from codex_agentops.manifest import create_agent
from codex_agentops.outbox import Outbox
from codex_agentops.paths import outbox_path


def test_hook_ignores_non_agent_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    result = handle_hook(
        {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t", "cwd": str(tmp_path)},
        launch_uploader=False,
    )
    assert result == {}
    assert not outbox_path().exists()


def test_hook_records_supported_event_in_agent_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(Settings("test-profile", "https://example.cloud.databricks.com"))
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


def test_stop_hook_attaches_exact_transcript_token_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(
        Settings("test-profile", "https://example.cloud.databricks.com", otel_mode="external")
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


def test_subagent_hooks_attach_child_usage_to_root_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    save_settings(
        Settings("test-profile", "https://example.cloud.databricks.com", otel_mode="external")
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
