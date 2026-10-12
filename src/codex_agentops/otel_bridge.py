from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .config import Settings
from .mlflow_exporter import MlflowTurnExporter


class OtelBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OtelSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_ns: int
    end_ns: int
    status: str
    attributes: dict[str, Any]
    resource: dict[str, Any]


def parse_otlp_traces(content: bytes) -> tuple[OtelSpan, ...]:
    """Decode an OTLP/HTTP protobuf trace export request."""
    try:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        request = ExportTraceServiceRequest.FromString(content)
    except Exception as exc:
        raise OtelBridgeError(f"Invalid OTLP trace payload: {exc}") from exc

    parsed: list[OtelSpan] = []
    for resource_spans in request.resource_spans:
        resource = _key_values(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                parsed.append(
                    OtelSpan(
                        trace_id=span.trace_id.hex(),
                        span_id=span.span_id.hex(),
                        parent_span_id=span.parent_span_id.hex() or None,
                        name=span.name,
                        start_ns=int(span.start_time_unix_nano),
                        end_ns=max(int(span.end_time_unix_nano), int(span.start_time_unix_nano) + 1),
                        status="ERROR" if int(span.status.code) == 2 else "OK",
                        attributes=_key_values(span.attributes),
                        resource=resource,
                    )
                )
    return tuple(parsed)


def export_otlp_traces(
    content: bytes,
    settings: Settings,
    *,
    exporter: "OtelMlflowExporter | None" = None,
) -> tuple[str, ...]:
    spans = parse_otlp_traces(content)
    grouped: dict[str, list[OtelSpan]] = defaultdict(list)
    for span in spans:
        grouped[span.trace_id].append(span)
    active = exporter or OtelMlflowExporter(settings)
    trace_ids: list[str] = []
    for group in grouped.values():
        if any(_span_kind(span) == "AGENT" for span in group):
            trace_ids.append(active.export(group))
    return tuple(trace_ids)


class OtelMlflowExporter:
    """Normalize Omnigent native OTel spans into the shared MLflow trace contract."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def export(self, spans: Iterable[OtelSpan]) -> str:
        items = sorted(spans, key=lambda span: (span.start_ns, span.end_ns, span.span_id))
        if not items:
            raise OtelBridgeError("Cannot export an empty OTel trace.")
        agent_spans = [span for span in items if _span_kind(span) == "AGENT"]
        if not agent_spans:
            raise OtelBridgeError("OTel trace does not contain an Agent span.")
        ids = {span.span_id for span in items}
        root = next(
            (
                span
                for span in agent_spans
                if span.parent_span_id is None or span.parent_span_id not in ids
            ),
            agent_spans[0],
        )
        client, experiment_id = MlflowTurnExporter(self.settings)._client_and_experiment()
        resource = {**root.resource, **_resource_union(items)}
        release = str(resource.get("agentops.agent_revision") or "working")
        agent_id = str(
            resource.get("agentops.agent_id")
            or root.attributes.get("agent.name")
            or root.attributes.get("gen_ai.agent.name")
            or "omnigent-agent"
        )
        session_id = str(root.attributes.get("session.id") or "")
        input_value = _content(root.attributes.get("input.value"))
        output_value = _content(root.attributes.get("output.value"))
        usage = _aggregate_usage(items)
        root_attributes: dict[str, Any] = {
            "agentops.agent_id": agent_id,
            "agentops.agent_revision": release,
            "agentops.agent_type": "harness",
            "agentops.runtime": "omnigent",
            "agentops.session_id": session_id or "unknown",
            "agentops.status": root.status.lower(),
            "agentops.telemetry_source": "omnigent-native-otel",
            "agentops.tool_count": sum(_span_kind(span) == "TOOL" for span in items),
            "agentops.subagent_count": max(0, len(agent_spans) - 1),
            **usage,
        }
        for key in (
            "agentops.harness_checksum",
            "agentops.skills_checksum",
            "agentops.skill_names",
            "agentops.model",
        ):
            if key in resource:
                root_attributes[key] = resource[key]
        trace_tags = {
            "agentops.agent_id": agent_id,
            "agentops.agent_revision": release,
            "agentops.runtime": "omnigent",
            "agentops.session_id": session_id or "unknown",
            "agentops.status": root.status.lower(),
            "agentops.telemetry_source": "omnigent-native-otel",
            "mlflow.trace.session": session_id or "unknown",
        }
        mlflow_root = client.start_trace(
            name="agent.turn",
            span_type="AGENT",
            inputs={"prompt": input_value},
            attributes=root_attributes,
            tags=trace_tags,
            experiment_id=experiment_id,
            start_time_ns=root.start_ns,
        )
        created: dict[str, Any] = {root.span_id: mlflow_root}
        pending = [span for span in items if span.span_id != root.span_id]
        try:
            while pending:
                progressed = False
                for span in list(pending):
                    parent = created.get(span.parent_span_id)
                    if parent is None and span.parent_span_id in ids:
                        continue
                    parent = parent or mlflow_root
                    created[span.span_id] = client.start_span(
                        name=_normalized_name(span),
                        trace_id=mlflow_root.trace_id,
                        parent_id=parent.span_id,
                        span_type=_mlflow_span_type(span),
                        inputs=_span_inputs(span),
                        attributes=_normalized_attributes(span),
                        start_time_ns=span.start_ns,
                    )
                    pending.remove(span)
                    progressed = True
                if not progressed:
                    for span in pending:
                        created[span.span_id] = client.start_span(
                            name=_normalized_name(span),
                            trace_id=mlflow_root.trace_id,
                            parent_id=mlflow_root.span_id,
                            span_type=_mlflow_span_type(span),
                            inputs=_span_inputs(span),
                            attributes=_normalized_attributes(span),
                            start_time_ns=span.start_ns,
                        )
                    pending.clear()

            for span in sorted(
                (item for item in items if item.span_id != root.span_id),
                key=lambda item: item.end_ns,
                reverse=True,
            ):
                target = created[span.span_id]
                client.end_span(
                    trace_id=mlflow_root.trace_id,
                    span_id=target.span_id,
                    outputs=_span_outputs(span),
                    status=span.status,
                    end_time_ns=span.end_ns,
                )
            client.end_trace(
                mlflow_root.trace_id,
                outputs={"response": output_value, "status": root.status.lower()},
                attributes={"agentops.status": root.status.lower()},
                status=root.status,
                end_time_ns=root.end_ns,
            )
            import mlflow

            mlflow.flush_trace_async_logging()
        except Exception:
            try:
                client.end_trace(
                    mlflow_root.trace_id,
                    outputs={"status": "export_error"},
                    status="ERROR",
                )
            except Exception:
                pass
            raise
        return mlflow_root.trace_id


def _resource_union(spans: Iterable[OtelSpan]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for span in spans:
        result.update(span.resource)
    return result


def _key_values(items: Iterable[Any]) -> dict[str, Any]:
    return {str(item.key): _any_value(item.value) for item in items}


def _any_value(value: Any) -> Any:
    kind = value.WhichOneof("value")
    if kind == "array_value":
        return [_any_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return _key_values(value.kvlist_value.values)
    return getattr(value, kind) if kind else None


def _span_kind(span: OtelSpan) -> str:
    return str(span.attributes.get("openinference.span.kind") or "").upper()


def _normalized_name(span: OtelSpan) -> str:
    kind = _span_kind(span)
    if kind == "TOOL":
        name = span.attributes.get("tool.name") or span.attributes.get("gen_ai.tool.name")
        return f"tool.{name or span.name.removeprefix('tool:')}"
    if kind == "AGENT":
        name = span.attributes.get("agent.name") or span.attributes.get("gen_ai.agent.name")
        return f"agent.subagent.{name or span.name.removeprefix('agent:')}"
    return span.name


def _mlflow_span_type(span: OtelSpan) -> str:
    kind = _span_kind(span)
    if kind in {"AGENT", "TOOL", "GUARDRAIL", "LLM", "RETRIEVER"}:
        return kind
    if span.name.startswith("llm"):
        return "LLM"
    return "UNKNOWN"


def _content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _span_inputs(span: OtelSpan) -> Any:
    value = _content(span.attributes.get("input.value"))
    return value if isinstance(value, dict) else {"input": value}


def _span_outputs(span: OtelSpan) -> Any:
    value = _content(span.attributes.get("output.value"))
    return value if isinstance(value, dict) else {"output": value}


def _normalized_attributes(span: OtelSpan) -> dict[str, Any]:
    excluded = {"input.value", "output.value"}
    return {
        "agentops.runtime": "omnigent",
        "agentops.otel.trace_id": span.trace_id,
        "agentops.otel.span_id": span.span_id,
        **{key: value for key, value in span.attributes.items() if key not in excluded},
    }


def _aggregate_usage(spans: Iterable[OtelSpan]) -> dict[str, Any]:
    keys = {
        "gen_ai.usage.input_tokens": "input_tokens",
        "gen_ai.usage.output_tokens": "output_tokens",
        "gen_ai.usage.cache_read_input_tokens": "cache_read_input_tokens",
        "gen_ai.usage.cache_creation_input_tokens": "cache_creation_input_tokens",
    }
    totals = {target: 0 for target in keys.values()}
    available = False
    for span in spans:
        for source, target in keys.items():
            if source in span.attributes:
                available = True
                totals[target] += int(span.attributes[source] or 0)
    total = totals["input_tokens"] + totals["output_tokens"]
    return {
        "agentops.token_usage.available": available,
        "agentops.token_usage.source": "omnigent-native-otel",
        "agentops.token_usage.exact": available,
        "agentops.tokens.input": totals["input_tokens"],
        "agentops.tokens.output": totals["output_tokens"],
        "agentops.tokens.cached_input": totals["cache_read_input_tokens"],
        "agentops.tokens.cache_write_input": totals["cache_creation_input_tokens"],
        "agentops.tokens.total": total,
        "mlflow.chat.tokenUsage": {**totals, "total_tokens": total},
    }
