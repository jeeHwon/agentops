from __future__ import annotations

import json
import sqlite3

import pytest

from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import create_agent, load_manifest
from codex_agentops.outbox import Outbox, StoredEvent
from codex_agentops.usage import TokenUsage


def test_outbox_rejects_incompatible_local_schema(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO schema_meta VALUES ('schema_version', '2')")
    with pytest.raises(ValueError, match="incompatible"):
        Outbox(path)


def _event(root, name, now_ns, **fields):
    manifest = load_manifest(root)
    settings = Settings("test-profile", "https://example.cloud.databricks.com")
    return from_hook_payload(
        {
            "hook_event_name": name,
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            **fields,
        },
        manifest,
        settings,
        now_ns=now_ns,
    )


def test_outbox_deduplicates_events_and_claims_only_completed_turns(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    assert (tmp_path / "outbox.db").stat().st_mode & 0o777 == 0o600
    prompt = _event(root, "UserPromptSubmit", 1, prompt="hello")
    assert outbox.append(prompt) is True
    assert outbox.append(prompt) is False
    assert outbox.claim(now_ns=10) is None

    stop = _event(root, "Stop", 2, last_assistant_message="world")
    assert outbox.append(stop) is True
    turn = outbox.claim(now_ns=10)
    assert turn is not None
    assert [item.event_name for item in turn.events] == ["UserPromptSubmit", "Stop"]
    outbox.mark_uploaded(turn.turn_key, "tr-test")
    assert outbox.stats()["uploaded"] == 1
    assert outbox.uploaded_trace_id(turn.turn_key) == "tr-test"


def test_inventory_and_turn_include_started_tools_without_results(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    for name, observed_ns, details in (
        ("UserPromptSubmit", 100, {"prompt": "demo"}),
        ("PreToolUse", 110, {"tool_name": "Read", "tool_use_id": "unreturned"}),
        ("PreToolUse", 120, {"tool_name": "Bash"}),
        ("PostToolUse", 130, {"tool_name": "Bash", "tool_response": {"exit_code": 2}}),
        ("Stop", 140, {"last_assistant_message": "done"}),
    ):
        outbox.append(_event(root, name, observed_ns, **details))
    inventory = outbox.call_inventory("session-1")
    assert len(inventory) == 2
    assert inventory[0]["call_id"] == "unreturned"
    assert inventory[0]["result_observed"] is False
    assert inventory[1]["tool_name"] == "Bash"
    assert inventory[1]["id_observed"] is False
    assert inventory[1]["result_observed"] is True
    turn = outbox.claim()
    assert turn is not None
    from codex_agentops.call_metrics import observed_calls

    assert [(prior.event_name, post is not None) for prior, post in observed_calls(turn.events)] == [
        ("PreToolUse", False), ("PreToolUse", True),
    ]


def test_duplicate_start_for_same_tool_id_counts_once():
    from codex_agentops.call_metrics import observed_calls

    events = (
        StoredEvent("first", "PreToolUse", 100, {"tool_use_id": "call-1", "tool_name": "Bash"}),
        StoredEvent("retry", "PreToolUse", 110, {"tool_use_id": "call-1", "tool_name": "Bash"}),
        StoredEvent("finish", "PostToolUse", 120, {"tool_use_id": "call-1", "tool_name": "Bash"}),
        StoredEvent("late", "PreToolUse", 130, {"tool_use_id": "call-1", "tool_name": "Bash"}),
    )

    assert [(prior.event_id, post.event_id if post else None)
            for prior, post in observed_calls(events)] == [("first", "finish")]


def test_subagent_type_survives_tool_events_without_type(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    for name, observed_ns, fields in (
        ("UserPromptSubmit", 100, {"prompt": "demo"}),
        ("SubagentStart", 110, {"agent_id": "child-1", "agent_type": "explorer"}),
        ("PostToolUse", 120, {"agent_id": "child-1", "tool_use_id": "call-1",
                              "tool_name": "Bash"}),
        ("SubagentStop", 130, {"agent_id": "child-1"}),
        ("Stop", 140, {"last_assistant_message": "done"}),
    ):
        outbox.append(_event(root, name, observed_ns, **fields))
    turn = outbox.claim()
    assert turn is not None
    assert [agent.agent_type for agent in turn.runtime_agents] == ["explorer"]


def test_existing_outbox_preserves_observed_cache_during_column_migration(tmp_path):
    from codex_agentops.outbox import _usage_from_row
    from codex_agentops.usage import TokenUsage

    path = tmp_path / "outbox.db"
    outbox = Outbox(path)
    outbox.append_token_usage(
        event_id="synthetic-usage", session_id="synthetic-session", turn_id="synthetic-turn",
        observed_ns=100, source="transcript", require_agent_session=False,
        usage=TokenUsage.from_dict({"input_tokens": 10, "output_tokens": 2,
                                    "cached_input_tokens": 3, "cache_write_input_tokens": 4}),
    )
    with outbox._connect() as connection:
        connection.execute("ALTER TABLE token_usage_events DROP COLUMN cache_read_reported")
        connection.execute("ALTER TABLE token_usage_events DROP COLUMN cache_creation_reported")
    migrated = Outbox(path)
    with migrated._connect() as connection:
        row = connection.execute("SELECT * FROM token_usage_events").fetchone()
    usage = _usage_from_row(row)
    assert usage.cache_read_available is True
    assert usage.cache_creation_available is True


def test_transcript_call_deduplicates_hook_and_records_late_arrivals(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100))
    outbox.append(_event(root, "PostToolUse", 150,
                         tool_name="WebSearch", tool_use_id="hook-call"))
    outbox.append(_event(root, "Stop", 300))
    assert not outbox.append_transcript_websearch(
        session_id="session-1", turn_id="turn-1", call_id="hook-call", observed_ns=200
    )
    assert outbox.append_transcript_websearch(
        session_id="session-1", turn_id="turn-1", call_id="hosted-call", observed_ns=250
    )
    assert not outbox.append_transcript_websearch(
        session_id="session-1", turn_id="turn-1", call_id="hosted-call", observed_ns=250
    )
    turn = outbox.claim()
    assert [event.payload["tool_use_id"] for event in turn.events
            if event.event_name == "PostToolUse"] == ["hook-call", "hosted-call"]
    outbox.mark_uploaded(turn.turn_key, "tr-test")
    assert outbox.append_transcript_websearch(
        session_id="session-1", turn_id="turn-1", call_id="late-call", observed_ns=260
    )
    outbox.append(_event(root, "PostToolUse", 400,
                         tool_name="WebSearch", tool_use_id="hosted-call"))
    assert outbox.claim() is None
    inventory = outbox.call_inventory("session-1")
    assert [call["call_id"] for call in inventory] == ["hook-call", "hosted-call", "late-call"]
    assert inventory[1]["capture_source"] == "codex-transcript"
    assert inventory[1]["trace_id"] == "tr-test"
    assert inventory[2]["trace_linkage"] == "late"
    with outbox._connect() as connection:
        late = connection.execute(
            "SELECT payload_json FROM outbox_events WHERE turn_key = ? AND created_ns = 260",
            (turn.turn_key,),
        ).fetchone()
    assert json.loads(late["payload_json"])["agentops.trace_linkage"] == "late"


def test_transcript_child_call_attaches_to_parent_turn(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100))
    outbox.append(_event(root, "SubagentStart", 150, agent_id="child-1",
                         turn_id="child-turn", agent_type="explorer"))
    outbox.append(_event(root, "Stop", 300))
    assert outbox.append_transcript_websearch(
        session_id="child-1", turn_id="child-turn", call_id="child-call", observed_ns=200
    )
    assert not outbox.append_transcript_websearch(
        session_id="unknown", turn_id="child-turn", call_id="stranger", observed_ns=200
    )
    turn = outbox.claim()
    child = next(event for event in turn.events if event.event_name == "PostToolUse")
    assert child.payload["codex_agent_id"] == "child-1"


def test_late_hook_replaces_pending_transcript_call(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100))
    assert outbox.append_transcript_websearch(
        session_id="session-1", turn_id="turn-1", call_id="search-1", observed_ns=150
    )
    outbox.append(_event(root, "PostToolUse", 160, tool_name="WebSearch",
                         tool_use_id="search-1"))
    outbox.append(_event(root, "Stop", 300))
    turn = outbox.claim()
    calls = [event for event in turn.events if event.event_name == "PostToolUse"]
    assert len(calls) == 1
    assert calls[0].payload.get("agentops.capture_source") is None


def test_outbox_prefers_otel_usage_and_falls_back_to_transcript(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100, prompt="hello"))
    outbox.append(_event(root, "Stop", 300, last_assistant_message="world"))
    outbox.append_token_usage(
        event_id="transcript",
        session_id="session-1",
        turn_id="turn-1",
        observed_ns=300,
        source="transcript",
        usage=TokenUsage(90, 10, total_tokens=100),
    )
    outbox.append_token_usage(
        event_id="otel",
        session_id="session-1",
        observed_ns=200,
        source="otel",
        usage=TokenUsage(90, 10, total_tokens=100),
    )
    turn = outbox.claim(now_ns=400, require_usage=True)
    assert turn is not None
    assert turn.token_usage_source == "otel+transcript"
    assert turn.token_usage is not None
    assert turn.token_usage.total_tokens == 100


