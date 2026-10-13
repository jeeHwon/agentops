from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from threading import Lock
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


@dataclass(frozen=True)
class _SubagentLink:
    parent_trace_id: str
    parent_session_id: str
    launch_span_id: str
    child_session_id: str
    agent_name: str


class OtelTraceAssembler:
    """Assemble one logical Omnigent turn across incremental OTLP batches.

    Omnigent's BatchSpanProcessors export completed tool spans before the
    enclosing Agent span ends.  A stateless collector therefore sees tools in
    earlier HTTP requests and the root in a later request.  The assembler keeps
    only GenAI spans, deduplicates exporter retries by span id, and joins a
    completed Sub-agent trace back to the parent turn using the
    ``sys_session_send`` conversation id.
    """

    def __init__(self) -> None:
        self._spans: dict[str, dict[str, OtelSpan]] = defaultdict(dict)
        self._session_trace: dict[str, str] = {}
        self._subagents: dict[str, _SubagentLink] = {}
        self._emitted: set[str] = set()
        self._lock = Lock()

    def ingest(self, spans: Iterable[OtelSpan]) -> tuple[tuple[OtelSpan, ...], ...]:
        with self._lock:
            for span in spans:
                if span.trace_id in self._emitted or not _is_genai_span(span):
                    continue
                self._spans[span.trace_id][span.span_id] = span
                if _span_kind(span) == "AGENT":
                    session_id = str(span.attributes.get("session.id") or "")
                    if session_id:
                        self._session_trace[session_id] = span.trace_id
                link = _subagent_link(span)
                if link is not None:
                    self._subagents[link.child_session_id] = link

            ready: list[tuple[OtelSpan, ...]] = []
            # Root Agent spans end after the tools in their turn, so their
            # presence is the completion signal for a buffered trace.
            for trace_id in list(self._spans):
                root = _root_agent_span(self._spans[trace_id].values())
                if root is None:
                    continue
                agent_name = str(
                    root.attributes.get("agent.name")
                    or root.attributes.get("gen_ai.agent.name")
                    or ""
                )
                if agent_name == "session-title":
                    self._drop((trace_id,))
                    continue
                session_id = str(root.attributes.get("session.id") or "")
                if session_id in self._subagents:
                    # Parent turn has not ended yet. Hold the child so the
                    # resulting MLflow trace contains the full Agent tree.
                    continue
                combined, consumed = self._assemble(trace_id)
                ready.append(tuple(combined))
                self._drop(consumed)
            return tuple(ready)

    def _assemble(self, trace_id: str) -> tuple[list[OtelSpan], tuple[str, ...]]:
        root = _root_agent_span(self._spans[trace_id].values())
        if root is None:
            return [], ()
        combined = list(self._spans[trace_id].values())
        consumed = [trace_id]
        for link in tuple(self._subagents.values()):
            if link.parent_trace_id != trace_id:
                continue
            child_trace_id = self._session_trace.get(link.child_session_id)
            if not child_trace_id or child_trace_id not in self._spans:
                continue
            child_root = _root_agent_span(self._spans[child_trace_id].values())
            if child_root is None:
                continue
            for span in self._spans[child_trace_id].values():
                if span.span_id == child_root.span_id:
                    attrs = {
                        **span.attributes,
                        "agent.name": link.agent_name,
                        "gen_ai.agent.name": link.agent_name,
                        "agentops.subagent": True,
                        "agentops.parent_session_id": link.parent_session_id,
                        "agentops.original_otel_trace_id": span.trace_id,
                    }
                    span = replace(span, parent_span_id=root.span_id, attributes=attrs)
                combined.append(span)
            consumed.append(child_trace_id)
        return combined, tuple(consumed)

    def _drop(self, trace_ids: Iterable[str]) -> None:
        for trace_id in trace_ids:
            self._emitted.add(trace_id)
            group = self._spans.pop(trace_id, {})
            for span in group.values():
                session_id = str(span.attributes.get("session.id") or "")
                if self._session_trace.get(session_id) == trace_id:
                    self._session_trace.pop(session_id, None)
        for child_session_id, link in list(self._subagents.items()):
            # Keep a launch link even when the child Agent span has not arrived
            # yet. Omnigent may finish an unrelated session-title trace between
            # ``sys_session_send`` and the first child batch. Removing the link
            # here would export the child as a second top-level turn instead of
            # joining it to its parent.
            if link.parent_trace_id in self._emitted:
                self._subagents.pop(child_session_id, None)


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
    assembler: OtelTraceAssembler | None = None,
) -> tuple[str, ...]:
    spans = parse_otlp_traces(content)
    if assembler is not None:
        groups = assembler.ingest(spans)
    else:
        grouped: dict[str, list[OtelSpan]] = defaultdict(list)
        for span in spans:
            grouped[span.trace_id].append(span)
        groups = tuple(tuple(group) for group in grouped.values())
    active = exporter or OtelMlflowExporter(settings)
    trace_ids: list[str] = []
    for group in groups:
        if _root_agent_span(group) is not None:
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
            "agentops.otel.trace_id": root.trace_id,
            "agentops.otel.span_id": root.span_id,
            "agentops.turn_id": str(
                root.attributes.get("turn.id")
                or root.attributes.get("gen_ai.request.id")
                or root.span_id
            ),
            "agentops.tool_count": sum(_span_kind(span) == "TOOL" for span in items),
            "agentops.subagent_count": max(0, len(agent_spans) - 1),
            **usage,
        }
        for key in (
            "agentops.harness_checksum",
            "agentops.skills_checksum",
            "agentops.skill_names",
            "agentops.model",
            "agentops.user_name",
            "agentops.owner",
            "agentops.owner_group",
            "agentops.source_system",
            "agentops.target_users",
            "agentops.cost_center",
            "agentops.service_criticality",
            "agentops.data_sensitivity",
            "agentops.security_access_level",
            "agentops.risk_level",
            "agentops.hitl_required",
            "agentops.required_uc_permissions",
            "agentops.use_flagship_model",
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
            "agentops.otel.trace_id": root.trace_id,
            "mlflow.trace.session": session_id or "unknown",
            "mlflow.trace.user": str(resource.get("agentops.user_name") or "unknown"),
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
    attributes = {
        "agentops.runtime": "omnigent",
        "agentops.otel.trace_id": span.trace_id,
        "agentops.otel.span_id": span.span_id,
        **{key: value for key, value in span.attributes.items() if key not in excluded},
    }
    usage = _aggregate_usage((span,))
    if usage["agentops.token_usage.available"]:
        attributes.update(usage)
    return attributes


def _aggregate_usage(spans: Iterable[OtelSpan]) -> dict[str, Any]:
    keys = {
        "gen_ai.usage.input_tokens": "input_tokens",
        "gen_ai.usage.output_tokens": "output_tokens",
        "gen_ai.usage.cache_read_input_tokens": "cache_read_input_tokens",
        "gen_ai.usage.cache_creation_input_tokens": "cache_creation_input_tokens",
    }
    totals = {target: 0 for target in keys.values()}
    reported_total = 0
    reported_total_available = False
    available = False
    for span in spans:
        for source, target in keys.items():
            if source in span.attributes:
                available = True
                totals[target] += int(span.attributes[source] or 0)
        if "gen_ai.usage.total_tokens" in span.attributes:
            reported_total_available = True
            reported_total += int(span.attributes["gen_ai.usage.total_tokens"] or 0)
    total = (
        reported_total
        if reported_total_available
        else totals["input_tokens"]
        + totals["output_tokens"]
        + totals["cache_read_input_tokens"]
        + totals["cache_creation_input_tokens"]
    )
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


def _is_genai_span(span: OtelSpan) -> bool:
    return _span_kind(span) in {"AGENT", "TOOL", "GUARDRAIL", "LLM", "RETRIEVER"}


def _root_agent_span(spans: Iterable[OtelSpan]) -> OtelSpan | None:
    items = tuple(spans)
    agent_spans = [span for span in items if _span_kind(span) == "AGENT"]
    if not agent_spans:
        return None
    ids = {span.span_id for span in items}
    return next(
        (
            span
            for span in sorted(agent_spans, key=lambda item: (item.start_ns, item.span_id))
            if span.parent_span_id is None or span.parent_span_id not in ids
        ),
        agent_spans[0],
    )


def _subagent_link(span: OtelSpan) -> _SubagentLink | None:
    if _span_kind(span) != "TOOL":
        return None
    tool_name = str(
        span.attributes.get("tool.name") or span.attributes.get("gen_ai.tool.name") or ""
    )
    if tool_name != "sys_session_send":
        return None
    output = _content(span.attributes.get("output.value"))
    inputs = _content(span.attributes.get("input.value"))
    if not isinstance(output, dict) or not isinstance(inputs, dict):
        return None
    child_session_id = str(output.get("conversation_id") or output.get("task_id") or "")
    if not child_session_id or str(output.get("kind") or "") != "sub_agent":
        return None
    return _SubagentLink(
        parent_trace_id=span.trace_id,
        parent_session_id=str(span.attributes.get("session.id") or ""),
        launch_span_id=span.span_id,
        child_session_id=child_session_id,
        agent_name=str(inputs.get("agent") or "subagent"),
    )
