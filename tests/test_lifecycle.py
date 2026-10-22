from __future__ import annotations

import json
import math
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_agentops.config import Settings
from codex_agentops.governance import ReleaseGovernance
from codex_agentops.manifest import ManifestError
from codex_agentops.project import create_project, load_project, save_project
from codex_agentops.registry import RegistryError, parse_registry
from codex_agentops.release import build_release, release_to_dict, write_release_metadata
from codex_agentops.tracing import TraceStore, configure_tracing, evaluate_traces, validate_trace_evidence


HOST = "https://synthetic.cloud.databricks.com"


def binding():
    return {
        "experiment_id": "1", "workspace_host": HOST,
        "catalog": "main", "schema": "aops", "warehouse_id": "warehouse",
    }


@pytest.fixture
def lifecycle(tmp_path):
    root = create_project("test-agent", tmp_path / "genie", build_type="no-code")
    project = load_project(root)
    project["tracing"] = binding()
    save_project(root, project)
    release = build_release(root, "1.0.0", "main.aops")
    write_release_metadata(release)
    registered = SimpleNamespace(
        agent_id=release.agent_id, version="1.0.0", registered_by="publisher@example.com",
        artifact_sha256="a" * 64, harness_sha256=release.harness_sha256,
        skills_sha256=release.skills_sha256, release_manifest=release_to_dict(release),
    )
    trace = SimpleNamespace(
        info=SimpleNamespace(trace_id="trace-1", experiment_id="1", state="OK", tags={
            "agentops.agent_id": "test-agent", "agentops.agent_revision": "1.0.0",
            "agentops.evaluation_inputs_available": "true",
        }),
        data=SimpleNamespace(spans=[SimpleNamespace(
            parent_id=None, name="agent.invoke", span_type="AGENT", start_time_ns=0,
            end_time_ns=2_000_000_000, inputs={"prompt": "question"}, outputs={"response": "answer"},
            attributes={
                "agentops.harness_checksum": release.harness_sha256,
                "agentops.skills_checksum": release.skills_sha256,
            },
        )]),
    )
    run = SimpleNamespace(
        info=SimpleNamespace(status="FINISHED", experiment_id="1"),
        data=SimpleNamespace(tags={
            "agentops.agent_id": "test-agent", "agentops.agent_version": "1.0.0",
            "agentops.artifact_sha256": registered.artifact_sha256,
            "agentops.evaluation_source": "verified-mlflow-traces", "agentops.judges_complete": "true",
        }, metrics={
            "correctness/mean": 0.9, "relevance_to_query/mean": 0.95, "safety/mean": 1.0,
            "agentops.latency_p90_seconds": 2.0,
        }),
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"trace_ids": ["trace-1"]}))
    store = SimpleNamespace(
        binding=binding(), context=nullcontext, verify_location=lambda: None,
        get=lambda trace_id: trace,
        client=SimpleNamespace(get_run=lambda run_id: run, download_artifacts=lambda *args: evidence),
    )
    sql = []
    registry = SimpleNamespace(
        profile="synthetic", location=parse_registry("main.aops"),
        get=lambda *args: registered,
        _execute_sql=lambda statement, parameters=None: sql.append((statement, parameters)) or [["reviewer@example.com"]],
    )
    governance = ReleaseGovernance(registry)
    governance._store = lambda release: store
    return SimpleNamespace(
        root=root, release=registered, trace=trace, run=run, store=store,
        registry=registry, governance=governance, sql=sql,
    )


def test_review_uses_real_evidence_metrics_and_independent_actor(lifecycle):
    result = lifecycle.governance.review("test-agent", "1.0.0", run_id="run-1", trace_id="trace-1")
    assert result["decision"] == "approved"
    statement, parameters = lifecycle.sql[-1]
    assert "INSERT INTO `main`.`aops`.`release_gates`" in statement
    assert json.loads(parameters["checks"])["artifact_sha256"] == lifecycle.release.artifact_sha256


def test_review_rejects_owner_or_publisher(lifecycle):
    for actor in ("OWNER@example.com", "publisher@example.com"):
        lifecycle.registry._execute_sql = lambda *args: [[actor]]
        with pytest.raises(RegistryError, match="independent reviewer"):
            lifecycle.governance.review("test-agent", "1.0.0", run_id="run-1", trace_id="trace-1")


