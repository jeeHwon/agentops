from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from . import __version__
from .call_metrics import metric_name, observed_calls
from .events import CONTENT_CAPTURE_DISABLED
from .mlflow_exporter import (
    ExportResult, MlflowTurnExporter, _token_attributes, _tool_event_failed,
)
from .outbox import Outbox, PendingSession, PendingTurn, SessionTurn
from .redaction import redact_text, trace_maxlen
from .usage import TokenUsage


CUSTOM_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
SENSITIVE_KEY = re.compile(
    r"(?i)(password|secret|token|api.?key|credential|phone|mobile|ssn|"
    r"(?:employee|customer|user|personal|resident)[_-]?(?:id|number)|"
    r"(?:bank|account)[_-]?number)"
)


def _turn(session: PendingSession, item: SessionTurn) -> PendingTurn:
    return PendingTurn(
        turn_key="", session_id=session.session_id, turn_id=item.turn_id,
        agent_id=session.agent_id, agent_root=session.agent_root,
        terminal_event="Interrupt" if any(
            event.event_name == "Interrupt" for event in item.events
        ) else "Stop",
        attempts=0, events=item.events, token_usage=item.token_usage,
        token_usage_source=item.token_usage_source,
        runtime_agents=item.runtime_agents, tool_observations=item.tool_observations,
    )


def _metrics(session: PendingSession) -> dict[str, Any]:
    activity = Counter()
    tool_names = Counter()
    agent_types = Counter()
    observed_usages: list[TokenUsage] = []
    cache_complete = True
    for item in session.turns:
        activity["turns_user"] += any(event.event_name == "UserPromptSubmit" for event in item.events)
        activity["turns_assistant"] += any(event.event_name == "Stop" for event in item.events)
        activity["subagent_calls"] += len(item.runtime_agents)
        for agent in item.runtime_agents:
            agent_types[metric_name(agent.agent_type)] += 1
        turn = _turn(session, item)
        calls = observed_calls(item.events)
        activity["tool_calls"] += len(calls)
        activity["tool_errors"] += sum(
            post is not None and _tool_event_failed(turn, post)
            for _, post in calls
        )
        activity["tool_calls_unfinished"] += sum(post is None for _, post in calls)
        for prior, post in calls:
            tool_names[metric_name(str((post or prior).payload.get("tool_name") or "unknown"))] += 1
        if item.token_usage is not None:
            observed_usages.append(item.token_usage)
        cache_complete = cache_complete and item.token_usage_complete

    values: dict[str, Any] = {
        f"agentops.metrics.{key}": activity[key]
        for key in ("turns_user", "turns_assistant", "tool_calls", "tool_errors",
                    "subagent_calls", "tool_calls_unfinished")
    }
    values["agentops.metrics.session_duration_available"] = (
        session.ended_ns > session.started_ns
    )
    if session.ended_ns > session.started_ns:
        values["agentops.metrics.session_duration_sec"] = (
            session.ended_ns - session.started_ns
        ) / 1_000_000_000
    for name, count in tool_names.items():
        values[f"agentops.metrics.toolcall.{name}"] = count
    for name, count in agent_types.items():
        values[f"agentops.metrics.subagent.{name}"] = count

    values["agentops.token_usage.observed_turns"] = len(observed_usages)
    values["agentops.token_usage.total_turns"] = len(session.turns)
    values["agentops.token_usage.complete"] = bool(session.turns) and cache_complete and (
        len(observed_usages) == len(session.turns)
    )
    values["agentops.token_usage.available"] = bool(observed_usages)
    values["agentops.token_usage.exact"] = False
    if observed_usages:
        combined = observed_usages[0]
        for usage in observed_usages[1:]:
            combined += usage
        complete = values["agentops.token_usage.complete"]
        prefix = "" if complete else "observed_"
        for key, amount in (
            ("input_tokens", combined.input_tokens),
            ("output_tokens", combined.output_tokens),
            ("total_tokens", combined.input_tokens + combined.output_tokens),
        ):
            values[f"agentops.metrics.{prefix}{key}"] = amount
        cache_read_complete = complete and all(
            usage.cache_read_available for usage in observed_usages
        )
        cache_creation_complete = complete and all(
            usage.cache_creation_available for usage in observed_usages
        )
        if cache_read_complete:
            values[f"agentops.metrics.{prefix}cache_read_tokens"] = combined.cached_input_tokens
        elif combined.cached_input_tokens > 0 or any(
            usage.cache_read_available for usage in observed_usages
        ):
            values["agentops.metrics.observed_cache_read_tokens"] = combined.cached_input_tokens
        if cache_creation_complete:
            values[f"agentops.metrics.{prefix}cache_creation_tokens"] = combined.cache_write_input_tokens
        elif combined.cache_write_input_tokens > 0 or any(
            usage.cache_creation_available for usage in observed_usages
        ):
            values["agentops.metrics.observed_cache_creation_tokens"] = combined.cache_write_input_tokens
        values["agentops.token_usage.total_consistent"] = all(
            usage.total_tokens == usage.input_tokens + usage.output_tokens
            for usage in observed_usages
        )
        if not values["agentops.token_usage.total_consistent"]:
            values["agentops.metrics.reported_total_tokens"] = combined.total_tokens
        values["agentops.token_usage.cache_read_available"] = cache_read_complete
        values["agentops.token_usage.cache_creation_available"] = cache_creation_complete
    return values


