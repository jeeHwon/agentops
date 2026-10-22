"""Verify the Skill-based Agent hook exporter in an isolated UC MLflow experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from mlflow import MlflowClient
from mlflow.entities.trace_location import UnityCatalog

from codex_agentops.config import Settings
from codex_agentops.events import CONTENT_CAPTURE_DISABLED, HookEvent
from codex_agentops.mlflow_exporter import MlflowTurnExporter
from codex_agentops.outbox import Outbox
from codex_agentops.session_exporter import MlflowSessionExporter
from codex_agentops.tracing import TraceStore, configure_tracing
from codex_agentops.usage import TokenUsage


def verify(settings: Settings, client: MlflowClient) -> None:
    with tempfile.TemporaryDirectory(prefix="agentops-hook-verify-") as workspace:
        root = Path(workspace)
        outbox = Outbox(root / "events.db")
        session_id = "synthetic-hook-verification"
        turn_id = "synthetic-turn"
        turn_key = hashlib.sha256(f"{session_id}:{turn_id}".encode()).hexdigest()
        start_ns = time.time_ns() - 3_000_000_000
        outbox.record_session(session_id=session_id, agent_id="synthetic-agent",
                              agent_root=workspace, observed_ns=start_ns)
        events = (
            ("UserPromptSubmit", turn_id, {"prompt": CONTENT_CAPTURE_DISABLED}),
            ("SubagentStart", "child-turn", {"codex_agent_id": "child-1",
                                                "codex_agent_type": "explorer"}),
            ("PreToolUse", "child-turn", {"codex_agent_id": "child-1",
                                            "tool_name": "Bash", "tool_use_id": "tool-one",
                                            "tool_input": CONTENT_CAPTURE_DISABLED}),
            ("PostToolUse", "child-turn", {"codex_agent_id": "child-1",
                                             "tool_name": "Bash", "tool_use_id": "tool-one",
                                             "tool_failed": True,
                                             "tool_response": CONTENT_CAPTURE_DISABLED}),
            ("SubagentStop", "child-turn", {"codex_agent_id": "child-1",
                                               "last_assistant_message": CONTENT_CAPTURE_DISABLED}),
            ("Stop", turn_id, {"last_assistant_message": CONTENT_CAPTURE_DISABLED}),
        )
        for index, (name, event_turn, data) in enumerate(events, start=1):
            outbox.append(HookEvent(
                event_id=f"synthetic-{index}", turn_key=turn_key,
                event_name=name, session_id=session_id, turn_id=event_turn,
                agent_id="synthetic-agent", agent_root=workspace,
                created_ns=start_ns + index * 20_000_000,
                payload={"tracing": settings.tracing, "agent_type": "skill-based-agent",
                         "source": "synthetic-verification", **data},
            ))
        outbox.append_token_usage(
            event_id="root-usage", session_id=session_id, turn_id=turn_id,
            observed_ns=start_ns + 110_000_000, source="transcript",
            usage=TokenUsage.from_dict({"input_tokens": 10, "output_tokens": 5,
                                        "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                        "total_tokens": 15}),
        )
        outbox.append_token_usage(
            event_id="child-usage", session_id="child-1", turn_id="child-turn",
            observed_ns=start_ns + 85_000_000, source="transcript",
            usage=TokenUsage.from_dict({"input_tokens": 4, "output_tokens": 2,
                                        "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                        "total_tokens": 6}),
        )
        outbox.record_session(session_id=session_id, agent_id="synthetic-agent",
                              agent_root=workspace, observed_ns=start_ns + 160_000_000,
                              ended=True, end_reason="other",
                              custom_metrics={"agentops.metrics.custom.checks_passed": 2},
                              custom_tags={"custom.note": "synthetic"})
        turn = outbox.claim(require_usage=True)
        assert turn is not None and turn.token_usage is not None
        turn_trace_id = MlflowTurnExporter(settings).export_turn(turn)
        outbox.mark_uploaded(turn.turn_key, turn_trace_id)
        session = outbox.claim_session()
        assert session is not None
        session_trace_id = MlflowSessionExporter(settings).export_session(session)
        outbox.mark_session_uploaded(session.session_id, session_trace_id)

        store = TraceStore(settings.profile, settings.tracing, client=client)
        canonical = store.get(turn_trace_id)
        summary = store.get(session_trace_id)
        root_span = next(span for span in summary.data.spans if span.name == "agent.session")
        turn_span = next(span for span in summary.data.spans if span.name == "turn.1")
        child_span = next(span for span in summary.data.spans
                          if span.name == "agent.subagent.explorer")
        tool_span = next(span for span in summary.data.spans if span.name == "tool.Bash")
        assert summary.info.tags["agentops.trace_role"] == "session_summary"
        assert canonical.info.tags["agentops.trace_role"] == "canonical_turn"
        assert summary.info.token_usage is None
        assert canonical.info.token_usage["total_tokens"] == 21
        assert turn_span.parent_id == root_span.span_id
        assert child_span.parent_id == turn_span.span_id
        assert int(turn_span.attributes["agentops.tokens.total"]) == 21
        assert int(child_span.attributes["agentops.tokens.total"]) == 6
        assert summary.info.tags["agentops.project"] == "synthetic-agent"
        assert summary.info.tags["agentops.session_id"] == session_id
        assert summary.info.tags["agentops.end_reason"] == "other"
        assert summary.info.tags["agentops.source"] == "codex-plugin-hooks"
        assert summary.info.tags["agentops.git_commit"] == "unknown"
        assert summary.info.tags["agentops.subagent_defs"] == "unknown"
        assert summary.info.tags["custom.note"] == "synthetic"
        for key, expected in {
            "turns_user": 1, "turns_assistant": 1, "tool_calls": 1, "tool_errors": 1,
            "subagent_calls": 1, "toolcall.Bash": 1, "subagent.explorer": 1,
            "input_tokens": 14, "output_tokens": 7, "total_tokens": 21,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "custom.checks_passed": 2,
        }.items():
            assert int(root_span.attributes[f"agentops.metrics.{key}"]) == expected
        assert float(root_span.attributes["agentops.metrics.session_duration_sec"]) > 0
        assert str(root_span.attributes["agentops.token_usage.complete"]).lower() == "true"
        assert str(root_span.attributes["agentops.token_usage.exact"]).lower() == "false"
        assert tool_span.parent_id == child_span.span_id
        assert str(tool_span.attributes["agentops.tool_error"]).lower() == "true"
        assert str(tool_span.attributes["agentops.tool_result_observed"]).lower() == "true"
        assert all(
            (json.loads(span.inputs) if isinstance(span.inputs, str) else span.inputs)
            in (None, {"prompt": None}, {"agent_id": "child-1", "agent_type": "explorer"})
            for span in summary.data.spans
        )
        with store.context():
            traces = client.search_traces(locations=[settings.experiment_id],
                                          max_results=100, include_spans=True)
        assert {turn_trace_id, session_trace_id}.issubset({trace.info.trace_id for trace in traces})
        print("UC MLflow: two synthetic traces read back; session hierarchy, metrics, "
              "token coverage and content suppression verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--registry", required=True, help="UC catalog.schema")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--experiment", help="Existing or new dedicated UC experiment path")
    args = parser.parse_args()
    workspace = WorkspaceClient(profile=args.profile)
    user = workspace.current_user.me().user_name
    if not user:
        raise RuntimeError("Cannot determine the validation experiment owner")
    experiment_name = args.experiment or (
        f"/Users/{user}/agentops-hook-metrics-"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    )
    settings = Settings(profile=args.profile, host=workspace.config.host,
                        warehouse_id=args.warehouse_id, registry=args.registry,
                        experiment=experiment_name, codex_enabled=True)
    binding = configure_tracing(settings)
    settings = replace(settings, experiment_id=binding["experiment_id"])
    client = MlflowClient(tracking_uri=f"databricks://{args.profile}")
    experiment = client.get_experiment(settings.experiment_id)
    if not isinstance(experiment.trace_location, UnityCatalog):
        raise RuntimeError("Validation experiment is not UC-backed")
    verify(settings, client)


if __name__ == "__main__":
    main()
