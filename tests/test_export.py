from __future__ import annotations

import time

from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import create_agent, load_manifest
from codex_agentops.mlflow_exporter import MlflowTurnExporter, export_pending
from codex_agentops.outbox import Outbox
from codex_agentops.usage import TokenUsage


def _complete_turn(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    manifest = load_manifest(root)
    settings = Settings("test-profile", "https://example.cloud.databricks.com")
    outbox = Outbox(tmp_path / "outbox.db")
    start_ns = time.time_ns()
    for index, payload in enumerate(
        [
            {"hook_event_name": "UserPromptSubmit", "prompt": "hello"},
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "pwd"},
                "tool_response": {"output": "/tmp", "exit_code": 0},
            },
            {"hook_event_name": "Stop", "last_assistant_message": "done"},
        ],
        start=1,
    ):
        event = from_hook_payload(
            {"session_id": "s", "turn_id": "t", "cwd": str(root), **payload},
            manifest,
            settings,
            now_ns=start_ns + index * 1_000_000,
        )
        outbox.append(event)
    return outbox, settings


def _complete_turn_with_subagent(tmp_path):
    root = create_agent("test-agent", tmp_path / "agent")
    manifest = load_manifest(root)
    settings = Settings("test-profile", "https://example.cloud.databricks.com")
    outbox = Outbox(tmp_path / "outbox.db")
    start_ns = 1_000_000_000
    payloads = [
        {"hook_event_name": "UserPromptSubmit", "turn_id": "root-turn", "prompt": "delegate"},
        {
            "hook_event_name": "SubagentStart",
            "turn_id": "child-turn",
            "agent_id": "child-1",
            "agent_type": "explorer",
        },
        {
            "hook_event_name": "PostToolUse",
            "turn_id": "child-turn",
            "agent_id": "child-1",
            "agent_type": "explorer",
            "tool_name": "Bash",
            "tool_use_id": "child-tool",
            "tool_input": {"command": "pwd"},
            "tool_response": {"output": "/tmp", "exit_code": 0},
        },
        {
            "hook_event_name": "SubagentStop",
            "turn_id": "child-turn",
            "agent_id": "child-1",
            "agent_type": "explorer",
            "last_assistant_message": "found it",
        },
        {
            "hook_event_name": "PostToolUse",
            "turn_id": "root-turn",
            "tool_name": "Agent",
            "tool_use_id": "agent-tool",
            "tool_input": {"agent_type": "explorer"},
            "tool_response": {"agent_id": "child-1", "status": "completed"},
        },
        {
            "hook_event_name": "Stop",
            "turn_id": "root-turn",
            "last_assistant_message": "done",
        },
    ]
    for index, payload in enumerate(payloads):
        outbox.append(
            from_hook_payload(
                {"session_id": "root-session", "cwd": str(root), **payload},
                manifest,
                settings,
                now_ns=start_ns + index * 100_000_000,
            )
        )
    outbox.append_token_usage(
        event_id="root-usage",
        session_id="root-session",
        turn_id="root-turn",
        observed_ns=start_ns + 490_000_000,
        source="transcript",
        usage=TokenUsage(100, 20, total_tokens=120),
    )
    outbox.append_token_usage(
        event_id="child-usage",
        session_id="child-1",
        turn_id="child-turn",
        observed_ns=start_ns + 290_000_000,
        source="transcript",
        usage=TokenUsage(30, 10, total_tokens=40),
    )
    outbox.append_tool_observation(
        event_id="child-tool-observation",
        conversation_id="child-1",
        call_id="child-tool",
        tool_name="shell",
        observed_ns=start_ns + 250_000_000,
        duration_ms=25.0,
        success=True,
    )
    outbox.append_tool_observation(
        event_id="agent-tool-observation",
        conversation_id="root-session",
        call_id="agent-tool",
        tool_name="collaboration.spawn_agent",
        observed_ns=start_ns + 410_000_000,
        duration_ms=300.0,
        success=True,
    )
    return outbox, settings


