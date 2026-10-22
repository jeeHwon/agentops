from __future__ import annotations

import time
from dataclasses import replace

import mlflow
from mlflow import MlflowClient

from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import create_agent, load_manifest
from codex_agentops.mlflow_exporter import MlflowTurnExporter
from codex_agentops.outbox import Outbox, StoredEvent
from codex_agentops.session_exporter import (
    MlflowSessionExporter, _metrics, _run_summary, export_pending_sessions,
)
from codex_agentops.usage import TokenUsage


def _local_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    return client, client.create_experiment("session-tracing")


def _events(tmp_path, *, capture_content=False, consent=False, summary=None):
    root = create_agent("metrics-agent", tmp_path / "agent")
    manifest = load_manifest(root)
    settings = Settings("test-profile", "https://example.cloud.databricks.com",
                        capture_content=capture_content, content_capture_consent=consent)
    outbox = Outbox(tmp_path / "outbox.db")
    started_ns = time.time_ns() - 3_000_000_000
    outbox.record_session(session_id="synthetic-session", agent_id=manifest.agent_id,
                          agent_root=str(root), observed_ns=started_ns)
    payloads = [
        {"hook_event_name": "UserPromptSubmit", "turn_id": "turn-one",
         "prompt": "user@example.com first request"},
        {"hook_event_name": "SubagentStart", "turn_id": "child-turn", "agent_id": "child-1",
         "agent_type": "explorer"},
        {"hook_event_name": "PreToolUse", "turn_id": "child-turn", "agent_id": "child-1",
         "tool_name": "Bash", "tool_use_id": "call-one", "tool_input": {"command": "pwd"}},
        {"hook_event_name": "PostToolUse", "turn_id": "child-turn", "agent_id": "child-1",
         "tool_name": "Bash", "tool_use_id": "call-one",
         "tool_response": {"output": "failed", "exit_code": 2}},
        {"hook_event_name": "SubagentStop", "turn_id": "child-turn", "agent_id": "child-1",
         "agent_type": "explorer", "last_assistant_message": "child finished"},
        {"hook_event_name": "PreToolUse", "turn_id": "turn-one", "tool_name": "Read",
         "tool_use_id": "call-two", "tool_input": {"path": "demo.txt"}},
        {"hook_event_name": "Stop", "turn_id": "turn-one", "last_assistant_message": "first answer"},
        {"hook_event_name": "UserPromptSubmit", "turn_id": "turn-two", "prompt": "second"},
        {"hook_event_name": "Stop", "turn_id": "turn-two", "last_assistant_message": "second answer"},
    ]
    for index, payload in enumerate(payloads, start=1):
        outbox.append(from_hook_payload(
            {"session_id": "synthetic-session", "cwd": str(root), **payload},
            manifest, settings, now_ns=started_ns + index * 20_000_000,
        ))
    outbox.append_token_usage(
        event_id="root-usage", session_id="synthetic-session", turn_id="turn-one",
        observed_ns=started_ns + 115_000_000, source="transcript",
        usage=TokenUsage.from_dict({"input_tokens": 100, "output_tokens": 20,
                                    "cached_input_tokens": 10, "cache_write_input_tokens": 0,
                                    "total_tokens": 120}),
    )
    outbox.append_token_usage(
        event_id="child-usage", session_id="child-1", turn_id="child-turn",
        observed_ns=started_ns + 85_000_000, source="transcript",
        usage=TokenUsage.from_dict({"input_tokens": 30, "output_tokens": 10,
                                    "total_tokens": 40}),
    )
    if summary is not None:
        (root / "run_summary.json").write_text(summary, encoding="utf-8")
    custom_metrics, custom_tags = _run_summary(str(root), capture_content=consent)
    outbox.record_session(session_id="synthetic-session", agent_id=manifest.agent_id,
                          agent_root=str(root), observed_ns=started_ns + 200_000_000,
                          ended=True, end_reason="other", custom_metrics=custom_metrics,
                          custom_tags=custom_tags)
    return root, outbox, settings


def _export_turns(outbox, settings, client, experiment_id):
    exporter = MlflowTurnExporter(settings)
    for _ in range(2):
        turn = outbox.claim()
        assert turn is not None
        trace_id = exporter._export_turn(turn, client, experiment_id)
        outbox.mark_uploaded(turn.turn_key, trace_id)
    assert outbox.claim() is None


