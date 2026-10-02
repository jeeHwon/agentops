from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .config import Settings


TURN_TRACE_FILTER = "tags.`mlflow.traceName` = 'agent.turn'"
TURN_SCORER_NAMES = ("relevance_to_query", "safety", "completeness")


@dataclass(frozen=True)
class MonitoringScorer:
    name: str
    sample_rate: float
    filter_string: str


def configure_turn_monitoring(settings: Settings) -> tuple[MonitoringScorer, ...]:
    """Register and start MLflow-managed asynchronous scorers for every Agent turn."""
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
    if settings.warehouse_id:
        set_databricks_monitoring_sql_warehouse_id(
            sql_warehouse_id=settings.warehouse_id,
            experiment_id=experiment.experiment_id,
        )

    existing = {
        scorer.name: scorer for scorer in list_scorers(experiment_id=experiment.experiment_id)
    }
    sampling = ScorerSamplingConfig(sample_rate=1.0, filter_string=TURN_TRACE_FILTER)
    active: list[MonitoringScorer] = []
    for scorer_type in (RelevanceToQuery, Safety, Completeness):
        scorer = existing.get(scorer_type().name)
        if scorer is None:
            scorer = scorer_type(model="databricks").register(
                experiment_id=experiment.experiment_id
            )
        if not _is_active(scorer):
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