@pytest.mark.parametrize("metric,value", [
    ("correctness/mean", math.nan), ("safety/mean", math.inf),
    ("safety/mean", 2), ("agentops.latency_p90_seconds", -1),
])
def test_review_fails_closed_on_invalid_metrics(lifecycle, metric, value):
    lifecycle.run.data.metrics[metric] = value
    with pytest.raises(RegistryError, match="invalid evaluation metric"):
        lifecycle.governance.check_evaluation(lifecycle.release, "run-1", "trace-1")


def test_review_records_failed_threshold_as_rejected(lifecycle):
    lifecycle.run.data.metrics["safety/mean"] = 0.5
    result = lifecycle.governance.review("test-agent", "1.0.0", run_id="run-1", trace_id="trace-1")
    assert result["decision"] == "rejected"
    assert lifecycle.sql[-1][1]["decision"] == "rejected"


@pytest.mark.parametrize("mutation", ["run-status", "experiment", "artifact", "judge-error", "trace-version", "checksum", "empty-spans"])
def test_review_rejects_unrelated_or_incomplete_evidence(lifecycle, mutation):
    if mutation == "run-status":
        lifecycle.run.info.status = "RUNNING"
    elif mutation == "experiment":
        lifecycle.run.info.experiment_id = "other"
    elif mutation == "artifact":
        lifecycle.run.data.tags["agentops.artifact_sha256"] = "wrong"
    elif mutation == "judge-error":
        lifecycle.run.data.tags.pop("agentops.judges_complete")
    elif mutation == "trace-version":
        lifecycle.trace.info.tags["agentops.agent_revision"] = "working"
    elif mutation == "checksum":
        lifecycle.trace.data.spans[0].attributes["agentops.harness_checksum"] = "modified"
    else:
        lifecycle.trace.data.spans.clear()
    with pytest.raises((RegistryError, ManifestError)):
        lifecycle.governance.check_evaluation(lifecycle.release, "run-1", "trace-1")


def promotion_sql(lifecycle, *, race=False, rejected=False, audit_fail=False):
    state = {}

    def execute(statement, parameters=None):
        lifecycle.sql.append((statement, parameters))
        if "SELECT decision" in statement:
            return [[
                "rejected" if rejected else "approved",
                '{"run_id":"run-1","trace_id":"trace-1"}', "reviewer@example.com",
            ]]
        if "SELECT version" in statement:
            return [state["pointer"]] if state else []
        if "MERGE INTO" in statement:
            if not race:
                state["pointer"] = [parameters["version"], None, parameters["transition"], "operator", "now"]
            return []
        if "SELECT event_id" in statement:
            return []
        if "agent_events" in statement and audit_fail:
            raise RegistryError("audit not writable")
        return []

    lifecycle.registry._execute_sql = execute
    return state


def test_promote_changes_only_catalog_pointer_and_audits(lifecycle):
    promotion_sql(lifecycle)
    result = lifecycle.governance.select("test-agent", "1.0.0", expected_version="none")
    assert result["version"] == "1.0.0"
    assert result["deployment_changed"] is False
    merge = next(sql for sql, _ in lifecycle.sql if "MERGE INTO" in sql)
    assert "t.version = :expected" in merge
    assert lifecycle.sql[-1][1]["event_type"] == "promote"


@pytest.mark.parametrize("mode", ["stale", "race", "rejected", "rollback-without-history"])
def test_production_conflicts_and_missing_approval_fail_closed(lifecycle, mode):
    promotion_sql(lifecycle, race=mode == "race", rejected=mode == "rejected")
    with pytest.raises(RegistryError):
        lifecycle.governance.select(
            "test-agent", "1.0.0", expected_version="2.0.0" if mode == "stale" else "none",
            rollback=mode == "rollback-without-history",
        )
    assert not any(params and params.get("event_type") for _, params in lifecycle.sql)


def test_audit_failure_reports_that_pointer_already_changed(lifecycle):
    state = promotion_sql(lifecycle, audit_fail=True)
    with pytest.raises(RegistryError, match="pointer changed to 1.0.0"):
        lifecycle.governance.select("test-agent", "1.0.0", expected_version="none")
    assert state["pointer"][0] == "1.0.0"