def _run_summary(root: str, *, capture_content: bool) -> tuple[dict[str, float], dict[str, str]]:
    path = Path(root) / "run_summary.json"
    if not path.exists():
        return {}, {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("run_summary.json must be a regular file of at most 64 KiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("run_summary.json must be an object")
    metrics: dict[str, float] = {}
    tags: dict[str, str] = {}
    for key, value in list(data.items())[:64]:
        if not isinstance(key, str) or not CUSTOM_KEY.fullmatch(key) or SENSITIVE_KEY.search(key):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if finite:
                metrics[f"agentops.metrics.custom.{key}"] = value
        elif capture_content:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            tags[f"custom.{key}"] = redact_text(text, limit=min(trace_maxlen(), 1024))
    return metrics, tags


def _visible(value: Any) -> Any:
    return None if value == CONTENT_CAPTURE_DISABLED else value


class MlflowSessionExporter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.turn_exporter = MlflowTurnExporter(settings)

    def export_session(self, session: PendingSession) -> str:
        binding = next((
            event.payload["tracing"] for item in session.turns for event in item.events
            if isinstance(event.payload.get("tracing"), dict)
            and event.payload["tracing"].get("experiment_id")
        ), None)
        if binding is not None:
            from .tracing import TraceStore

            if binding["workspace_host"].rstrip("/") != self.settings.host.rstrip("/"):
                raise RuntimeError("Session trace binding belongs to another workspace")
            store = TraceStore(self.settings.profile, binding)
            store.verify_location()
            with store.context():
                import mlflow

                mlflow.set_experiment(experiment_id=binding["experiment_id"])
                return self._export(session, store.client, binding["experiment_id"])
        if self.settings.experiment_id and session.turns:
            raise RuntimeError("Session has no captured UC trace binding; use its original configuration")
        client, experiment_id = self.turn_exporter._client_and_experiment()
        return self._export(session, client, experiment_id)

    def _export(self, session: PendingSession, client: Any, experiment_id: str) -> str:
        ordered = sorted(session.turns, key=lambda item: min(
            (event.created_ns for event in item.events), default=session.started_ns
        ))
        all_events = tuple(event for item in ordered for event in item.events)
        first_prompt = next((
            event.payload.get("prompt") for event in all_events
            if event.event_name == "UserPromptSubmit"
        ), None)
        last_response = next((
            event.payload.get("last_assistant_message") for event in reversed(all_events)
            if event.event_name == "Stop"
        ), None)
        metadata = next((event.payload for event in all_events if event.event_name == "UserPromptSubmit"), {})
        start_ns = min([session.started_ns] + [event.created_ns for event in all_events])
        end_ns = max(session.ended_ns, *(event.created_ns for event in all_events), start_ns + 1)
        failed_turns = any(
            _turn(session, item).terminal_event == "Interrupt" or any(
                post is not None and _tool_event_failed(_turn(session, item), post)
                for _, post in observed_calls(item.events)
            ) for item in ordered
        )
        root = client.start_trace(
            name="agent.session", span_type="AGENT",
            inputs={"prompt": _visible(first_prompt)},
            attributes={
                "agentops.agent_id": session.agent_id,
                "agentops.session_id": session.session_id,
                "agentops.trace_role": "session_summary",
                "agentops.evaluation_inputs_available": False,
                "agentops.canonical_turn_traces": [item.trace_id for item in ordered],
                "agentops.session_status": "error" if failed_turns else "ok",
                "agentops.end_reason": session.end_reason,
                "agentops.adapter_version": __version__,
                **_metrics(session),
                **session.custom_metrics,
            },
            tags={
                "agentops.agent_id": session.agent_id,
                "agentops.project": str(metadata.get("project") or session.agent_id),
                "agentops.session_id": session.session_id,
                "agentops.git_commit": str(metadata.get("git_commit") or "unknown"),
                "agentops.subagent_defs": (
                    json.dumps(metadata["subagent_defs"], ensure_ascii=False)
                    if isinstance(metadata.get("subagent_defs"), list) else "unknown"
                ),
                "agentops.end_reason": session.end_reason,
                "agentops.source": "codex-plugin-hooks",
                "agentops.trace_role": "session_summary",
                "agentops.evaluation_inputs_available": "false",
                "mlflow.trace.session": session.session_id,
                **session.custom_tags,
            },
            experiment_id=experiment_id, start_time_ns=start_ns,
        )
        try:
            for index, item in enumerate(ordered, start=1):
                events = item.events
                prompt = next((event.payload.get("prompt") for event in events
                               if event.event_name == "UserPromptSubmit"), None)
                response = next((event.payload.get("last_assistant_message")
                                 for event in reversed(events) if event.event_name == "Stop"), None)
                turn_start = min((event.created_ns for event in events), default=start_ns)
                turn_end = max((event.created_ns for event in events), default=turn_start + 1)
                turn_span = client.start_span(
                    name=f"turn.{index}", trace_id=root.trace_id, parent_id=root.span_id,
                    span_type="AGENT", inputs={"prompt": _visible(prompt)},
                    attributes={
                        "agentops.turn_id": item.turn_id,
                        "agentops.source_turn_trace_id": item.trace_id,
                        "agentops.token_usage.complete": item.token_usage_complete,
                        **_token_attributes(item.token_usage, item.token_usage_source,
                                            include_mlflow=False),
                    },
                    start_time_ns=turn_start,
                )
                agent_spans: dict[str, Any] = {}
                for agent in item.runtime_agents:
                    agent_spans[agent.agent_id] = client.start_span(
                        name=f"agent.subagent.{agent.agent_type}", trace_id=root.trace_id,
                        parent_id=turn_span.span_id, span_type="AGENT",
                        inputs={"agent_id": agent.agent_id, "agent_type": agent.agent_type},
                        attributes={
                            "agentops.runtime_agent_id": agent.agent_id,
                            **_token_attributes(agent.token_usage, agent.token_usage_source,
                                                include_mlflow=False),
                        },
                        start_time_ns=max(1, agent.started_ns),
                    )
                turn = _turn(session, item)
                for call_index, (prior, post) in enumerate(observed_calls(events), start=1):
                    tool = post or prior
                    tool_name = str(tool.payload.get("tool_name") or "unknown")
                    parent = agent_spans.get(str(tool.payload.get("codex_agent_id") or ""), turn_span)
                    failed = post is not None and _tool_event_failed(turn, post)
                    tool_span = client.start_span(
                        name=f"tool.{tool_name}", trace_id=root.trace_id,
                        parent_id=parent.span_id, span_type="TOOL",
                        inputs=_visible(prior.payload.get("tool_input")),
                        attributes={
                            "agentops.tool_name": tool_name,
                            "agentops.tool_index": call_index,
                            "agentops.tool_use_id": tool.payload.get("tool_use_id") or "unknown",
                            "agentops.tool_error": failed,
                            "agentops.is_error": failed,
                            "agentops.tool_result_observed": post is not None,
                            "agentops.result_status": (
                                "error" if failed else "observed" if post else "unobserved"
                            ),
                            "agentops.call_linkage": (
                                "id" if prior.event_name == "PreToolUse" and post is not None
                                and prior.payload.get("tool_use_id") == post.payload.get("tool_use_id")
                                and post.payload.get("tool_use_id") else "heuristic"
                                if prior.event_name == "PreToolUse" and post is not None
                                else "unpaired"
                            ),
                        },
                        start_time_ns=prior.created_ns,
                    )
                    tool_end = max(tool.created_ns + 1, prior.created_ns + 1)
                    client.end_span(
                        trace_id=root.trace_id, span_id=tool_span.span_id,
                        outputs=_visible(post.payload.get("tool_response")) if post else None,
                        status="ERROR" if failed else "OK" if post else "UNSET",
                        end_time_ns=tool_end,
                    )
                    turn_end = max(turn_end, tool_end)
                for agent in reversed(item.runtime_agents):
                    child_events = tuple(event for event in events if
                                         event.payload.get("codex_agent_id") == agent.agent_id)
                    last = next((event for event in reversed(child_events)
                                 if event.event_name == "SubagentStop"), None)
                    end = max(agent.started_ns + 1, agent.ended_ns or 0,
                              *(event.created_ns for event in child_events))
                    client.end_span(
                        trace_id=root.trace_id, span_id=agent_spans[agent.agent_id].span_id,
                        outputs={"response": _visible(last.payload.get("last_assistant_message"))}
                        if last else None,
                        attributes={"agentops.result_observed": last is not None},
                        status="OK" if last else "UNSET", end_time_ns=end,
                    )
                    turn_end = max(turn_end, end)
                turn_failed = turn.terminal_event == "Interrupt" or any(
                    post is not None and _tool_event_failed(turn, post)
                    for _, post in observed_calls(events)
                )
                client.end_span(
                    trace_id=root.trace_id, span_id=turn_span.span_id,
                    outputs={"response": _visible(response)},
                    status="ERROR" if turn_failed else "OK",
                    end_time_ns=max(turn_end, turn_start + 1),
                )
                end_ns = max(end_ns, turn_end)
            client.end_trace(
                root.trace_id, outputs={"response": _visible(last_response)},
                status="ERROR" if failed_turns else "OK", end_time_ns=max(end_ns, start_ns + 1),
            )
            import mlflow

            mlflow.flush_trace_async_logging()
        except Exception:
            try:
                client.end_trace(root.trace_id, outputs={"status": "export_error"}, status="ERROR")
            except Exception:
                pass
            raise
        return root.trace_id


def export_pending_sessions(
    outbox: Outbox, settings: Settings, *, limit: int = 100,
    exporter_factory: Callable[[Settings], Any] = MlflowSessionExporter,
) -> ExportResult:
    exporter = exporter_factory(settings)
    uploaded = failed = 0
    trace_ids: list[str] = []
    for _ in range(limit):
        session = outbox.claim_session()
        if session is None:
            break
        try:
            trace_id = exporter.export_session(session)
            outbox.mark_session_uploaded(session.session_id, trace_id)
            trace_ids.append(trace_id)
            uploaded += 1
        except Exception as exc:
            retry_after = min(300.0, float(2 ** min(session.attempts + 3, 8)))
            outbox.mark_session_failed(
                session.session_id, str(exc), retry_after_seconds=retry_after
            )
            failed += 1
    return ExportResult(uploaded, failed, tuple(trace_ids))