def test_outbox_associates_subagent_with_root_and_aggregates_usage(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100, prompt="delegate"))
    outbox.append(
        _event(
            root,
            "SubagentStart",
            120,
            turn_id="child-turn",
            agent_id="child-1",
            agent_type="explorer",
        )
    )
    outbox.append(
        _event(
            root,
            "PostToolUse",
            150,
            turn_id="child-turn",
            agent_id="child-1",
            agent_type="explorer",
            tool_name="Bash",
            tool_use_id="child-tool",
            tool_input={"command": "pwd"},
            tool_response={"output": "/tmp", "exit_code": 0},
        )
    )
    child_stop = _event(
        root,
        "SubagentStop",
        180,
        turn_id="child-turn",
        agent_id="child-1",
        agent_type="explorer",
        last_assistant_message="found it",
    )
    outbox.append(child_stop)
    outbox.append(_event(root, "Stop", 200, last_assistant_message="done"))
    assert outbox.append(child_stop) is False
    outbox.append_token_usage(
        event_id="root-usage",
        session_id="session-1",
        turn_id="turn-1",
        observed_ns=190,
        source="transcript",
        usage=TokenUsage(100, 20, total_tokens=120),
    )
    outbox.append_token_usage(
        event_id="child-usage",
        session_id="child-1",
        turn_id="child-turn",
        observed_ns=175,
        source="transcript",
        usage=TokenUsage(30, 10, total_tokens=40),
    )

    turn = outbox.claim(now_ns=300, require_usage=True)
    assert turn is not None
    assert turn.turn_id == "turn-1"
    assert len(turn.runtime_agents) == 1
    assert turn.runtime_agents[0].agent_id == "child-1"
    assert turn.runtime_agents[0].turn_id == "child-turn"
    assert turn.runtime_agents[0].token_usage is not None
    assert turn.runtime_agents[0].token_usage.total_tokens == 40
    assert turn.token_usage is not None
    assert turn.token_usage.total_tokens == 160
    assert outbox.stats()["open"] == 0


def test_partial_otel_does_not_override_transcript_and_missing_usage_settles(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.append(_event(root, "UserPromptSubmit", 100, prompt="hello"))
    outbox.append(_event(root, "Stop", 300, last_assistant_message="done"))
    outbox.append_token_usage(
        event_id="transcript", session_id="session-1", turn_id="turn-1",
        observed_ns=300, source="transcript", usage=TokenUsage(90, 10, total_tokens=100),
    )
    outbox.append_token_usage(
        event_id="otel", session_id="session-1", observed_ns=200,
        source="otel", usage=TokenUsage(4, 2, total_tokens=6),
    )
    turn = outbox.claim(now_ns=400, require_usage=True)
    assert turn.token_usage.total_tokens == 100
    assert turn.token_usage_source == "transcript-otel-partial"

    second = Outbox(tmp_path / "missing.db")
    second.append(_event(root, "Stop", 300))
    assert second.claim(now_ns=400, require_usage=True) is None
    assert second.claim(now_ns=300 + 60_000_000_000, require_usage=True).token_usage is None
