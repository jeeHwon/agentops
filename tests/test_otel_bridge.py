from __future__ import annotations

from codex_agentops.config import Settings
from codex_agentops.mlflow_exporter import MlflowTurnExporter
from dataclasses import replace

from codex_agentops.otel_bridge import (
    OtelSpan,
    OtelMlflowExporter,
    OtelTraceAssembler,
    _project_runtime_metadata,
    _aggregate_usage,
    _normalized_attributes,
    export_otlp_traces,
    parse_otlp_traces,
)


def _payload() -> bytes:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    resource = request.resource_spans.add()
    for key, value in {
        "agentops.agent_id": "sample-agent",
        "agentops.agent_revision": "1.0.0",
        "agentops.agent_version": "1.0.0",
        "agentops.security_level": "일반",
        "agentops.model_service": "system.ai.gpt-5-6-sol",
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
        "gen_ai.usage.cache_read_input_tokens": 6,
        "gen_ai.usage.total_tokens": 20,
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
    assert trace.info.tags["agentops.evaluation_inputs_available"] == "true"
    assert tool.parent_id == root.span_id
    assert tool.inputs == {"query": "x"}
    assert tool.outputs == {"count": 1}
    assert trace.info.tags["agentops.runtime"] == "omnigent"
    assert trace.info.tags["agentops.security_level"] == "일반"
    assert trace.info.tags["agentops.model_service"] == "system.ai.gpt-5-6-sol"
    assert trace.info.token_usage["total_tokens"] == 20
    assert trace.info.token_usage["input_tokens"] == 16
    assert trace.info.token_usage["output_tokens"] == 4
    assert root.attributes["agentops.token_usage.coverage"] == "unverified"
    assert trace.info.token_usage["cache_read_input_tokens"] == 6
    assert trace.info.execution_time_ms == 1000
    assert len(client.search_traces([experiment_id])) == 1
    assert trace.info.cost is None


def test_real_mlflow_omnigent_subagent_and_missing_usage(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri(str(tmp_path / "mlruns"))
    client = MlflowClient()
    experiment_id = client.create_experiment("omnigent-coverage")
    monkeypatch.setattr(MlflowTurnExporter, "_client_and_experiment",
                        lambda self: (client, experiment_id))

    root, tool = parse_otlp_traces(_payload())
    child = replace(root, span_id="55" * 8, parent_span_id=root.span_id,
                    attributes={**root.attributes, "agent.name": "validator"})
    child_tool = replace(tool, parent_span_id=child.span_id)
    exporter = OtelMlflowExporter(Settings("test-profile", "https://example.cloud.databricks.com"))
    trace = client.get_trace(exporter.export((root, child, child_tool)))
    spans = trace.data.spans
    mlflow_root = next(span for span in spans if span.name == "agent.turn")
    mlflow_child = next(span for span in spans if span.span_type == "AGENT" and span.parent_id)
    mlflow_tool = next(span for span in spans if span.span_type == "TOOL")
    assert mlflow_child.parent_id == mlflow_root.span_id
    assert mlflow_tool.parent_id == mlflow_child.span_id
    assert trace.info.token_usage["total_tokens"] == 20
    assert mlflow_root.attributes["agentops.token_usage.coverage"] == "ambiguous"

    incomplete = replace(root, trace_id="77" * 16, attributes={
        "openinference.span.kind": "AGENT", "agent.name": "sample-agent",
        "gen_ai.usage.input_tokens": 10,
    })
    missing = client.get_trace(exporter.export((incomplete,)))
    assert missing.info.token_usage is None
    assert missing.data.spans[0].attributes["agentops.token_usage.coverage"] == "partial"
    assert len(client.search_traces([experiment_id])) == 2


def test_session_send_without_child_span_is_not_reported_as_subagent_coverage(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient

    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("missing-subagent-span")
    monkeypatch.setattr(
        MlflowTurnExporter, "_client_and_experiment", lambda self: (client, experiment_id)
    )
    root, tool = parse_otlp_traces(_payload())
    root = replace(root, attributes={
        key: value for key, value in root.attributes.items()
        if key not in {"input.value", "output.value"}
    })
    launch = replace(
        tool, name="tool:sys_session_send",
        attributes={**tool.attributes, "tool.name": "sys_session_send"},
    )

    trace = client.get_trace(
        OtelMlflowExporter(Settings("test", "https://example.com")).export((root, launch))
    )
    attributes = next(span for span in trace.data.spans if span.name == "agent.turn").attributes
    assert attributes["agentops.subagent_count"] == 0
    assert attributes["agentops.session_send_calls"] == 1
    assert attributes["agentops.subagent_span_coverage"] == "unverified"
    assert trace.info.tags["agentops.evaluation_inputs_available"] == "false"


def test_trace_assembler_buffers_batches_deduplicates_and_joins_subagent():
    root, _ = parse_otlp_traces(_payload())
    launch = OtelSpan(
        trace_id=root.trace_id,
        span_id="44" * 8,
        parent_span_id=root.span_id,
        name="tool:sys_session_send",
        start_ns=root.start_ns + 10,
        end_ns=root.start_ns + 20,
        status="OK",
        attributes={
            "openinference.span.kind": "TOOL",
            "tool.name": "sys_session_send",
            "session.id": "conv-1",
            "input.value": '{"agent":"validator"}',
            "output.value": (
                '{"kind":"sub_agent","conversation_id":"child-1",'
                '"task_id":"child-1"}'
            ),
        },
        resource=root.resource,
    )
    child = replace(
        root,
        trace_id="55" * 16,
        span_id="66" * 8,
        start_ns=root.start_ns + 30,
        end_ns=root.end_ns - 10,
        attributes={
            **root.attributes,
            "session.id": "child-1",
            "input.value": "validate",
            "output.value": "passed",
        },
    )
    assembler = OtelTraceAssembler()

    assert assembler.ingest([launch, launch]) == ()
    assert assembler.ingest([child, child]) == ()
    groups = assembler.ingest([root, root])

    assert len(groups) == 1
    assert len(groups[0]) == 3
    child_result = next(span for span in groups[0] if span.span_id == child.span_id)
    assert child_result.parent_span_id == root.span_id
    assert child_result.attributes["agent.name"] == "validator"
    assert child_result.attributes["agentops.subagent"] is True
    assert assembler.ingest([root, launch, child]) == ()


def test_trace_assembler_keeps_pending_subagent_link_across_unrelated_trace():
    root, _ = parse_otlp_traces(_payload())
    launch = OtelSpan(
        trace_id=root.trace_id,
        span_id="77" * 8,
        parent_span_id=root.span_id,
        name="tool:sys_session_send",
        start_ns=root.start_ns + 10,
        end_ns=root.start_ns + 20,
        status="OK",
        attributes={
            "openinference.span.kind": "TOOL",
            "tool.name": "sys_session_send",
            "session.id": "parent-session",
            "input.value": '{"agent":"validator"}',
            "output.value": (
                '{"kind":"sub_agent","conversation_id":"child-session",'
                '"task_id":"child-session"}'
            ),
        },
        resource=root.resource,
    )
    session_title = replace(
        root,
        trace_id="88" * 16,
        span_id="99" * 8,
        attributes={
            **root.attributes,
            "agent.name": "session-title",
            "session.id": "title-session",
        },
    )
    child = replace(
        root,
        trace_id="aa" * 16,
        span_id="bb" * 8,
        attributes={
            **root.attributes,
            "session.id": "child-session",
            "input.value": "validate",
            "output.value": "passed",
        },
    )
    assembler = OtelTraceAssembler()

    assert assembler.ingest([launch]) == ()
    assert assembler.ingest([session_title]) == ()
    assert assembler.ingest([child]) == ()
    groups = assembler.ingest([root])

    assert len(groups) == 1
    assert {span.span_id for span in groups[0]} == {
        root.span_id,
        launch.span_id,
        child.span_id,
    }
    child_result = next(span for span in groups[0] if span.span_id == child.span_id)
    assert child_result.parent_span_id == root.span_id
    assert child_result.attributes["agent.name"] == "validator"


def test_agent_span_preserves_exact_token_usage_for_subagent_view():
    root, _ = parse_otlp_traces(_payload())

    attributes = _normalized_attributes(root)

    assert attributes["agentops.token_usage.exact"] is False
    assert attributes["mlflow.chat.tokenUsage"] == {
        "input_tokens": 16,
        "output_tokens": 4,
        "cache_read_input_tokens": 6,
        "cache_creation_input_tokens": 0,
        "total_tokens": 20,
    }


def test_assembler_waits_for_late_tool_without_collecting_content():
    root, tool = parse_otlp_traces(_payload())
    assembler = OtelTraceAssembler(grace_seconds=2)
    assert assembler.ingest([root], now_ns=1_000_000_000) == ()
    assert assembler.ingest([replace(tool, attributes={"openinference.span.kind": "TOOL", "tool.name": "search"})],
                            now_ns=2_000_000_000) == ()
    groups = assembler.ingest((), now_ns=3_000_000_000)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_usage_prefers_generation_spans_and_distinguishes_missing_counts():
    root, tool = parse_otlp_traces(_payload())
    llm = replace(root, span_id="33" * 8, attributes={
        **root.attributes, "openinference.span.kind": "LLM",
    })
    assert _aggregate_usage((root, llm))["agentops.tokens.total"] == 20
    assert _aggregate_usage((root,))["agentops.token_usage.coverage"] == "unverified"
    assert _aggregate_usage((tool,))["agentops.token_usage.coverage"] == "unknown"


def test_usage_missing_field_does_not_report_false_zero():
    root, _ = parse_otlp_traces(_payload())
    incomplete = replace(root, attributes={
        "openinference.span.kind": "AGENT",
        "gen_ai.usage.input_tokens": 10,
    })
    usage = _aggregate_usage((incomplete,))
    assert usage == {
        "agentops.token_usage.available": False,
        "agentops.token_usage.coverage": "partial",
    }
    zero_output = replace(root, attributes={
        "openinference.span.kind": "LLM",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 0,
    })
    assert _aggregate_usage((zero_output,))["mlflow.chat.tokenUsage"]["total_tokens"] == 10


def test_usage_deduplicates_agent_rollup_and_marks_partial_llm():
    root, _ = parse_otlp_traces(_payload())
    child = replace(root, span_id="55" * 8, parent_span_id=root.span_id)
    usage = _aggregate_usage((root, child))
    assert usage["agentops.tokens.total"] == 20
    assert usage["agentops.token_usage.coverage"] == "ambiguous"

    llm = replace(root, span_id="66" * 8, parent_span_id=root.span_id, attributes={
        "openinference.span.kind": "LLM",
        "gen_ai.usage.input_tokens": 8,
        "gen_ai.usage.output_tokens": 3,
        "gen_ai.usage.total_tokens": 11,
    })
    usage = _aggregate_usage((root, llm))
    assert usage["agentops.tokens.total"] == 20
    assert usage["agentops.token_usage.coverage"] == "ambiguous"


def test_usage_reads_standard_otel_cache_breakdown():
    root, _ = parse_otlp_traces(_payload())
    attrs = dict(root.attributes)
    attrs.pop("gen_ai.usage.cache_read_input_tokens")
    attrs["gen_ai.usage.cache_read.input_tokens"] = 6
    usage = _aggregate_usage((replace(root, attributes=attrs),))
    assert usage["mlflow.chat.tokenUsage"]["cache_read_input_tokens"] == 6


def test_web_project_release_metadata_is_selected_by_agent_id(tmp_path):
    import json

    project = tmp_path / "unrelated-project" / ".aops" / "omnigent"
    project.mkdir(parents=True)
    (project / "runtime.json").write_text(json.dumps({
        "agent_id": "sample-agent", "release_version": "1.2.3", "agent_type": "skill-based-agent",
        "harness_sha256": "abc", "skills_sha256": "def", "skills": ["one"],
    }))
    assert _project_runtime_metadata({"agentops.projects_root": str(tmp_path)}, "sample-agent")["release_version"] == "1.2.3"


def test_web_project_trace_inherits_canonical_spec_tags(tmp_path, monkeypatch):
    import json
    import mlflow
    from mlflow import MlflowClient
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    project = tmp_path / "sample" / ".aops" / "omnigent"
    project.mkdir(parents=True)
    (project / "runtime.json").write_text(json.dumps({
        "agent_id": "sample-agent", "release_version": "1.0.0",
        "agent_type": "skill-based-agent", "harness_sha256": "abc", "skills_sha256": "def",
        "agent_name": "Sample", "agent_version": "1.0.0", "build_type": "skill",
        "serving_type": "apps", "origin": "databricks", "owner": "owner@example.com",
        "owner_group": "AI-CoE", "service_criticality": "하", "data_sensitivity": "내부",
        "security_level": "일반", "uc_permissions": [], "flagship_model": False,
        "model_service": "system.ai.test", "success_criteria": {"correctness_min": 0.8},
    }))
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    for key, value in {"agentops.projects_root": str(tmp_path)}.items():
        attribute = resource_spans.resource.attributes.add()
        attribute.key = key
        attribute.value.string_value = value
    span = resource_spans.scope_spans.add().spans.add(
        trace_id=bytes.fromhex("aa" * 16), span_id=bytes.fromhex("bb" * 8),
        name="agent:sample-agent", start_time_unix_nano=1_000_000_000,
        end_time_unix_nano=2_000_000_000,
    )
    for key, value in {"agent.name": "sample-agent", "openinference.span.kind": "AGENT"}.items():
        attribute = span.attributes.add()
        attribute.key = key
        attribute.value.string_value = value
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking_uri = str(tmp_path / "mlruns")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment("omnigent-project-spec-test")
    monkeypatch.setattr(MlflowTurnExporter, "_client_and_experiment", lambda self: (client, experiment_id))
    trace_ids = export_otlp_traces(request.SerializeToString(), Settings("test", "https://example.com"))
    assert len(trace_ids) == 1
    tags = client.get_trace(trace_ids[0]).info.tags
    assert tags["agentops.agent_version"] == "1.0.0"
    assert tags["agentops.model_service"] == "system.ai.test"
    assert tags["agentops.owner_group"] == "AI-CoE"
