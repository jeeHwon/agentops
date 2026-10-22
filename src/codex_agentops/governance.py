from __future__ import annotations

import json
import math
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .manifest import AGENT_ID_RE, VERSION_RE
from .registry import RegistryClient, RegistryError, _quoted_name
from .tracing import TraceStore, validate_trace_evidence


class ReleaseGovernance:
    """Client-side release policy backed by UC tables and real MLflow evidence."""

    def __init__(self, registry: RegistryClient):
        self.registry = registry
        self.location = registry.location

    def _actor(self) -> str:
        rows = self.registry._execute_sql("SELECT current_user()")
        if not rows or not rows[0][0]:
            raise RegistryError("Cannot determine the authenticated Registry actor.")
        return str(rows[0][0])

    def production(self, agent_id: str) -> dict[str, Any] | None:
        if not AGENT_ID_RE.fullmatch(agent_id):
            raise RegistryError("Invalid agent ID.")
        rows = self.registry._execute_sql(
            f"""SELECT version, previous_version, transition_id, updated_by, CAST(updated_at AS STRING)
                FROM {_quoted_name(self.location.production_table_name)}
                WHERE agent_id = :agent_id""",
            {"agent_id": agent_id},
        )
        if len(rows) > 1:
            raise RegistryError("Duplicate production pointers detected; an administrator must repair the table.")
        if not rows:
            return None
        return dict(zip(("version", "previous_version", "transition_id", "updated_by", "updated_at"), rows[0]))

    def _store(self, release: Any) -> TraceStore:
        project = release.release_manifest.get("project", {})
        if not project or not project.get("tracing", {}).get("experiment_id"):
            raise RegistryError("Release has no UC tracing binding; configure tracing and register a new version.")
        from .runtime import NativeRuntime

        runtime = NativeRuntime(self.registry.profile, workspace=self.registry.workspace)
        runtime.check_host(project)
        return TraceStore(self.registry.profile, project.get("tracing", {}))

    def check_evaluation(self, release: Any, run_id: str, trace_id: str) -> dict[str, Any]:
        store = self._store(release)
        store.verify_location()
        with store.context():
            run = store.client.get_run(run_id)
            if (
                run.info.status != "FINISHED"
                or getattr(run.info, "lifecycle_stage", "active") != "active"
                or str(run.info.experiment_id) != store.binding["experiment_id"]
            ):
                raise RegistryError("Evaluation must be FINISHED in the release's MLflow experiment.")
            expected_tags = {
                "agentops.agent_id": release.agent_id,
                "agentops.agent_version": release.version,
                "agentops.artifact_sha256": release.artifact_sha256,
                "agentops.evaluation_source": "verified-mlflow-traces",
                "agentops.judges_complete": "true",
            }
            if any(run.data.tags.get(key) != value for key, value in expected_tags.items()):
                raise RegistryError("MLflow evaluation does not identify this immutable release.")
            with tempfile.TemporaryDirectory(prefix="aops-evidence-") as temporary:
                path = store.client.download_artifacts(run_id, "agentops/evidence.json", temporary)
                evidence = json.loads(Path(path).read_text(encoding="utf-8"))
        trace_ids = evidence.get("trace_ids") if isinstance(evidence, dict) else None
        if (
            not isinstance(trace_ids, list) or not trace_ids or len(trace_ids) > 1000
            or not all(isinstance(item, str) for item in trace_ids)
            or len(set(trace_ids)) != len(trace_ids) or trace_id not in trace_ids
        ):
            raise RegistryError("Evaluation evidence lacks valid source trace references.")
        for source_trace_id in trace_ids:
            validate_trace_evidence(store.get(source_trace_id), release)
        criteria = release.release_manifest["spec"]["success_criteria"]
        comparisons = (
            ("correctness/mean", "correctness_min", True),
            ("relevance_to_query/mean", "relevance_min", True),
            ("safety/mean", "safety_min", True),
            ("agentops.latency_p90_seconds", "latency_p90_seconds", False),
        )
        checks = {}
        for metric, criterion, minimum in comparisons:
            value = run.data.metrics.get(metric)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or (minimum and value > 1)
            ):
                raise RegistryError(f"Missing or invalid evaluation metric: {metric}")
            threshold = criteria[criterion]
            passed = value >= threshold if minimum else value <= threshold
            checks[metric] = {"value": value, "threshold": threshold, "passed": passed}
        return {
            "scope": "review", "run_id": run_id, "trace_id": trace_id,
            "artifact_sha256": release.artifact_sha256,
            "checks": checks, "approved": all(item["passed"] for item in checks.values()),
        }

    def review(self, agent_id: str, version: str, *, run_id: str, trace_id: str) -> dict[str, Any]:
        release = self.registry.get(agent_id, version)
        actor = self._actor()
        owner = release.release_manifest["spec"]["owner"]
        if actor.casefold() in {owner.casefold(), release.registered_by.casefold()}:
            raise RegistryError("An independent reviewer, not the owner or publisher, must review this release.")
        checks = self.check_evaluation(release, run_id, trace_id)
        decision = "approved" if checks["approved"] else "rejected"
        gate_id = uuid.uuid4().hex
        self.registry._execute_sql(
            f"""INSERT INTO {_quoted_name(self.location.gates_table_name)}
                (gate_id, agent_id, version, checks_json, decision, evidence_ref,
                 decided_by, decided_at, upgrade_type)
                VALUES (:gate_id, :agent_id, :version, :checks, :decision, :run_id,
                        current_user(), current_timestamp(), NULL)""",
            {
                "gate_id": gate_id, "agent_id": agent_id, "version": version,
                "checks": json.dumps(checks, sort_keys=True), "decision": decision, "run_id": run_id,
            },
        )
        return {"gate_id": gate_id, "decision": decision, **checks}

    def select(
        self, agent_id: str, version: str, *, expected_version: str, rollback: bool = False,
    ) -> dict[str, Any]:
        if expected_version != "none" and not VERSION_RE.fullmatch(expected_version):
            raise RegistryError("expected-version must be major.minor.patch, or none for first activation.")
        release = self.registry.get(agent_id, version)
        rows = self.registry._execute_sql(
            f"""SELECT decision, checks_json, decided_by
                FROM {_quoted_name(self.location.gates_table_name)}
                WHERE agent_id = :agent_id AND version = :version
                  AND get_json_object(checks_json, '$.scope') = 'review'
                ORDER BY decided_at DESC, gate_id DESC LIMIT 1""",
            {"agent_id": agent_id, "version": version},
        )
        if not rows or rows[0][0] != "approved":
            raise RegistryError("The latest independent review has not approved this release.")
        owner = release.release_manifest["spec"]["owner"]
        if str(rows[0][2]).casefold() in {owner.casefold(), release.registered_by.casefold()}:
            raise RegistryError("Stored approval is not independent.")
        evidence = json.loads(rows[0][1])
        checks = self.check_evaluation(release, evidence["run_id"], evidence["trace_id"])
        if not checks["approved"]:
            raise RegistryError("Release evidence no longer passes the SPEC criteria.")
        current = self.production(agent_id)
        if (current["version"] if current else "none") != expected_version:
            raise RegistryError("Production pointer changed. Inspect aops production and retry explicitly.")
        if expected_version == version:
            raise RegistryError("Requested version is already the production selection.")
        if rollback:
            history = self.registry._execute_sql(
                f"""SELECT event_id FROM {_quoted_name(self.location.events_table_name)}
                    WHERE agent_id = :agent_id AND version = :version
                      AND event_type IN ('promote', 'rollback') LIMIT 1""",
                {"agent_id": agent_id, "version": version},
            )
            if not history:
                raise RegistryError("Rollback target was never selected for production.")
        transition = uuid.uuid4().hex
        params = {
            "agent_id": agent_id, "version": version, "expected": expected_version,
            "transition": transition,
        }
        self.registry._execute_sql(
            f"""MERGE INTO {_quoted_name(self.location.production_table_name)} AS t
                USING (SELECT :agent_id AS agent_id) AS s ON t.agent_id = s.agent_id
                WHEN MATCHED AND t.version = :expected THEN UPDATE SET
                  previous_version = t.version, version = :version, transition_id = :transition,
                  updated_by = current_user(), updated_at = current_timestamp()
                WHEN NOT MATCHED AND :expected = 'none' THEN INSERT
                  (agent_id, version, previous_version, transition_id, updated_by, updated_at)
                VALUES (:agent_id, :version, NULL, :transition, current_user(), current_timestamp())""",
            params,
        )
        updated = self.production(agent_id)
        if updated is None or updated["transition_id"] != transition:
            raise RegistryError("Production compare-and-set did not succeed; re-read the pointer.")
        try:
            self.registry._execute_sql(
                f"""INSERT INTO {_quoted_name(self.location.events_table_name)}
                    (event_id, agent_id, version, event_type, environment, source, actor, event_ts, detail_json)
                    VALUES (:transition, :agent_id, :version, :event_type, 'production', 'aops',
                            current_user(), current_timestamp(), :detail)""",
                {
                    **params, "event_type": "rollback" if rollback else "promote",
                    "detail": json.dumps({"previous_version": expected_version, "deployment_changed": False}),
                },
            )
        except Exception as exc:
            raise RegistryError(
                f"Production pointer changed to {version}, but audit event {transition} failed. "
                "Inspect and repair the audit log before another transition."
            ) from exc
        return {**updated, "deployment_changed": False}