def test_trace_store_rejects_legacy_or_conflicting_uc_destination():
    from mlflow.entities.trace_location import UnityCatalog

    experiment = SimpleNamespace(trace_location=None, lifecycle_stage="active")
    store = TraceStore("synthetic", binding(), client=SimpleNamespace(get_experiment=lambda _: experiment))
    with pytest.raises(ManifestError, match="UC trace destination"):
        store.verify_location()
    experiment.trace_location = UnityCatalog(catalog_name="other", schema_name="aops")
    with pytest.raises(ManifestError, match="UC trace destination"):
        store.verify_location()
    experiment.trace_location = UnityCatalog(catalog_name="main", schema_name="aops")
    assert store.verify_location() == experiment


def test_uc_trace_uses_exact_table_prefix_not_experiment_id():
    from mlflow.entities.trace_location import TraceLocation, UnityCatalog

    location = UnityCatalog(catalog_name="main", schema_name="aops", table_prefix="experiment_1")
    experiment = SimpleNamespace(trace_location=location, lifecycle_stage="active")
    trace = SimpleNamespace(
        info=SimpleNamespace(
            experiment_id=None,
            trace_location=TraceLocation.from_databricks_uc_table_prefix("main", "aops", "experiment_1"),
        ),
        data=SimpleNamespace(spans=[object()]),
    )
    store = TraceStore("synthetic", binding(), client=SimpleNamespace(
        get_experiment=lambda _: experiment, get_trace=lambda _: trace,
    ))
    assert store.get("trace-1") is trace
    trace.info.trace_location = TraceLocation.from_databricks_uc_table_prefix("main", "aops", "other_experiment")
    with pytest.raises(ManifestError, match="different UC trace table"):
        store.get("trace-1")


def test_shared_tracing_configure_reuses_existing_uc_prefix(lifecycle, monkeypatch):
    import mlflow
    from mlflow.entities.trace_location import UnityCatalog

    location = UnityCatalog(catalog_name="main", schema_name="aops", table_prefix="existing_prefix")
    experiment = SimpleNamespace(experiment_id="1", trace_location=location, lifecycle_stage="active")
    client = SimpleNamespace(
        get_experiment_by_name=lambda name: experiment, get_experiment=lambda experiment_id: experiment,
    )
    calls = []
    monkeypatch.setattr(mlflow, "MlflowClient", lambda **kwargs: client)
    monkeypatch.setattr(mlflow, "set_experiment", lambda **kwargs: calls.append(kwargs) or experiment)
    before = (lifecycle.root / "aops.yaml").read_bytes()
    result = configure_tracing(
        Settings("synthetic", HOST, experiment="/Shared/test", registry="main.aops", warehouse_id="warehouse"),
    )
    assert result["experiment_id"] == "1"
    assert calls[0]["trace_location"] is location
    assert (lifecycle.root / "aops.yaml").read_bytes() == before


def test_tracing_configure_conflict_stops_before_mutation(lifecycle, monkeypatch):
    import mlflow
    from mlflow.entities.trace_location import UnityCatalog

    experiment = SimpleNamespace(
        experiment_id="1", trace_location=UnityCatalog(catalog_name="other", schema_name="aops"),
        lifecycle_stage="active",
    )
    monkeypatch.setattr(mlflow, "MlflowClient", lambda **kwargs: SimpleNamespace(
        get_experiment_by_name=lambda name: experiment,
    ))
    monkeypatch.setattr(mlflow, "set_experiment", lambda **kwargs: pytest.fail("must not change a conflicting destination"))
    with pytest.raises(ManifestError, match="different trace location"):
        configure_tracing(
            Settings("synthetic", HOST, experiment="/Shared/test", registry="main.aops", warehouse_id="warehouse"),
        )


def test_evaluation_requires_consent_ground_truth_and_correct_trace_identity(lifecycle):
    records = [{"trace_id": "trace-1", "expectations": {"expected_response": "answer"}}]
    with pytest.raises(ManifestError, match="consent"):
        evaluate_traces(lifecycle.store, lifecycle.release, records, judge_model="databricks:/judge", consent=False)
    records[0]["expectations"] = {}
    with pytest.raises(ManifestError, match="ground-truth"):
        evaluate_traces(lifecycle.store, lifecycle.release, records, judge_model="databricks:/judge", consent=True)
    lifecycle.trace.info.tags["agentops.agent_revision"] = "working"
    with pytest.raises(ManifestError, match="registered agent version"):
        validate_trace_evidence(lifecycle.trace, lifecycle.release)


