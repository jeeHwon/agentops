from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings
from .events import tool_failed
from .outbox import Outbox, PendingTurn, RuntimeAgent, StoredEvent, ToolObservation
from . import __version__


@dataclass(frozen=True)
class ExportResult:
    uploaded: int
    failed: int
    trace_ids: tuple[str, ...]


class MlflowTurnExporter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tracking_uri = f"databricks://{settings.profile}"

    def _client_and_experiment(self, *, create_if_missing: bool = False):
        import mlflow
        from mlflow import MlflowClient

        os.environ["DATABRICKS_CONFIG_PROFILE"] = self.settings.profile
        mlflow.set_tracking_uri(self.tracking_uri)
        if self.settings.warehouse_id:
            os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = self.settings.warehouse_id
        client = MlflowClient(tracking_uri=self.tracking_uri)
        experiment = client.get_experiment_by_name(self.settings.experiment)
        if experiment is None and create_if_missing:
            try:
                experiment_id = client.create_experiment(self.settings.experiment)
            except Exception as exc:
                # Another configure process may have created the same experiment
                # after our initial lookup. Re-read before reporting a failure.
                try:
                    experiment = client.get_experiment_by_name(self.settings.experiment)
                except Exception:
                    experiment = None
                if experiment is None:
                    raise RuntimeError(
                        "MLflow Experiment를 생성하지 못했습니다: "
                        f"{self.settings.experiment}. Databricks Workspace에서 "
                        f"Experiment 생성 권한을 확인하세요. 원인: {exc}"
                    ) from exc
            else:
                mlflow.set_experiment(experiment_id=experiment_id)
                return client, experiment_id
        if experiment is None:
            raise RuntimeError(
                f"MLflow Experiment가 없습니다: {self.settings.experiment}. "
                "먼저 `aops configure`를 실행하세요."
            )
        mlflow.set_experiment(experiment_id=experiment.experiment_id)
        return client, experiment.experiment_id

    def write_test_trace(self) -> str:
        import mlflow

        client, experiment_id = self._client_and_experiment(create_if_missing=True)
        now = time.time_ns()
        root = client.start_trace(
            name="codex-agentops.configuration-test",
            span_type="AGENT",
            inputs={"message": "Codex AgentOps configuration test"},
            attributes={
                "agentops.phase": "1",
                "agentops.test": "true",
                "agentops.profile": self.settings.profile,
            },
            tags={"agentops.test": "true", "agentops.profile": self.settings.profile},
            experiment_id=experiment_id,
            start_time_ns=now,
        )
        client.end_trace(
            root.trace_id,
            outputs={"status": "configured"},
            attributes={"agentops.status": "ok"},
            status="OK",
            end_time_ns=max(time.time_ns(), now + 1),
        )
        mlflow.flush_trace_async_logging()
        trace = client.get_trace(root.trace_id)
        if not trace.data.spans:
            raise RuntimeError(f"Test trace has no spans: {root.trace_id}")
        return root.trace_id

    def export_turn(self, turn: PendingTurn) -> str:
        client, experiment_id = self._client_and_experiment()
        prompt_event = _first(turn.events, "UserPromptSubmit")
        stop_event = _last(turn.events, "Stop")
        interrupt_event = _last(turn.events, "Interrupt")
        tool_events = tuple(item for item in turn.events if item.event_name == "PostToolUse")
        has_tool_error = any(_tool_event_failed(turn, item) for item in tool_events)
        status_name = "cancelled" if interrupt_event else ("error" if has_tool_error else "ok")
        mlflow_status = "ERROR" if status_name in {"cancelled", "error"} else "OK"
        prompt = prompt_event.payload.get("prompt") if prompt_event else None
        response = stop_event.payload.get("last_assistant_message") if stop_event else None
        model = _first_present(turn.events, "model")
        permission_mode = _first_present(turn.events, "permission_mode")
        user_name = _first_present(turn.events, "user_name") or self.settings.user_name or "unknown"
        harness_checksum = _first_present(turn.events, "harness_checksum") or "unknown"
        skills_checksum = _first_present(turn.events, "skills_checksum") or "unknown"
        skill_checksums = _first_present(turn.events, "skill_checksums") or {}
        matched_observations = tuple(
            observation
            for event in tool_events
            if (observation := _tool_observation(turn, event)) is not None
        )
        event_times = [item.created_ns for item in turn.events]
        observation_starts = [_tool_start_ns(item) for item in matched_observations]
        observation_ends = [item.observed_ns for item in matched_observations]
        start_ns = min(event_times + observation_starts, default=time.time_ns())
        end_ns = max(event_times + observation_ends, default=start_ns + 1)
        token_attributes = _token_attributes(
            turn.token_usage, turn.token_usage_source, include_mlflow=True
        )

        root = client.start_trace(
            name="agent.turn",
            span_type="AGENT",
            inputs={"prompt": prompt},
            attributes={
                "agentops.agent_id": turn.agent_id,
                "agentops.agent_revision": "working",
                "agentops.session_id": turn.session_id,
                "agentops.turn_id": turn.turn_id,
                "agentops.model": model or "unknown",
                "agentops.permission_mode": permission_mode or "unknown",
                "agentops.status": status_name,
                "agentops.tool_count": len(tool_events),
                "agentops.tool_error_count": sum(_tool_event_failed(turn, item) for item in tool_events),
                "agentops.subagent_count": len(turn.runtime_agents),
                "agentops.telemetry_source": "codex-plugin-hooks",
                "agentops.phase": "1",
                "agentops.skeleton_version": __version__,
                "agentops.harness_checksum": harness_checksum,
                "agentops.skills_checksum": skills_checksum,
                "agentops.skill_checksums": json.dumps(skill_checksums, sort_keys=True),
                "mlflow.llm.model": model or "unknown",
                **token_attributes,
            },
            tags={
                "agentops.agent_id": turn.agent_id,
                "agentops.session_id": turn.session_id,
                "agentops.turn_id": turn.turn_id,
                "agentops.turn_key": turn.turn_key,
                "agentops.status": status_name,
                "agentops.telemetry_source": "codex-plugin-hooks",
                "mlflow.trace.user": user_name,
                "mlflow.trace.session": turn.session_id,
            },
            experiment_id=experiment_id,
            start_time_ns=start_ns,
        )
        try:
            agent_spans: dict[str, Any] = {}
            for agent in turn.runtime_agents:
                agent_start_ns, agent_end_ns = _agent_bounds(turn, agent)
                agent_events = _events_for_agent(turn.events, agent.agent_id)
                agent_model = _first_present(agent_events, "model")
                agent_span = client.start_span(
                    name=f"agent.subagent.{agent.agent_type}",
                    trace_id=root.trace_id,
                    parent_id=root.span_id,
                    span_type="AGENT",
                    inputs={
                        "agent_id": agent.agent_id,
                        "agent_type": agent.agent_type,
                    },
                    attributes={
                        "agentops.runtime_agent_id": agent.agent_id,
                        "agentops.runtime_agent_type": agent.agent_type,
                        "agentops.parent_session_id": turn.session_id,
                        "agentops.turn_id": agent.turn_id,
                        "agentops.model": agent_model or model or "unknown",
                        **_token_attributes(
                            agent.token_usage,
                            agent.token_usage_source,
                            include_mlflow=False,
                        ),
                    },
                    start_time_ns=agent_start_ns,
                )
                agent_spans[agent.agent_id] = agent_span

            for index, event in enumerate(tool_events, start=1):
                runtime_agent_id = str(event.payload.get("codex_agent_id") or "")
                parent = agent_spans.get(runtime_agent_id, root)
                observation = _tool_observation(turn, event)
                self._write_tool_span(client, root, parent, event, observation, index)

            for agent in reversed(turn.runtime_agents):
                span = agent_spans[agent.agent_id]
                agent_events = _events_for_agent(turn.events, agent.agent_id)
                stop = _last(agent_events, "SubagentStop")
                child_tools = tuple(
                    item for item in agent_events if item.event_name == "PostToolUse"
                )
                failed = any(
                    _tool_event_failed(turn, item)
                    for item in child_tools
                )
                _, agent_end_ns = _agent_bounds(turn, agent)
                client.end_span(
                    trace_id=root.trace_id,
                    span_id=span.span_id,
                    outputs={
                        "response": (
                            stop.payload.get("last_assistant_message") if stop else None
                        )
                    },
                    attributes={
                        "agentops.status": "error" if failed else "ok",
                        "agentops.tool_count": len(child_tools),
                    },
                    status="ERROR" if failed else "OK",
                    end_time_ns=agent_end_ns,
                )
            outputs: dict[str, Any] = {"response": response, "status": status_name}
            client.end_trace(
                root.trace_id,
                outputs=outputs,
                attributes={
                    "agentops.status": status_name,
                    "agentops.completed_event": turn.terminal_event,
                },
                status=mlflow_status,
                end_time_ns=max(end_ns, start_ns + 1),
            )
            import mlflow

            mlflow.flush_trace_async_logging()
        except Exception:
            try:
                client.end_trace(
                    root.trace_id,
                    outputs={"status": "export_error"},
                    attributes={"agentops.status": "export_error"},
                    status="ERROR",
                )
            except Exception:
                pass
            raise
        return root.trace_id

    @staticmethod
    def _write_tool_span(
        client: Any,
        root: Any,
        parent: Any,
        event: StoredEvent,
        observation: ToolObservation | None,
        index: int,
    ) -> None:
        payload = event.payload
        failed = tool_failed(payload) or (observation is not None and not observation.success)
        tool_name = str(payload.get("tool_name") or "unknown")
        start_ns = _tool_start_ns(observation) if observation else event.created_ns
        end_ns = observation.observed_ns if observation else event.created_ns + 1
        duration_attributes: dict[str, Any]
        if observation is None:
            duration_attributes = {"agentops.duration.available": False}
        else:
            duration_attributes = {
                "agentops.duration.available": True,
                "agentops.duration.ms": observation.duration_ms,
                "agentops.duration.source": "codex-otel",
                "agentops.otel.tool_name": observation.tool_name,
            }
        span = client.start_span(
            name=f"tool.{tool_name}",
            trace_id=root.trace_id,
            parent_id=parent.span_id,
            span_type="TOOL",
            inputs=payload.get("tool_input"),
            attributes={
                "agentops.tool_name": tool_name,
                "agentops.tool_use_id": payload.get("tool_use_id") or "unknown",
                "agentops.tool_index": index,
                "agentops.tool_error": failed,
                "agentops.runtime_agent_id": payload.get("codex_agent_id") or "root",
                **duration_attributes,
            },
            start_time_ns=start_ns,
        )
        client.end_span(
            trace_id=root.trace_id,
            span_id=span.span_id,
            outputs=payload.get("tool_response"),
            attributes={"agentops.tool_error": failed},
            status="ERROR" if failed else "OK",
            end_time_ns=max(end_ns, start_ns + 1),
        )


