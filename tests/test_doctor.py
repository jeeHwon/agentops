from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from codex_agentops import doctor
from codex_agentops.config import Settings
from codex_agentops.databricks import Profile


def test_plugin_timeout_is_reported_as_warning(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

    monkeypatch.setattr(doctor.subprocess, "run", timeout)

    check = doctor._plugin_check()

    assert check.level == "WARN"
    assert check.name == "codex-plugin"


def test_common_doctor_does_not_require_codex_or_create_an_outbox(monkeypatch):
    settings = Settings("chosen", "https://example.com")
    monkeypatch.setattr(doctor, "load_settings", lambda: settings)
    monkeypatch.setattr(doctor, "list_profiles", lambda: [Profile(settings.profile, settings.host)])
    monkeypatch.setattr(doctor, "validate_profile", lambda profile: "user@example.com")
    for name in ("_plugin_check", "_hook_check", "collector_health", "Outbox"):
        monkeypatch.setattr(doctor, name, lambda *args: pytest.fail("Codex is opt-in"))
    experiment = SimpleNamespace(experiment_id="123", trace_location="UC")
    monkeypatch.setattr(doctor, "MlflowTurnExporter", lambda settings: SimpleNamespace(
        _client_and_experiment=lambda: (
            SimpleNamespace(get_experiment_by_name=lambda name: experiment), "123",
        ),
    ))
    checks = doctor.run_doctor()
    assert {check.name for check in checks} == {"configuration", "databricks-auth", "mlflow"}
    assert all(check.level == "OK" for check in checks)


def test_doctor_rejects_profile_workspace_change_before_mlflow_access(monkeypatch):
    monkeypatch.setattr(doctor, "load_settings", lambda: Settings("chosen", "https://original.test"))
    monkeypatch.setattr(doctor, "list_profiles", lambda: [Profile("chosen", "https://other.test")])
    monkeypatch.setattr(doctor, "MlflowTurnExporter", lambda settings: pytest.fail("wrong workspace"))
    checks = doctor.run_doctor(write_test_trace=True)
    assert checks[-1].name == "databricks-auth"
    assert checks[-1].level == "FAIL"
