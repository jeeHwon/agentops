from __future__ import annotations

import json

from codex_agentops.collector import ingest_otlp_logs, parse_otlp_logs, parse_otlp_tool_results
from codex_agentops.outbox import Outbox
from codex_agentops.usage import parse_transcript_usage, parse_transcript_usages


def _otlp_payload(session_id: str = "session-1"):
    def attr(key, value):
        kind = "intValue" if isinstance(value, int) else "stringValue"
        return {"key": key, "value": {kind: str(value) if kind == "intValue" else value}}

    values = {
        "event.name": "codex.sse_event",
        "event.kind": "response.completed",
        "conversation.id": session_id,
        "model": "gpt-test",
        "input_token_count": 100,
        "output_token_count": 20,
        "cached_token_count": 30,
        "cache_write_token_count": 40,
        "reasoning_token_count": 5,
    }
    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "200",
                                "traceId": "trace-1",
                                "spanId": "span-1",
                                "attributes": [attr(key, value) for key, value in values.items()],
                            }
                        ]
                    }
                ]
            }
        ]
    }


def _tool_otlp_payload(session_id: str = "session-1"):
    def attr(key, value):
        if isinstance(value, bool):
            kind = "boolValue"
        elif isinstance(value, (int, float)):
            kind = "doubleValue" if isinstance(value, float) else "intValue"
        else:
            kind = "stringValue"
        encoded = str(value) if kind == "intValue" else value
        return {"key": key, "value": {kind: encoded}}

    values = {
        "event.name": "codex.tool_result",
        "conversation.id": session_id,
        "call_id": "call-1",
        "tool_name": "shell",
        "duration_ms": 25.5,
        "success": True,
        "tool_result_seq": 1,
    }
    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "300000000",
                                "attributes": [attr(key, value) for key, value in values.items()],
                            }
                        ]
                    }
                ]
            }
        ]
    }


def test_parses_exact_otel_token_fields():
    event = parse_otlp_logs(_otlp_payload())[0]
    assert event.session_id == "session-1"
    assert event.usage.input_tokens == 100
    assert event.usage.output_tokens == 20
    assert event.usage.cached_input_tokens == 30
    assert event.usage.cache_write_input_tokens == 40
    assert event.usage.reasoning_output_tokens == 5
    assert event.usage.total_tokens == 120


def test_parses_exact_otel_tool_result_fields():
    event = parse_otlp_tool_results(_tool_otlp_payload())[0]
    assert event.conversation_id == "session-1"
    assert event.call_id == "call-1"
    assert event.tool_name == "shell"
    assert event.duration_ms == 25.5
    assert event.success is True
    assert event.observed_ns == 300_000_000


def test_otel_ingest_only_accepts_registered_agent_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    outbox = Outbox(tmp_path / "outbox.db")
    assert ingest_otlp_logs(_otlp_payload(), outbox) == 0
    outbox.record_session(
        session_id="session-1", agent_id="agent", agent_root=str(tmp_path), observed_ns=1
    )
    assert ingest_otlp_logs(_otlp_payload(), outbox) == 1
    assert ingest_otlp_logs(_otlp_payload(), outbox) == 0
    assert outbox.stats()["token_events"] == 1


def test_otel_tool_result_accepts_registered_runtime_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))
    outbox = Outbox(tmp_path / "outbox.db")
    assert ingest_otlp_logs(_tool_otlp_payload("child-1"), outbox) == 0
    root = tmp_path / "agent"
    root.mkdir()
    from codex_agentops.config import Settings
    from codex_agentops.events import from_hook_payload
    from codex_agentops.manifest import create_agent, load_manifest

    create_agent("test-agent", root)
    manifest = load_manifest(root)
    settings = Settings("test-profile", "https://example.cloud.databricks.com")
    for index, payload in enumerate(
        [
            {"hook_event_name": "UserPromptSubmit", "turn_id": "root-turn", "prompt": "go"},
            {
                "hook_event_name": "SubagentStart",
                "turn_id": "child-turn",
                "agent_id": "child-1",
                "agent_type": "explorer",
            },
        ],
        start=1,
    ):
        outbox.append(
            from_hook_payload(
                {"session_id": "root-1", "cwd": str(root), **payload},
                manifest,
                settings,
                now_ns=index,
            )
        )
    assert ingest_otlp_logs(_tool_otlp_payload("child-1"), outbox) == 1
    assert ingest_otlp_logs(_tool_otlp_payload("child-1"), outbox) == 0


def test_reads_only_matching_transcript_token_record(tmp_path):
    target = tmp_path / "rollout.jsonl"
    rows = [
        {"type": "response_item", "payload": {"content": "must not be parsed"}},
        {
            "type": "token_usage_record",
            "payload": {
                "session_id": "session-1",
                "turn_id": "turn-1",
                "turn_token_usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cached_input_tokens": 3,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 18,
                },
            },
        },
    ]
    target.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    usage = parse_transcript_usage(target, session_id="session-1", turn_id="turn-1")
    assert usage is not None
    assert usage.total_tokens == 18
    assert parse_transcript_usage(target, session_id="session-1", turn_id="other") is None
    assert set(parse_transcript_usages(target, session_id="session-1")) == {"turn-1"}


def test_reads_subagent_usage_by_thread_id(tmp_path):
    target = tmp_path / "child-rollout.jsonl"
    target.write_text(
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

    usage = parse_transcript_usage(target, session_id="child-1", turn_id="child-turn")
    assert usage is not None
    assert usage.total_tokens == 40