def export_pending(
    outbox: Outbox,
    settings: Settings,
    *,
    limit: int = 100,
    attempts_per_turn: int = 3,
    exporter_factory: Callable[[Settings], Any] = MlflowTurnExporter,
    sleep: Callable[[float], None] = time.sleep,
    require_usage: bool = False,
) -> ExportResult:
    exporter = exporter_factory(settings)
    uploaded = 0
    failed = 0
    trace_ids: list[str] = []
    for _ in range(limit):
        turn = outbox.claim(require_usage=require_usage)
        if turn is None:
            break
        last_error: Exception | None = None
        for attempt in range(attempts_per_turn):
            try:
                trace_id = exporter.export_turn(turn)
                outbox.mark_uploaded(turn.turn_key, trace_id)
                uploaded += 1
                trace_ids.append(trace_id)
                last_error = None
                break
            except Exception as exc:  # network/auth failures must not block Codex
                last_error = exc
                if attempt + 1 < attempts_per_turn:
                    sleep(2**attempt)
        if last_error is not None:
            retry_after = min(300.0, float(2 ** min(turn.attempts + 3, 8)))
            outbox.mark_failed(turn.turn_key, str(last_error), retry_after_seconds=retry_after)
            failed += 1
    return ExportResult(uploaded, failed, tuple(trace_ids))


