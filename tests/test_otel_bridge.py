from __future__ import annotations

from codex_agentops.config import Settings
from codex_agentops.mlflow_exporter import MlflowTurnExporter
from codex_agentops.otel_bridge import export_otlp_traces, parse_otlp_traces


def _payload() -> bytes:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    resource = request.resource_spans.add()
    for key, value in {
        "agentops.agent_id": "sample-agent",
        "agentops.agent_revision": "1.0.0",
        "agentops.harness_checksum": "harness-hash",
        "agentops.skills_checksum": "skills-hash",
    }.items():
        attribute = resource.resource.attributes.add()
        attribute.key = key
        attribute.value.string_value = value
    spans = resource.scope_spans.add().spans
    trace_id = bytes.fromhex("11" * 16)
    root = spans.add(
        trace_id=trace_id,
        span_id=bytes.fromhex("22" * 8),
        name="agent:sample-agent",
        start_time_unix_nano=1_000_000_000,
        end_time_unix_nano=2_000_000_000,
    )
    root.status.code = 1
    root_attrs = {
        "openinference.span.kind": "AGENT",
        "agent.name": "sample-agent",
        "session.id": "conv-1",
        "input.value": "hello",
        "output.value": "done",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 4,
    }
    for key, value in root_attrs.items():
        attribute = root.attributes.add()
        attribute.key = key
        if isinstance(value, int):
            attribute.value.int_value = value
        else:
            attribute.value.string_value = value
    tool = spans.add(
        trace_id=trace_id,
        span_id=bytes.fromhex("33" * 8),
        parent_span_id=root.span_id,
        name="tool:search",
        start_time_unix_nano=1_200_000_000,
        end_time_unix_nano=1_500_000_000,
    )
    tool.status.code = 1
    for key, value in {
        "openinference.span.kind": "TOOL",
        "tool.name": "search",
        "input.value": '{"query":"x"}',
        "output.value": '{"count":1}',
    }.items():
        attribute = tool.attributes.add()
        attribute.key = key
        attribute.value.string_value = value
    return request.SerializeToString()


def test_parse_otlp_protobuf_preserves_hierarchy_and_content():
    spans = parse_otlp_traces(_payload())
    assert [span.name for span in spans] == ["agent:sample-agent", "tool:search"]
    assert spans[1].parent_span_id == spans[0].span_id
    assert spans[0].attributes["gen_ai.usage.input_tokens"] == 10


def test_omnigent_otlp_is_normalized_to_agent_turn(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("omnigent-otel-test")
    monkeypatch.setattr(
        MlflowTurnExporter,
        "_client_and_experiment",
        lambda self: (client, experiment_id),
    )

    trace_ids = export_otlp_traces(
        _payload(), Settings("test-profile", "https://example.cloud.databricks.com")
    )
    assert len(trace_ids) == 1
    trace = client.get_trace(trace_ids[0])
    root = next(span for span in trace.data.spans if span.name == "agent.turn")
    tool = next(span for span in trace.data.spans if span.name == "tool.search")
    assert root.inputs == {"prompt": "hello"}
    assert root.outputs["response"] == "done"
    assert tool.parent_id == root.span_id
    assert tool.inputs == {"query": "x"}
    assert tool.outputs == {"count": 1}
    assert trace.info.tags["agentops.runtime"] == "omnigent"
    assert trace.info.token_usage["total_tokens"] == 14
