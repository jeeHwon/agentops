from __future__ import annotations

import json

import pytest

from codex_agentops.config import ConfigError, Settings, load_settings, save_settings


def test_settings_round_trip_without_token(tmp_path):
    target = tmp_path / "config.json"
    settings = Settings(
        profile="test-profile",
        host="https://example.cloud.databricks.com",
        warehouse_id="warehouse-1",
        registry="poc_catalog.agentops_test",
        capture_content=True,
        content_capture_consent=True,
        experiment_id="123",
    )
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.profile == settings.profile
    assert loaded.host == settings.host
    assert loaded.warehouse_id == "warehouse-1"
    assert loaded.registry == "poc_catalog.agentops_test"
    assert loaded.capture_content is True
    assert loaded.configured_at
    assert loaded.codex_enabled is False
    assert loaded.tracing == {
        "experiment_id": "123", "workspace_host": settings.host,
        "catalog": "poc_catalog", "schema": "agentops_test", "warehouse_id": "warehouse-1",
    }
    raw = target.read_text()
    assert "token" not in raw.lower()
    assert target.stat().st_mode & 0o777 == 0o600


def test_legacy_capture_setting_is_not_explicit_consent(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"profile": "test", "host": "https://example.com", "capture_content": True}))
    assert load_settings(target).capture_content is False


def test_invalid_config_is_rejected(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"profile": "test-profile", "host": "http://unsafe"}))
    with pytest.raises(ConfigError):
        load_settings(target)


def test_legacy_codex_integration_remains_enabled_without_new_flag():
    settings = Settings.from_dict({"profile": "chosen", "host": "https://example.com"})
    assert settings.codex_enabled is True


@pytest.mark.parametrize("extra", [
    {"experiment_id": "not-an-id"}, {"registry": "not-a-schema"},
    {"registry": "main.aops;drop"}, {"warehouse_id": "with spaces"},
])
def test_invalid_shared_settings_are_rejected(extra):
    with pytest.raises(ConfigError):
        Settings.from_dict({"profile": "chosen", "host": "https://example.com", **extra})
