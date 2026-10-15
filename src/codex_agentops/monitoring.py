from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .config import Settings


TURN_TRACE_FILTER = (
    "tags.`mlflow.traceName` = 'agent.turn' "
    "AND tags.`agentops.evaluation_inputs_available` = 'true'"
)
TURN_SCORER_NAMES = ("relevance_to_query", "safety", "completeness")


@dataclass(frozen=True)
class MonitoringScorer:
    name: str
    sample_rate: float
    filter_string: str


def configure_turn_monitoring(settings: Settings) -> tuple[MonitoringScorer, ...]:
    """Register scorers for turns with captured inputs, without changing existing policies."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.genai.scorers import (
        Completeness,
        RelevanceToQuery,
        Safety,
        ScorerSamplingConfig,
        list_scorers,
    )
    from mlflow.tracing import set_databricks_monitoring_sql_warehouse_id

    os.environ["DATABRICKS_CONFIG_PROFILE"] = settings.profile
    mlflow.set_tracking_uri(f"databricks://{settings.profile}")
    if settings.warehouse_id:
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.warehouse_id
    client = MlflowClient(tracking_uri=f"databricks://{settings.profile}")
    experiment = client.get_experiment_by_name(settings.experiment)
    if experiment is None:
        raise RuntimeError(
            f"Platform-managed MLflow experiment does not exist: {settings.experiment}"
        )
    mlflow.set_experiment(experiment_id=experiment.experiment_id)
    existing = {
        scorer.name: scorer for scorer in list_scorers(experiment_id=experiment.experiment_id)
    }
    scorer_types = (RelevanceToQuery, Safety, Completeness)
    incompatible = [
        scorer_type().name for scorer_type in scorer_types
        if (scorer := existing.get(scorer_type().name)) is not None
        and not _is_active(scorer)
    ]
    if incompatible:
        raise RuntimeError(
            "Existing MLflow scorers have a different sampling policy: "
            + ", ".join(incompatible)
            + ". Review coverage for untagged traces before changing shared scorers."
        )
    if settings.warehouse_id:
        set_databricks_monitoring_sql_warehouse_id(
            sql_warehouse_id=settings.warehouse_id,
            experiment_id=experiment.experiment_id,
        )

    sampling = ScorerSamplingConfig(sample_rate=1.0, filter_string=TURN_TRACE_FILTER)
    active: list[MonitoringScorer] = []
    for scorer_type in scorer_types:
        scorer = existing.get(scorer_type().name)
        if scorer is None:
            scorer = scorer_type(model="databricks").register(
                experiment_id=experiment.experiment_id
            )
            scorer = scorer.start(
                experiment_id=experiment.experiment_id,
                sampling_config=sampling,
            )
        active.append(
            MonitoringScorer(
                name=scorer.name,
                sample_rate=float(scorer.sample_rate or 0),
                filter_string=str(scorer.filter_string or ""),
            )
        )
    return tuple(active)


def _is_active(scorer: Any) -> bool:
    return (
        float(getattr(scorer, "sample_rate", 0) or 0) == 1.0
        and str(getattr(scorer, "filter_string", "") or "") == TURN_TRACE_FILTER
    )
