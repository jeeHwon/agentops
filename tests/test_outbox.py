from __future__ import annotations

from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import create_agent, load_manifest
from codex_agentops.outbox import Outbox
from codex_agentops.usage import TokenUsage


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
    assert turn.token_usage_source == "otel"
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