def test_export_marks_turn_uploaded(tmp_path):
    outbox, settings = _complete_turn(tmp_path)

    class FakeExporter:
        def __init__(self, _settings):
            pass

        def export_turn(self, turn):
            assert len(turn.events) == 3
            return "tr-123"

    result = export_pending(outbox, settings, exporter_factory=FakeExporter, sleep=lambda _: None)
    assert result.uploaded == 1
    assert result.failed == 0
    assert outbox.stats()["uploaded"] == 1


def test_export_failure_stays_retryable(tmp_path):
    outbox, settings = _complete_turn(tmp_path)

    class FailingExporter:
        def __init__(self, _settings):
            pass

        def export_turn(self, turn):
            raise RuntimeError("workspace unavailable")

    result = export_pending(outbox, settings, exporter_factory=FailingExporter, sleep=lambda _: None)
    assert result.failed == 1
    assert outbox.stats()["failed"] == 1


def test_real_mlflow_trace_contains_root_io_and_tool_span(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    outbox, settings = _complete_turn(tmp_path)
    outbox.append_token_usage(
        event_id="usage-1",
        session_id="s",
        turn_id="t",
        observed_ns=time.time_ns(),
        source="transcript",
        usage=TokenUsage(
            input_tokens=100,
            output_tokens=20,
            cached_input_tokens=40,
            cache_write_input_tokens=10,
            reasoning_output_tokens=5,
            total_tokens=120,
        ),
    )
    turn = outbox.claim(now_ns=10)
    assert turn is not None
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("integration-test")
    exporter = MlflowTurnExporter(settings)
    monkeypatch.setattr(exporter, "_client_and_experiment", lambda: (client, experiment_id))

    trace_id = exporter.export_turn(turn)
    trace = client.get_trace(trace_id)
    spans = trace.data.spans
    assert len(spans) == 2
    root = next(span for span in spans if span.name == "agent.turn")
    tool = next(span for span in spans if span.name == "tool.Bash")
    assert root.inputs == {"prompt": "hello"}
    assert root.outputs["response"] == "done"
    assert tool.inputs == {"command": "pwd"}
    assert tool.outputs["exit_code"] == 0
    assert trace.info.token_usage == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 10,
    }