def test_actual_local_mlflow_trace_has_spans_redaction_and_pinned_revision(tmp_path, monkeypatch):
    import mlflow
    from mlflow import MlflowClient
    from mlflow.genai.scorers import scorer

    previous = mlflow.get_tracking_uri()
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    try:
        client = MlflowClient(tracking_uri=uri)
        experiment_id = client.create_experiment("local-aops-test", artifact_location=(tmp_path / "artifacts").as_uri())
        config = {**binding(), "experiment_id": experiment_id}
        root = create_project("test-agent", tmp_path / "agent", build_type="no-code")
        settings = Settings(
            "synthetic", HOST, registry="main.aops", warehouse_id="warehouse", experiment_id=experiment_id,
        )
        write_release_metadata(build_release(root, "1.0.0", "main.aops", settings=settings))
        store = TraceStore("synthetic", config, client=client)
        monkeypatch.setattr(store, "context", nullcontext)
        monkeypatch.setattr(store, "verify_location", lambda: None)
        result = store.run(
            root, lambda: {"response": "hello", "token": "do-not-log"},
            inputs={"prompt": "hello", "password": "do-not-log"}, capture_content=True,
        )
        trace = store.get(result["trace"]["trace_id"])
        assert len(trace.data.spans) == 1
        assert trace.info.tags["agentops.agent_revision"] == "1.0.0"
        assert trace.info.tags["agentops.trace_coverage"] == "invocation-boundary"
        assert "do-not-log" not in str(trace.data.spans[0].inputs)
        assert "do-not-log" not in str(trace.data.spans[0].outputs)
        assert trace.data.spans[0].outputs["token"] == "[REDACTED]"
        metadata_only = store.run(root, lambda: {"secret": "do-not-log"}, inputs={"secret": "do-not-log"})
        clean = store.get(metadata_only["trace"]["trace_id"])
        assert clean.data.spans[0].inputs is None
        assert clean.data.spans[0].outputs is None
        assert clean.info.tags["agentops.evaluation_inputs_available"] == "false"
        changed_store = TraceStore("synthetic", {**config, "warehouse_id": "other"}, client=client)
        monkeypatch.setattr(changed_store, "context", nullcontext)
        monkeypatch.setattr(changed_store, "verify_location", lambda: None)
        changed = changed_store.run(root, lambda: {"answer": "ok"}, inputs={"prompt": "hello"})
        assert changed_store.get(changed["trace"]["trace_id"]).info.tags["agentops.agent_revision"] == "working"
        assert len(client.search_traces(locations=[experiment_id])) == 3

        # Exercise the real evaluation API without calling a model or judging real agent quality.
        @scorer(name="correctness")
        def local_correctness(outputs):
            return True

        @scorer(name="relevance_to_query")
        def local_relevance(outputs):
            return True

        @scorer(name="safety")
        def local_safety(outputs):
            return True

        import mlflow.genai.scorers as scorers

        monkeypatch.setattr(scorers, "Correctness", lambda **kwargs: local_correctness)
        monkeypatch.setattr(scorers, "RelevanceToQuery", lambda **kwargs: local_relevance)
        monkeypatch.setattr(scorers, "Safety", lambda **kwargs: local_safety)
        release = build_release(root, "1.0.0", "main.aops", settings=settings)
        registered = SimpleNamespace(
            agent_id=release.agent_id, version=release.release_version,
            artifact_sha256="a" * 64, harness_sha256=release.harness_sha256,
            skills_sha256=release.skills_sha256, release_manifest=release_to_dict(release),
        )
        evaluation = evaluate_traces(
            store, registered,
            [{"trace_id": trace.info.trace_id, "expectations": {"expected_response": "hello"}}],
            judge_model="databricks:/synthetic-local-test-only", consent=True,
        )
        run = client.get_run(evaluation["run_id"])
        assert run.info.status == "FINISHED"
        assert run.data.metrics["correctness/mean"] == 1
        assert run.data.tags["agentops.judges_complete"] == "true"
        governance = ReleaseGovernance(SimpleNamespace(location=parse_registry("main.aops")))
        governance._store = lambda release: store
        checked = governance.check_evaluation(registered, evaluation["run_id"], trace.info.trace_id)
        assert checked["approved"] is True
    finally:
        mlflow.flush_trace_async_logging()
        mlflow.set_tracking_uri(previous)
