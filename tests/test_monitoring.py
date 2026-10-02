from __future__ import annotations

import mlflow
import mlflow.genai.scorers as scorers
import mlflow.tracing as tracing

from codex_agentops.config import Settings
from codex_agentops.monitoring import TURN_TRACE_FILTER, configure_turn_monitoring


class _Experiment:
    experiment_id = "experiment-1"


class _Client:
    def __init__(self, tracking_uri):
        self.tracking_uri = tracking_uri

    def get_experiment_by_name(self, name):
        assert name == "/Shared/codex-agentops"
        return _Experiment()


class _Scorer:
    def __init__(self, name, model=None):
        self.name = name
        self.model = model
        self.sample_rate = None
        self.filter_string = None
        self.registered_experiment = None

    def register(self, experiment_id):
        self.registered_experiment = experiment_id
        return self

    def start(self, experiment_id, sampling_config):
        assert self.registered_experiment == experiment_id
        self.sample_rate = sampling_config.sample_rate
        self.filter_string = sampling_config.filter_string
        return self


def test_configure_turn_monitoring_registers_three_background_scorers(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "test-profile")
    monkeypatch.setattr(mlflow, "MlflowClient", _Client)
    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: None)
    monkeypatch.setattr(mlflow, "set_experiment", lambda **kwargs: None)
    monkeypatch.setattr(scorers, "list_scorers", lambda **kwargs: [])
    monkeypatch.setattr(
        scorers, "RelevanceToQuery", lambda model=None: _Scorer("relevance_to_query", model)
    )
    monkeypatch.setattr(scorers, "Safety", lambda model=None: _Scorer("safety", model))
    monkeypatch.setattr(
        scorers, "Completeness", lambda model=None: _Scorer("completeness", model)
    )
    warehouse_calls = []
    monkeypatch.setattr(
        tracing,
        "set_databricks_monitoring_sql_warehouse_id",
        lambda **kwargs: warehouse_calls.append(kwargs),
    )

    active = configure_turn_monitoring(
        Settings(
            profile="test-profile",
            host="https://example.cloud.databricks.com",
            warehouse_id="warehouse-1",
        )
    )

    assert [item.name for item in active] == [
        "relevance_to_query",
        "safety",
        "completeness",
    ]
    assert all(item.sample_rate == 1.0 for item in active)
    assert all(item.filter_string == TURN_TRACE_FILTER for item in active)
    assert warehouse_calls == [
        {"sql_warehouse_id": "warehouse-1", "experiment_id": "experiment-1"}
    ]
