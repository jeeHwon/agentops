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
    )
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.profile == settings.profile
    assert loaded.host == settings.host
    assert loaded.warehouse_id == "warehouse-1"
    assert loaded.registry == "poc_catalog.agentops_test"
    assert loaded.capture_content is True
    assert loaded.configured_at
    raw = target.read_text()
    assert "token" not in raw.lower()
    assert target.stat().st_mode & 0o777 == 0o600


def test_invalid_config_is_rejected(tmp_path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"profile": "test-profile", "host": "http://unsafe"}))
    with pytest.raises(ConfigError):
        load_settings(target)
