"""Exercise both exporters in an isolated MLflow experiment with synthetic metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import mlflow
from databricks.sdk import WorkspaceClient
from mlflow import MlflowClient

from codex_agentops.config import Settings
from codex_agentops.events import HookEvent
from codex_agentops.mlflow_exporter import MlflowTurnExporter
from codex_agentops.otel_bridge import OtelMlflowExporter, OtelSpan, OtelTraceAssembler
from codex_agentops.outbox import Outbox
from codex_agentops.usage import TokenUsage, parse_transcript_websearch


def _verify_codex(settings: Settings, client: MlflowClient, experiment_id: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        outbox = Outbox(Path(directory) / "outbox.db")
        now = time.time_ns()
        turn_key = hashlib.sha256(b"synthetic-validation-turn").hexdigest()
        for index, name in enumerate(("UserPromptSubmit", "Stop")):
            outbox.append(HookEvent(
                event_id=f"synthetic-{index}", turn_key=turn_key, event_name=name,
                session_id="synthetic-codex-session", turn_id="synthetic-codex-turn",
                agent_id="synthetic-agent", agent_root=directory,
                created_ns=now + index * 1_000_000,
                payload={"runtime": "codex", "agent_type": "skill-based-agent"},
            ))
        transcript = Path(directory) / "synthetic.jsonl"
        transcript.write_text(json.dumps({"type": "event_msg", "payload": {
            "type": "item_completed", "thread_id": "synthetic-codex-session",
            "turn_id": "synthetic-codex-turn", "completed_at_ms": now // 1_000_000,
            "item": {"type": "WebSearch", "id": "synthetic-search", "query": ""},
        }}) + "\n", encoding="utf-8")
        for call in parse_transcript_websearch(transcript, session_id="synthetic-codex-session"):
            outbox.append_transcript_websearch(
                session_id="synthetic-codex-session", turn_id=call.turn_id,
                call_id=call.call_id, observed_ns=call.completed_ns,
            )
        outbox.append_token_usage(
            event_id="synthetic-usage", session_id="synthetic-codex-session",
            turn_id="synthetic-codex-turn", observed_ns=now + 500_000,
            source="transcript", usage=TokenUsage(10, 5, total_tokens=15),
        )
        turn = outbox.claim()
        assert turn is not None
        trace = client.get_trace(MlflowTurnExporter(settings).export_turn(turn))
        root = next(span for span in trace.data.spans if span.name == "agent.turn")
        tools = [span for span in trace.data.spans if span.span_type == "TOOL"]
        assert len(tools) == 1
        assert tools[0].attributes["agentops.tool_use_id"] == "synthetic-search"
        assert tools[0].attributes["agentops.capture_source"] == "codex-transcript"
        assert tools[0].inputs is None and tools[0].outputs is None
        assert root.attributes["agentops.token_usage.available"] is True
        assert trace.info.token_usage["total_tokens"] == 15
        assert trace.info.experiment_id == experiment_id
        print("A: remote trace=ok, WebSearch ID=ok, token usage=ok")


def _verify_omnigent(settings: Settings, client: MlflowClient, experiment_id: str) -> None:
    now = time.time_ns()
    root = OtelSpan(
        trace_id="11" * 16, span_id="22" * 8, parent_span_id=None,
        name="agent:synthetic-agent", start_ns=now, end_ns=now + 4_000_000,
        status="OK", resource={"agentops.agent_id": "synthetic-agent"},
        attributes={"openinference.span.kind": "AGENT", "agent.name": "synthetic-agent",
                    "session.id": "synthetic-omnigent-session",
                    "gen_ai.usage.input_tokens": 4, "gen_ai.usage.output_tokens": 2},
    )
    launch = OtelSpan(
        trace_id=root.trace_id, span_id="33" * 8, parent_span_id=root.span_id,
        name="tool:sys_session_send", start_ns=now + 100, end_ns=now + 500,
        status="OK", resource=root.resource,
        attributes={"openinference.span.kind": "TOOL", "tool.name": "sys_session_send",
                    "agentops.subagent.child_session_id": "synthetic-child",
                    "agentops.subagent.name": "validator"},
    )
    child = OtelSpan(
        trace_id="44" * 16, span_id="55" * 8, parent_span_id=None,
        name="agent:validator", start_ns=now + 1_000, end_ns=now + 3_000,
        status="OK", resource=root.resource,
        attributes={"openinference.span.kind": "AGENT", "agent.name": "validator",
                    "session.id": "synthetic-child"},
    )
    assembler = OtelTraceAssembler(grace_seconds=0, link_wait_seconds=5)
    assert assembler.ingest((root, launch), now_ns=1_000_000_000) == ()
    groups = assembler.ingest((child,), now_ns=2_000_000_000)
    assert len(groups) == 1
    trace = client.get_trace(OtelMlflowExporter(settings).export(groups[0]))
    parent_span = next(span for span in trace.data.spans if span.name == "agent.turn")
    child_span = next(span for span in trace.data.spans if span.name == "agent.subagent.validator")
    assert child_span.parent_id == parent_span.span_id
    assert parent_span.attributes["agentops.subagent.trace_coverage"] == "linked"
    assert trace.info.token_usage["total_tokens"] == 6
    assert trace.info.experiment_id == experiment_id
    print("B: remote trace=ok, late child linkage=ok, observed token usage=ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    workspace = WorkspaceClient(profile=args.profile)
    user = workspace.current_user.me().user_name
    if not user:
        raise RuntimeError("Unable to identify the private experiment owner")
    experiment_name = (
        f"/Users/{user}/agentops-monitoring-validation-"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    )
    tracking_uri = f"databricks://{args.profile}"
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    experiment_id = client.create_experiment(experiment_name)
    mlflow.set_experiment(experiment_id=experiment_id)
    settings = Settings(args.profile, workspace.config.host, experiment=experiment_name)
    _verify_codex(settings, client, experiment_id)
    _verify_omnigent(settings, client, experiment_id)
    print("Isolated validation experiment retained for inspection (no customer content).")


if __name__ == "__main__":
    main()