def _first(events: tuple[StoredEvent, ...], event_name: str) -> StoredEvent | None:
    return next((item for item in events if item.event_name == event_name), None)


def _last(events: tuple[StoredEvent, ...], event_name: str) -> StoredEvent | None:
    return next((item for item in reversed(events) if item.event_name == event_name), None)


def _first_present(events: tuple[StoredEvent, ...], key: str) -> Any:
    return next((item.payload[key] for item in events if item.payload.get(key)), None)


def _token_attributes(usage: Any, source: str | None, *, include_mlflow: bool) -> dict[str, Any]:
    attributes: dict[str, Any] = {"agentops.token_usage.available": usage is not None}
    if usage is None:
        return attributes
    attributes.update(
        {
            "agentops.token_usage.source": source or "unknown",
            "agentops.token_usage.exact": True,
            "agentops.tokens.input": usage.input_tokens,
            "agentops.tokens.output": usage.output_tokens,
            "agentops.tokens.cached_input": usage.cached_input_tokens,
            "agentops.tokens.cache_write_input": usage.cache_write_input_tokens,
            "agentops.tokens.reasoning_output": usage.reasoning_output_tokens,
            "agentops.tokens.total": usage.total_tokens,
        }
    )
    if include_mlflow:
        attributes["mlflow.chat.tokenUsage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cache_read_input_tokens": usage.cached_input_tokens,
            "cache_creation_input_tokens": usage.cache_write_input_tokens,
        }
    return attributes


