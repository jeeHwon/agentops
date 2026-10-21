from __future__ import annotations

import json
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, Iterable

from .config import Settings
from .manifest import AGENT_ID_RE
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

    def __init__(
        self, *, grace_seconds: float = 0.0, link_wait_seconds: float = 30.0
    ) -> None:
        self._spans: dict[str, dict[str, OtelSpan]] = defaultdict(dict)
        self._root_seen_ns: dict[str, int] = {}
        self._grace_ns = int(grace_seconds * 1_000_000_000)
        self._link_wait_ns = int(link_wait_seconds * 1_000_000_000)
        self._session_trace: dict[str, str] = {}
        self._subagents: dict[str, _SubagentLink] = {}
        self._emitted: set[str] = set()
        self._lock = Lock()

    def ingest(
        self, spans: Iterable[OtelSpan], *, now_ns: int | None = None
    ) -> tuple[tuple[OtelSpan, ...], ...]:
        with self._lock:
            now = now_ns if now_ns is not None else time.monotonic_ns()
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
                first_seen = self._root_seen_ns.setdefault(trace_id, now)
                if now - first_seen < self._grace_ns:
                    continue
                session_id = str(root.attributes.get("session.id") or "")
                if session_id in self._subagents:
                    # Parent turn has not ended yet. Hold the child so the
                    # resulting MLflow trace contains the full Agent tree.
                    continue
                unresolved = any(
                    link.parent_trace_id == trace_id and not self._child_root(link.child_session_id)
                    for link in self._subagents.values()
                )
                if unresolved and now - first_seen < self._link_wait_ns:
                    continue
                combined, consumed = self._assemble(trace_id)
                ready.append(tuple(combined))
                self._drop(consumed)
            return tuple(ready)

    def _child_root(self, session_id: str) -> bool:
        trace_id = self._session_trace.get(session_id)
        return bool(
            trace_id and trace_id in self._spans
            and _root_agent_span(self._spans[trace_id].values())
        )

    def _assemble(self, trace_id: str) -> tuple[list[OtelSpan], tuple[str, ...]]:
        root = _root_agent_span(self._spans[trace_id].values())
        if root is None:
            return [], ()
        combined = list(self._spans[trace_id].values())
        consumed = [trace_id]
        linked = 0
        missing = 0
        for link in tuple(self._subagents.values()):
            if link.parent_trace_id != trace_id:
                continue
            child_trace_id = self._session_trace.get(link.child_session_id)
            if not child_trace_id or child_trace_id not in self._spans:
                missing += 1
                continue
            child_root = _root_agent_span(self._spans[child_trace_id].values())
            if child_root is None:
                missing += 1
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
            linked += 1
        if linked or missing:
            combined = [
                replace(
                    span,
                    attributes={
                        **span.attributes,
                        "agentops.subagent.trace_coverage": "partial" if missing else "linked",
                        "agentops.subagent.linked_count": linked,
                        "agentops.subagent.unjoined_count": missing,
                    },
                ) if span.span_id == root.span_id and span.trace_id == trace_id else span
                for span in combined
            ]
        return combined, tuple(consumed)

    def _drop(self, trace_ids: Iterable[str]) -> None:
        for trace_id in trace_ids:
            self._emitted.add(trace_id)
            self._root_seen_ns.pop(trace_id, None)
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
        agent_id = str(
            resource.get("agentops.agent_id")
            or root.attributes.get("agent.name")
            or root.attributes.get("gen_ai.agent.name")
            or "omnigent-agent"
        )
        metadata = _project_runtime_metadata(resource, agent_id)
        for field in (
            "agent_name", "agent_version", "build_type", "serving_type", "origin",
            "owner", "owner_group", "service_criticality", "data_sensitivity",
            "security_level", "model_service",
        ):
            if field in metadata:
                resource.setdefault(f"agentops.{field}", str(metadata[field]))
        if isinstance(metadata.get("flagship_model"), bool):
            resource.setdefault("agentops.flagship_model", str(metadata["flagship_model"]).lower())
        permissions = metadata.get("uc_permissions")
        if isinstance(permissions, list) and all(
            isinstance(item, dict) and isinstance(item.get("object"), str)
            and isinstance(item.get("privilege"), str) for item in permissions
        ):
            resource.setdefault("agentops.uc_permissions", "|".join(
                f"{item['object']}:{item['privilege']}" for item in permissions
            ))
        criteria = metadata.get("success_criteria")
        if isinstance(criteria, dict):
            resource.setdefault("agentops.success_criteria", "|".join(
                f"{key}={value}" for key, value in criteria.items()
            ))
        release = str(resource.get("agentops.agent_revision") or metadata.get("release_version") or "working")
        session_id = str(root.attributes.get("session.id") or "")
        input_value = _content(root.attributes.get("input.value"))
        output_value = _content(root.attributes.get("output.value"))
        evaluation_inputs_available = bool(input_value) and bool(output_value)
        usage = _aggregate_usage(items)
        subagent_count = max(0, len(agent_spans) - 1)
        session_send_calls = sum(
            _span_kind(span) == "TOOL"
            and str(span.attributes.get("tool.name") or span.attributes.get("gen_ai.tool.name") or "")
            == "sys_session_send"
            for span in items
        )
        root_attributes: dict[str, Any] = {
            "agentops.agent_id": agent_id,
            "agentops.agent_revision": release,
            "agentops.agent_type": str(metadata.get("agent_type") or resource.get("agentops.agent_type") or "skill-based-agent"),
            "agentops.runtime": "omnigent",
            "agentops.session_id": session_id or "unknown",
            "agentops.status": root.status.lower(),
            "agentops.evaluation_inputs_available": evaluation_inputs_available,
            "agentops.telemetry_source": "omnigent-native-otel",
            "agentops.otel.trace_id": root.trace_id,
            "agentops.otel.span_id": root.span_id,
            "agentops.turn_id": str(
                root.attributes.get("turn.id")
                or root.attributes.get("gen_ai.request.id")
                or root.span_id
            ),
            "agentops.tool_count": sum(_span_kind(span) == "TOOL" for span in items),
            "agentops.subagent_count": subagent_count,
            "agentops.session_send_calls": session_send_calls,
            "agentops.subagent_span_coverage": (
                "partial" if root.attributes.get("agentops.subagent.unjoined_count")
                else "observed" if subagent_count else "unverified" if session_send_calls
                else "not_requested"
            ),
            **usage,
        }
        for key in (
            "agentops.subagent.trace_coverage",
            "agentops.subagent.linked_count",
            "agentops.subagent.unjoined_count",
        ):
            if key in root.attributes:
                root_attributes[key] = root.attributes[key]
        for key in (
            "agentops.harness_checksum",
            "agentops.skills_checksum",
            "agentops.skill_names",
            "agentops.model",
            "agentops.agent_name",
            "agentops.agent_version",
            "agentops.build_type",
            "agentops.serving_type",
            "agentops.origin",
            "agentops.security_level",
            "agentops.uc_permissions",
            "agentops.flagship_model",
            "agentops.model_service",
            "agentops.success_criteria",
            "agentops.user_name",
            "agentops.owner",
            "agentops.owner_group",
            "agentops.service_criticality",
            "agentops.data_sensitivity",
        ):
            if key in resource:
                root_attributes[key] = resource[key]
        if metadata:
            root_attributes.update({
                "agentops.harness_checksum": metadata["harness_sha256"],
                "agentops.skills_checksum": metadata["skills_sha256"],
                "agentops.skill_names": "|".join(metadata.get("skills") or []),
            })
        trace_tags = {
            "agentops.agent_id": agent_id,
            **{
                key: str(resource[key]) for key in (
                    "agentops.agent_name", "agentops.agent_version", "agentops.build_type",
                    "agentops.serving_type", "agentops.origin", "agentops.owner",
                    "agentops.owner_group", "agentops.service_criticality",
                    "agentops.data_sensitivity", "agentops.security_level",
                    "agentops.uc_permissions", "agentops.flagship_model",
                    "agentops.model_service", "agentops.success_criteria",
                ) if key in resource
            },
            "agentops.agent_revision": release,
            "agentops.runtime": "omnigent",
            "agentops.session_id": session_id or "unknown",
            "agentops.status": root.status.lower(),
            "agentops.evaluation_inputs_available": str(evaluation_inputs_available).lower(),
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


def _project_runtime_metadata(resource: dict[str, Any], agent_id: str) -> dict[str, Any]:
    location = resource.get("agentops.projects_root")
    if not isinstance(location, str) or not location or not AGENT_ID_RE.fullmatch(agent_id):
        return {}
    root = Path(location)
    if not root.is_dir():
        return {}
    matches = []
    for project in root.iterdir():
        if project.is_symlink() or not project.is_dir():
            continue
        if (project / ".aops").is_symlink() or (project / ".aops" / "omnigent").is_symlink():
            continue
        file = project / ".aops" / "omnigent" / "runtime.json"
        if file.is_symlink() or not file.is_file():
            continue
        try:
            metadata = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(metadata, dict)
            and metadata.get("agent_id") == agent_id
            and isinstance(metadata.get("harness_sha256"), str)
            and isinstance(metadata.get("skills_sha256"), str)
        ):
            matches.append(metadata)
    return matches[0] if len(matches) == 1 else {}