def test_real_mlflow_trace_nests_subagent_tools_and_aggregates_tokens(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    outbox, settings = _complete_turn_with_subagent(tmp_path)
    turn = outbox.claim(now_ns=2_000_000_000, require_usage=True)
    assert turn is not None
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("subagent-integration-test")
    exporter = MlflowTurnExporter(settings)
    monkeypatch.setattr(exporter, "_client_and_experiment", lambda: (client, experiment_id))

    trace_id = exporter.export_turn(turn)
    trace = client.get_trace(trace_id)
    spans = trace.data.spans
    assert len(spans) == 4
    root = next(span for span in spans if span.name == "agent.turn")
    agent_tool = next(span for span in spans if span.name == "tool.Agent")
    subagent = next(span for span in spans if span.name == "agent.subagent.explorer")
    child_tool = next(span for span in spans if span.name == "tool.Bash")
    assert agent_tool.parent_id == root.span_id
    assert subagent.parent_id == root.span_id
    assert child_tool.parent_id == subagent.span_id
    assert subagent.outputs == {"response": "found it"}
    assert subagent.attributes["agentops.tokens.total"] == 40
    assert child_tool.attributes["agentops.duration.available"] is True
    assert child_tool.attributes["agentops.duration.ms"] == 25.0
    assert child_tool.end_time_ns - child_tool.start_time_ns == 25_000_000
    assert trace.info.token_usage["total_tokens"] == 160


def test_configuration_trace_is_flushed_and_readable(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("configuration-test")
    settings = Settings("test-profile", "https://example.cloud.databricks.com")
    exporter = MlflowTurnExporter(settings)
    monkeypatch.setattr(
        exporter,
        "_client_and_experiment",
        lambda *, create_if_missing=False: (client, experiment_id),
    )

    trace_id = exporter.write_test_trace()
    trace = client.get_trace(trace_id)
    assert trace.data.spans[0].name == "codex-agentops.configuration-test"


def test_configuration_creates_missing_experiment(monkeypatch):
    import mlflow

    class Experiment:
        experiment_id = "created-experiment"

    class FakeClient:
        def __init__(self, **_kwargs):
            self.experiment = None
            self.created_names = []

        def get_experiment_by_name(self, _name):
            return self.experiment

        def create_experiment(self, name):
            self.created_names.append(name)
            self.experiment = Experiment()
            return self.experiment.experiment_id

    client = FakeClient()
    selected = []
    monkeypatch.setattr(mlflow, "MlflowClient", lambda **_kwargs: client)
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda **kwargs: selected.append(kwargs))
    exporter = MlflowTurnExporter(
        Settings(
            "test-profile",
            "https://example.cloud.databricks.com",
            experiment="/Shared/codex-agentops",
        )
    )

    returned_client, experiment_id = exporter._client_and_experiment(create_if_missing=True)

    assert returned_client is client
    assert experiment_id == "created-experiment"
    assert client.created_names == ["/Shared/codex-agentops"]
    assert selected == [{"experiment_id": "created-experiment"}]


def test_configuration_reuses_existing_experiment(monkeypatch):
    import mlflow

    class Experiment:
        experiment_id = "existing-experiment"

    class FakeClient:
        def get_experiment_by_name(self, _name):
            return Experiment()

        def create_experiment(self, _name):
            raise AssertionError("기존 Experiment를 다시 생성하면 안 됩니다")

    client = FakeClient()
    monkeypatch.setattr(mlflow, "MlflowClient", lambda **_kwargs: client)
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda **_kwargs: None)
    exporter = MlflowTurnExporter(Settings("test-profile", "https://example.com"))

    _, experiment_id = exporter._client_and_experiment(create_if_missing=True)

    assert experiment_id == "existing-experiment"


def test_runtime_does_not_create_missing_experiment(monkeypatch):
    import mlflow
    import pytest

    class FakeClient:
        def get_experiment_by_name(self, _name):
            return None

        def create_experiment(self, _name):
            raise AssertionError("Runtime 조회에서 Experiment를 생성하면 안 됩니다")

    monkeypatch.setattr(mlflow, "MlflowClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    exporter = MlflowTurnExporter(Settings("test-profile", "https://example.com"))

    with pytest.raises(RuntimeError, match="aops configure"):
        exporter._client_and_experiment()


def test_configuration_recovers_from_concurrent_experiment_creation(monkeypatch):
    import mlflow

    class Experiment:
        experiment_id = "concurrently-created"

    class FakeClient:
        def __init__(self):
            self.lookups = 0

        def get_experiment_by_name(self, _name):
            self.lookups += 1
            return None if self.lookups == 1 else Experiment()

        def create_experiment(self, _name):
            raise RuntimeError("RESOURCE_ALREADY_EXISTS")

    client = FakeClient()
    monkeypatch.setattr(mlflow, "MlflowClient", lambda **_kwargs: client)
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda **_kwargs: None)
    exporter = MlflowTurnExporter(Settings("test-profile", "https://example.com"))

    _, experiment_id = exporter._client_and_experiment(create_if_missing=True)

    assert experiment_id == "concurrently-created"


def test_configuration_reports_experiment_creation_permission_failure(monkeypatch):
    import mlflow
    import pytest

    class FakeClient:
        def get_experiment_by_name(self, _name):
            return None

        def create_experiment(self, _name):
            raise RuntimeError("PERMISSION_DENIED")

    monkeypatch.setattr(mlflow, "MlflowClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda _uri: None)
    exporter = MlflowTurnExporter(Settings("test-profile", "https://example.com"))

    with pytest.raises(RuntimeError, match="Experiment 생성 권한") as error:
        exporter._client_and_experiment(create_if_missing=True)

    assert "PERMISSION_DENIED" in str(error.value)