def test_session_tree_metrics_retrieval_and_no_double_count(tmp_path, monkeypatch):
    root, outbox, settings = _events(
        tmp_path, capture_content=True, consent=True,
        summary='{"checks_passed": 2, "note": "user@example.com", "enabled": false}',
    )
    (root / "run_summary.json").write_text('{"checks_passed": 999}', encoding="utf-8")
    client, experiment_id = _local_client(tmp_path, monkeypatch)
    assert outbox.claim_session() is None
    _export_turns(outbox, settings, client, experiment_id)
    exporter = MlflowSessionExporter(settings)
    monkeypatch.setattr(exporter.turn_exporter, "_client_and_experiment",
                        lambda: (client, experiment_id))
    result = export_pending_sessions(outbox, settings, exporter_factory=lambda _: exporter)

    assert result.uploaded == 1 and result.failed == 0
    assert outbox.session_trace_id("synthetic-session") == result.trace_ids[0]
    assert export_pending_sessions(outbox, settings, exporter_factory=lambda _: exporter).uploaded == 0
    trace = client.get_trace(result.trace_ids[0])
    assert trace.info.tags["agentops.trace_role"] == "session_summary"
    assert trace.info.tags["agentops.end_reason"] == "other"
    assert trace.info.tags["agentops.project"] == "metrics-agent"
    assert trace.info.tags["agentops.session_id"] == "synthetic-session"
    assert trace.info.trace_metadata["mlflow.trace.session"] == "synthetic-session"
    assert trace.info.tags["agentops.source"] == "codex-plugin-hooks"
    assert trace.info.tags["agentops.git_commit"] == "unknown"
    assert trace.info.tags["agentops.subagent_defs"] == "[]"
    assert trace.info.tags["custom.note"] == "[REDACTED_EMAIL]"
    assert trace.info.token_usage is None
    assert trace.info.status == "ERROR"
    spans = trace.data.spans
    assert len(spans) == 6
    session = next(span for span in spans if span.name == "agent.session")
    turns = [span for span in spans if span.name.startswith("turn.")]
    child = next(span for span in spans if span.name == "agent.subagent.explorer")
    failed_tool = next(span for span in spans if span.name == "tool.Bash")
    unfinished = next(span for span in spans if span.name == "tool.Read")
    assert session.inputs == {"prompt": "[REDACTED_EMAIL] first request"}
    assert session.outputs == {"response": "second answer"}
    assert len(turns) == 2 and all(turn.parent_id == session.span_id for turn in turns)
    assert turns[0].inputs == {"prompt": "[REDACTED_EMAIL] first request"}
    assert turns[0].outputs == {"response": "first answer"}
    assert turns[1].inputs == {"prompt": "second"}
    assert turns[1].outputs == {"response": "second answer"}
    assert child.parent_id == turns[0].span_id
    assert child.outputs == {"response": "child finished"}
    assert failed_tool.parent_id == child.span_id
    assert failed_tool.status.status_code == "ERROR"
    assert failed_tool.inputs == {"command": "pwd"}
    assert failed_tool.outputs == {"output": "failed", "exit_code": 2}
    assert failed_tool.attributes["agentops.is_error"] is True
    assert failed_tool.attributes["agentops.tool_result_observed"] is True
    assert unfinished.parent_id == turns[0].span_id
    assert unfinished.attributes["agentops.tool_result_observed"] is False
    assert unfinished.attributes["agentops.result_status"] == "unobserved"
    attributes = session.attributes
    for key, expected in {
        "turns_user": 2, "turns_assistant": 2, "tool_calls": 2, "tool_errors": 1,
        "subagent_calls": 1, "tool_calls_unfinished": 1, "toolcall.Bash": 1,
        "toolcall.Read": 1, "subagent.explorer": 1, "observed_input_tokens": 130,
        "observed_output_tokens": 30, "observed_total_tokens": 160,
        "observed_cache_read_tokens": 10, "custom.checks_passed": 2,
    }.items():
        assert attributes[f"agentops.metrics.{key}"] == expected
    assert "agentops.metrics.input_tokens" not in attributes
    assert "agentops.metrics.cache_creation_tokens" not in attributes
    assert attributes["agentops.token_usage.complete"] is False
    assert attributes["agentops.token_usage.cache_read_available"] is False
    assert attributes["agentops.token_usage.cache_creation_available"] is False
    assert attributes["agentops.metrics.session_duration_sec"] == 0.2
    assert turns[0].attributes["agentops.tokens.total"] == 160
    assert turns[1].attributes["agentops.token_usage.available"] is False
    assert len(client.search_traces([experiment_id])) == 3


def test_no_content_session_does_not_leak_summary_tags(tmp_path, monkeypatch):
    _, outbox, settings = _events(tmp_path, summary='{"note": "user@example.com"}')
    client, experiment_id = _local_client(tmp_path, monkeypatch)
    _export_turns(outbox, settings, client, experiment_id)
    exporter = MlflowSessionExporter(settings)
    monkeypatch.setattr(exporter.turn_exporter, "_client_and_experiment",
                        lambda: (client, experiment_id))

    trace = client.get_trace(exporter.export_session(outbox.claim_session()))
    assert "custom.note" not in trace.info.tags
    assert trace.data.spans[0].inputs == {"prompt": None}
    assert all(span.inputs in (None, {"prompt": None}, {"agent_id": "child-1",
                      "agent_type": "explorer"}) or span.name == "tool.Read"
               for span in trace.data.spans)