def _aggregate_usage(spans: Iterable[OtelSpan]) -> dict[str, Any]:
    candidates = [span for span in spans if (
        "gen_ai.usage.input_tokens" in span.attributes
        or "gen_ai.usage.output_tokens" in span.attributes
    )]
    complete = [span for span in candidates if (
        "gen_ai.usage.input_tokens" in span.attributes
        and "gen_ai.usage.output_tokens" in span.attributes
    )]
    generations = [span for span in complete if _span_kind(span) == "LLM"]
    observations = generations or [span for span in complete if _span_kind(span) == "AGENT"]
    if not observations:
        return {"agentops.token_usage.available": False,
                "agentops.token_usage.coverage": "partial" if candidates else "unknown"}
    overlap = False
    if generations:
        agents = [span for span in complete if _span_kind(span) == "AGENT"]
        roots = [span for span in agents if span.parent_span_id is None]
        if roots and _reported_tokens(roots[0]) > sum(
            _reported_tokens(span) for span in generations
        ):
            observations = roots[:1]
            overlap = True
    else:
        by_id = {span.span_id: span for span in observations}
        filtered = []
        for span in observations:
            parent = by_id.get(span.parent_span_id)
            if parent is not None:
                overlap = True
                if (
                    _reported_tokens(parent) >= _reported_tokens(span)
                    and int(parent.attributes["gen_ai.usage.input_tokens"])
                    >= int(span.attributes["gen_ai.usage.input_tokens"])
                    and int(parent.attributes["gen_ai.usage.output_tokens"])
                    >= int(span.attributes["gen_ai.usage.output_tokens"])
                ):
                    continue
            filtered.append(span)
        observations = filtered
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "total_tokens": 0}
    consistent = True
    ambiguous = False
    for span in observations:
        attrs = span.attributes
        input_tokens = int(attrs.get("gen_ai.usage.input_tokens") or 0)
        output_tokens = int(attrs.get("gen_ai.usage.output_tokens") or 0)
        cached = int(attrs.get("gen_ai.usage.cache_read.input_tokens",
                               attrs.get("gen_ai.usage.cache_read_input_tokens")) or 0)
        cache_write = int(attrs.get("gen_ai.usage.cache_creation.input_tokens",
                                    attrs.get("gen_ai.usage.cache_creation_input_tokens")) or 0)
        reported = attrs.get("gen_ai.usage.total_tokens")
        if reported is not None and int(reported) == input_tokens + output_tokens:
            normalized_input = input_tokens
        elif reported is not None and int(reported) == input_tokens + cached + cache_write + output_tokens:
            normalized_input = input_tokens + cached + cache_write
        elif reported is None:
            normalized_input = input_tokens
            if cached or cache_write:
                ambiguous = True
        else:
            normalized_input = input_tokens + cached + cache_write
            consistent = False
        totals["input_tokens"] += normalized_input
        totals["output_tokens"] += output_tokens
        totals["cache_read_input_tokens"] += cached
        totals["cache_creation_input_tokens"] += cache_write
        totals["total_tokens"] += int(reported) if reported is not None else normalized_input + output_tokens
    return {
        "agentops.token_usage.available": True,
        "agentops.token_usage.source": "omnigent-native-otel",
        "agentops.token_usage.exact": False,
        "agentops.token_usage.coverage": (
            "inconsistent" if not consistent else "partial" if len(complete) != len(candidates)
            else "ambiguous" if ambiguous or overlap
            else "observed" if generations else "unverified"
        ),
        "agentops.tokens.input": totals["input_tokens"],
        "agentops.tokens.output": totals["output_tokens"],
        "agentops.tokens.cached_input": totals["cache_read_input_tokens"],
        "agentops.tokens.cache_write_input": totals["cache_creation_input_tokens"],
        "agentops.tokens.total": totals["total_tokens"],
        "mlflow.chat.tokenUsage": totals,
    }


def _reported_tokens(span: OtelSpan) -> int:
    attrs = span.attributes
    return int(attrs.get("gen_ai.usage.total_tokens") or (
        int(attrs.get("gen_ai.usage.input_tokens") or 0)
        + int(attrs.get("gen_ai.usage.output_tokens") or 0)
    ))


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
    child_id = span.attributes.get("agentops.subagent.child_session_id")
    if child_id:
        return _SubagentLink(
            parent_trace_id=span.trace_id,
            parent_session_id=str(span.attributes.get("session.id") or ""),
            launch_span_id=span.span_id,
            child_session_id=str(child_id),
            agent_name=str(span.attributes.get("agentops.subagent.name") or "subagent"),
        )
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