def _events_for_agent(
    events: tuple[StoredEvent, ...], runtime_agent_id: str
) -> tuple[StoredEvent, ...]:
    return tuple(
        item
        for item in events
        if str(item.payload.get("codex_agent_id") or "") == runtime_agent_id
    )


def _tool_observation(turn: PendingTurn, event: StoredEvent) -> ToolObservation | None:
    call_id = str(event.payload.get("tool_use_id") or "")
    if not call_id:
        return None
    conversation_id = str(event.payload.get("codex_agent_id") or turn.session_id)
    matches = [
        item
        for item in turn.tool_observations
        if item.call_id == call_id and item.conversation_id == conversation_id
    ]
    if not matches:
        matches = [item for item in turn.tool_observations if item.call_id == call_id]
    if not matches:
        return None
    return min(matches, key=lambda item: abs(item.observed_ns - event.created_ns))


def _tool_start_ns(observation: ToolObservation) -> int:
    duration_ns = int(observation.duration_ms * 1_000_000)
    return max(1, observation.observed_ns - duration_ns)


def _tool_event_failed(turn: PendingTurn, event: StoredEvent) -> bool:
    observation = _tool_observation(turn, event)
    return tool_failed(event.payload) or (observation is not None and not observation.success)


def _agent_bounds(turn: PendingTurn, agent: RuntimeAgent) -> tuple[int, int]:
    events = _events_for_agent(turn.events, agent.agent_id)
    starts = [agent.started_ns]
    ends = [agent.ended_ns or agent.started_ns]
    for event in events:
        starts.append(event.created_ns)
        ends.append(event.created_ns)
        if event.event_name == "PostToolUse":
            observation = _tool_observation(turn, event)
            if observation is not None:
                starts.append(_tool_start_ns(observation))
                ends.append(observation.observed_ns)
    start_ns = min(starts)
    return start_ns, max(max(ends), start_ns + 1)