def test_session_without_prompt_marks_static_agent_definitions_unknown(tmp_path, monkeypatch):
    root = create_agent("metrics-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.record_session(session_id="synthetic-session", agent_id="metrics-agent",
                          agent_root=str(root), ended=True, end_reason="other")
    client, experiment_id = _local_client(tmp_path, monkeypatch)
    exporter = MlflowSessionExporter(
        Settings("test-profile", "https://example.cloud.databricks.com")
    )
    monkeypatch.setattr(exporter.turn_exporter, "_client_and_experiment",
                        lambda: (client, experiment_id))
    session = outbox.claim_session()
    assert session is not None
    trace = client.get_trace(exporter.export_session(session))
    assert trace.info.tags["agentops.subagent_defs"] == "unknown"
    assert trace.data.spans[0].attributes["agentops.metrics.turns_user"] == 0


def test_complete_session_preserves_reported_zero_cache(tmp_path, monkeypatch):
    _, outbox, settings = _events(tmp_path)
    outbox.append_token_usage(
        event_id="child-cache-usage", session_id="child-1", turn_id="child-turn",
        observed_ns=time.time_ns(), source="transcript",
        usage=TokenUsage.from_dict({"input_tokens": 30, "output_tokens": 10,
                                    "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                    "total_tokens": 40}),
    )
    outbox.append_token_usage(
        event_id="second-usage", session_id="synthetic-session", turn_id="turn-two",
        observed_ns=time.time_ns(), source="transcript",
        usage=TokenUsage.from_dict({"input_tokens": 0, "output_tokens": 0,
                                    "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                    "total_tokens": 0}),
    )
    client, experiment_id = _local_client(tmp_path, monkeypatch)
    _export_turns(outbox, settings, client, experiment_id)
    exporter = MlflowSessionExporter(settings)
    monkeypatch.setattr(exporter.turn_exporter, "_client_and_experiment",
                        lambda: (client, experiment_id))

    trace = client.get_trace(exporter.export_session(outbox.claim_session()))
    session = next(span for span in trace.data.spans if span.name == "agent.session")
    attributes = session.attributes
    assert attributes["agentops.token_usage.complete"] is True
    assert attributes["agentops.metrics.input_tokens"] == 130
    assert attributes["agentops.metrics.output_tokens"] == 30
    assert attributes["agentops.metrics.total_tokens"] == 160
    assert attributes["agentops.metrics.cache_read_tokens"] == 10
    assert attributes["agentops.metrics.cache_creation_tokens"] == 0
    assert "agentops.metrics.observed_total_tokens" not in attributes
    assert trace.info.token_usage is None


def test_unique_turn_counts_and_inconsistent_source_total(tmp_path, monkeypatch):
    _, outbox, settings = _events(tmp_path)
    client, experiment_id = _local_client(tmp_path, monkeypatch)
    _export_turns(outbox, settings, client, experiment_id)
    session = outbox.claim_session()
    assert session is not None
    first = session.turns[0]
    duplicate_events = (
        StoredEvent("extra-prompt", "UserPromptSubmit", first.events[-1].created_ns + 1, {}),
        StoredEvent("extra-stop", "Stop", first.events[-1].created_ns + 2, {}),
    )
    mismatched = replace(first, events=first.events + duplicate_events,
                         token_usage=replace(first.token_usage, total_tokens=999))
    metrics = _metrics(replace(session, turns=(mismatched, *session.turns[1:])))
    assert metrics["agentops.metrics.turns_user"] == 2
    assert metrics["agentops.metrics.turns_assistant"] == 2
    assert metrics["agentops.metrics.observed_input_tokens"] == 130
    assert metrics["agentops.metrics.observed_output_tokens"] == 30
    assert metrics["agentops.metrics.observed_total_tokens"] == 160
    assert metrics["agentops.metrics.reported_total_tokens"] == 999
    assert metrics["agentops.token_usage.total_consistent"] is False


def test_session_failed_export_is_retryable(tmp_path):
    root = create_agent("metrics-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    outbox.record_session(session_id="synthetic-session", agent_id="metrics-agent",
                          agent_root=str(root), ended=True, end_reason="other")
    settings = Settings("test-profile", "https://example.cloud.databricks.com")

    class BrokenExporter:
        def export_session(self, _session):
            raise RuntimeError("synthetic failure")

    result = export_pending_sessions(outbox, settings,
                                     exporter_factory=lambda _: BrokenExporter())
    assert result.failed == 1
    assert outbox.stats()["session_failed"] == 1
    assert outbox.claim_session() is None
    outbox.requeue_failed()

    class WorkingExporter:
        def export_session(self, _session):
            return "tr-synthetic"

    result = export_pending_sessions(outbox, settings,
                                     exporter_factory=lambda _: WorkingExporter())
    assert result.uploaded == 1
    assert outbox.session_trace_id("synthetic-session") == "tr-synthetic"
