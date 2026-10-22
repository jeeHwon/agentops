from __future__ import annotations

import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import Settings
from .manifest import (
    AGENT_ID_RE, VERSION_RE, ManifestError, calculate_definition_checksums,
    read_release_metadata, validate_agent,
)
from .redaction import redact_value


class TraceStore:
    def __init__(self, profile: str, binding: dict[str, str], *, client: Any = None):
        if not profile.strip():
            raise ValueError("An explicitly selected Databricks profile is required.")
        if not all(binding.get(key) for key in (
            "experiment_id", "workspace_host", "catalog", "schema", "warehouse_id",
        )):
            raise ManifestError("Set up shared UC MLflow tracing with aops configure first.")
        self.profile = profile
        self.binding = binding
        if client is None:
            from mlflow import MlflowClient

            client = MlflowClient(tracking_uri=f"databricks://{profile}")
        self.client = client

    @contextmanager
    def context(self):
        import mlflow

        previous_uri = mlflow.get_tracking_uri()
        variables = {
            "DATABRICKS_CONFIG_PROFILE": self.profile,
            "MLFLOW_TRACING_SQL_WAREHOUSE_ID": self.binding["warehouse_id"],
            "MLFLOW_EXPERIMENT_ID": self.binding["experiment_id"],
        }
        previous = {key: os.environ.get(key) for key in variables}
        os.environ.update(variables)
        mlflow.set_tracking_uri(f"databricks://{self.profile}")
        try:
            yield
        finally:
            mlflow.set_tracking_uri(previous_uri)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def verify_location(self) -> Any:
        from mlflow.entities.trace_location import UnityCatalog

        with self.context():
            experiment = self.client.get_experiment(self.binding["experiment_id"])
        location = experiment.trace_location
        if (
            not isinstance(location, UnityCatalog)
            or location.catalog_name != self.binding["catalog"]
            or location.schema_name != self.binding["schema"]
        ):
            raise ManifestError("MLflow experiment does not match the configured UC trace destination.")
        if experiment.lifecycle_stage != "active":
            raise ManifestError("MLflow experiment is not active.")
        return experiment

    def get(self, trace_id: str) -> Any:
        experiment = self.verify_location()
        with self.context():
            trace = self.client.get_trace(trace_id)
        self._check_trace_location(trace, experiment)
        if not trace.data.spans:
            raise ManifestError("Trace has no readable spans.")
        return trace

    def _check_trace_location(self, trace: Any, experiment: Any) -> None:
        # UC traces carry a table-prefix location, not an experiment_id.
        expected = getattr(experiment, "trace_location", None)
        if expected is not None:
            actual = getattr(getattr(trace.info, "trace_location", None), "uc_table_prefix", None)
            if actual != expected:
                raise ManifestError("Trace belongs to a different UC trace table location.")
        elif str(trace.info.experiment_id) != self.binding["experiment_id"]:
            raise ManifestError("Trace belongs to a different MLflow experiment.")

    def search(self, agent_id: str, version: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not AGENT_ID_RE.fullmatch(agent_id) or not VERSION_RE.fullmatch(version):
            raise ManifestError("Trace search requires an agent ID and major.minor.patch version.")
        if not 1 <= limit <= 1000:
            raise ManifestError("Trace limit must be between 1 and 1000.")
        experiment = self.verify_location()
        with self.context():
            traces = self.client.search_traces(
                locations=[self.binding["experiment_id"]],
                filter_string=(
                    f"tags.`agentops.agent_id` = '{agent_id}' AND "
                    f"tags.`agentops.agent_revision` = '{version}'"
                ),
                max_results=limit, include_spans=True,
            )
        for trace in traces:
            self._check_trace_location(trace, experiment)
        return [trace_summary(trace) for trace in traces]

    def run(self, root: Path, invoke: Any, *, inputs: Any, capture_content: bool = False) -> dict[str, Any]:
        import mlflow

        manifest = validate_agent(root)
        if capture_content and (
            manifest.data_sensitivity not in {"공개", "내부"} or manifest.security_level != "일반"
        ):
            raise ManifestError("Content capture is prohibited for this SPEC security classification.")
        checksums = calculate_definition_checksums(root)
        release = read_release_metadata(root) or {}
        version = manifest.agent_version if (
            release.get("version") == manifest.agent_version
            and release.get("harness_sha256") == checksums.harness
            and release.get("skills_sha256") == checksums.combined_skills
            and release.get("project", {}).get("tracing") == self.binding
        ) else "working"
        self.verify_location()
        tags = {
            "agentops.agent_id": manifest.agent_id,
            "agentops.agent_version": manifest.agent_version,
            "agentops.agent_revision": version,
            "agentops.build_type": manifest.build_type,
            "agentops.agent_type": manifest.agent_type,
            "agentops.harness_checksum": checksums.harness,
            "agentops.skills_checksum": checksums.combined_skills,
            "agentops.trace_coverage": "invocation-boundary",
            "agentops.evaluation_inputs_available": str(capture_content).lower(),
        }
        with self.context():
            # Activating the bound experiment selects MLflow's UC span processor.
            mlflow.set_experiment(experiment_id=self.binding["experiment_id"])
            span = self.client.start_trace(
                name="agent.invoke", span_type="AGENT", tags=tags, attributes=tags,
                inputs=redact_value(inputs) if capture_content else None,
                experiment_id=self.binding["experiment_id"],
            )
            try:
                response = invoke()
            except Exception:
                self.client.end_trace(span.trace_id, status="ERROR")
                mlflow.flush_trace_async_logging()
                raise
            self.client.end_trace(
                span.trace_id, status="OK",
                outputs=redact_value(response) if capture_content else None,
            )
            mlflow.flush_trace_async_logging()
            verified = self.get(span.trace_id)
        return {"result": response, "trace": trace_summary(verified)}


def configure_tracing(settings: Settings) -> dict[str, str]:
    import mlflow
    from mlflow.entities.trace_location import UnityCatalog

    binding = settings.tracing
    catalog, schema = binding["catalog"], binding["schema"]
    if not settings.experiment.startswith("/") or not all((catalog, schema, settings.warehouse_id)):
        raise ManifestError("An absolute experiment path, catalog, schema and warehouse ID are required.")
    from mlflow import MlflowClient

    client = MlflowClient(tracking_uri=f"databricks://{settings.profile}")
    store = TraceStore(settings.profile, {**binding, "experiment_id": "0"}, client=client)
    with store.context():
        experiment = client.get_experiment_by_name(settings.experiment)
        if experiment is not None and experiment.lifecycle_stage != "active":
            raise ManifestError("MLflow experiment is not active.")
        location = getattr(experiment, "trace_location", None)
        if location is not None and (
            not isinstance(location, UnityCatalog)
            or location.catalog_name != catalog or location.schema_name != schema
        ):
            raise ManifestError("Existing experiment is bound to a different trace location.")
        experiment = mlflow.set_experiment(
            experiment_name=settings.experiment,
            trace_location=location or UnityCatalog(catalog_name=catalog, schema_name=schema),
        )
    binding["experiment_id"] = experiment.experiment_id
    TraceStore(settings.profile, binding, client=client).verify_location()
    return binding


def trace_summary(trace: Any) -> dict[str, Any]:
    return {
        "trace_id": trace.info.trace_id,
        "state": str(getattr(trace.info.state, "value", trace.info.state)),
        "span_count": len(trace.data.spans),
        "coverage": trace.info.tags.get("agentops.trace_coverage", "native-or-hook"),
    }


def validate_trace_evidence(trace: Any, release: Any) -> None:
    tags = trace.info.tags
    expected = {
        "agentops.agent_id": release.agent_id,
        "agentops.agent_revision": release.version,
    }
    if any(tags.get(key) != value for key, value in expected.items()):
        raise ManifestError("Trace does not identify this registered agent version.")
    root = next((span for span in trace.data.spans if not span.parent_id), None)
    if root is None:
        raise ManifestError("Trace has no readable root span.")
    for key, expected_hash in (
        ("agentops.harness_checksum", release.harness_sha256),
        ("agentops.skills_checksum", release.skills_sha256),
    ):
        if root.attributes.get(key) != expected_hash:
            raise ManifestError("Trace source checksum does not match the registered release.")
    if str(getattr(trace.info.state, "value", trace.info.state)) != "OK":
        raise ManifestError("Trace did not complete successfully.")


def evaluate_traces(store: TraceStore, release: Any, records: Any, *, judge_model: str, consent: bool) -> dict[str, Any]:
    import mlflow
    from mlflow.genai.scorers import Correctness, RelevanceToQuery, Safety

    if not consent:
        raise ManifestError("Evaluation sends trace content to judges. Pass --capture-content to consent.")
    spec = release.release_manifest["spec"]
    if spec["data_sensitivity"] not in {"공개", "내부"} or spec["security_level"] != "일반":
        raise ManifestError("Content evaluation is prohibited for this SPEC security classification.")
    if not judge_model.startswith("databricks:/"):
        raise ManifestError("Select an explicit Databricks judge model URI.")
    if not isinstance(records, list) or not records or len(records) > 1000:
        raise ManifestError("Evaluation data must contain 1 to 1000 trace references.")
    data, latencies, trace_ids = [], [], []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"trace_id", "expectations"}:
            raise ManifestError("Each evaluation row requires trace_id and expectations.")
        if not isinstance(record["trace_id"], str) or not record["trace_id"].strip():
            raise ManifestError("Each evaluation trace_id must be a non-empty string.")
        if record["trace_id"] in trace_ids:
            raise ManifestError("Evaluation contains a duplicate trace.")
        trace = store.get(record["trace_id"])
        validate_trace_evidence(trace, release)
        root = next(span for span in trace.data.spans if not span.parent_id)
        if trace.info.tags.get("agentops.evaluation_inputs_available") != "true" or not root.inputs or not root.outputs:
            raise ManifestError("Trace lacks consented inputs/outputs. Capture a new trace with content first.")
        expectations = record["expectations"]
        expected_response = expectations.get("expected_response") if isinstance(expectations, dict) else None
        expected_facts = expectations.get("expected_facts") if isinstance(expectations, dict) else None
        if not (
            isinstance(expected_response, str) and expected_response.strip()
            or isinstance(expected_facts, list) and expected_facts
            and all(isinstance(fact, str) and fact.strip() for fact in expected_facts)
        ):
            raise ManifestError("Correctness evaluation requires ground-truth expectations.")
        data.append({
            "inputs": redact_value(root.inputs if isinstance(root.inputs, dict) else {"request": root.inputs}),
            "outputs": redact_value(root.outputs),
            "expectations": redact_value(expectations),
        })
        latency = (root.end_time_ns - root.start_time_ns) / 1e9
        if not math.isfinite(latency) or latency < 0:
            raise ManifestError("Trace has invalid latency.")
        latencies.append(latency)
        trace_ids.append(trace.info.trace_id)
    with store.context():
        mlflow.set_experiment(experiment_id=store.binding["experiment_id"])
        with mlflow.start_run(tags={
            "agentops.agent_id": release.agent_id, "agentops.agent_version": release.version,
            "agentops.artifact_sha256": release.artifact_sha256,
            "agentops.evaluation_source": "verified-mlflow-traces",
        }) as run:
            result = mlflow.genai.evaluate(
                data=data, scorers=[
                    Correctness(model=judge_model), RelevanceToQuery(model=judge_model),
                    Safety(model=judge_model),
                ],
            )
            frame = result.result_df
            if frame is None or len(frame) != len(data):
                raise RuntimeError("Evaluation did not produce a result for every source trace.")
            for row in frame.to_dict(orient="records"):
                for scorer in ("correctness", "relevance_to_query", "safety"):
                    value = row.get(f"{scorer}/value")
                    error = row.get(f"{scorer}/error_message")
                    has_error = error is not None and not (
                        isinstance(error, float) and math.isnan(error)
                    )
                    if has_error or str(value).lower() not in {"yes", "no", "true", "false", "0", "1", "0.0", "1.0"}:
                        raise RuntimeError(f"Evaluation has a missing or failed {scorer} judgment.")
            latencies.sort()
            # Linear interpolation matches the usual percentile definition.
            index = (len(latencies) - 1) * 0.9
            low, high = math.floor(index), math.ceil(index)
            p90 = latencies[low] + (latencies[high] - latencies[low]) * (index - low)
            mlflow.log_metric("agentops.latency_p90_seconds", p90)
            mlflow.log_dict({"trace_ids": trace_ids}, "agentops/evidence.json")
            mlflow.set_tag("agentops.judges_complete", "true")
            run_id = run.info.run_id
            if result.run_id != run_id:
                raise RuntimeError("MLflow evaluation used an unexpected run; no approval evidence was emitted.")
        mlflow.flush_trace_async_logging()
    return {"run_id": run_id, "metrics": {**result.metrics, "agentops.latency_p90_seconds": p90}}
