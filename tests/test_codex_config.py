from __future__ import annotations

import pytest

from codex_agentops.codex_config import (
    configure_otel,
    install_otel_config,
    is_otel_configured,
    remove_otel_config,
)
from codex_agentops.config import ConfigError, Settings


def test_managed_otel_block_preserves_existing_config(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('model = "test-model"\n', encoding="utf-8")
    settings = Settings("test-profile", "https://example.com", otel_port=4319)

    install_otel_config(settings, target)
    content = target.read_text(encoding="utf-8")
    assert 'model = "test-model"' in content
    assert 'endpoint = "http://127.0.0.1:4319/v1/logs"' in content
    assert 'endpoint = "http://127.0.0.1:4319/v1/metrics"' in content
    assert is_otel_configured(settings, target)

    assert remove_otel_config(target) is True
    assert target.read_text(encoding="utf-8") == 'model = "test-model"\n'


def test_external_otel_config_is_not_overwritten(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[otel]\nexporter = "none"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        install_otel_config(Settings("test-profile", "https://example.com"), target)

    mode, returned = configure_otel(Settings("test-profile", "https://example.com"), target)
    assert mode == "external"
    assert returned == target
    assert target.read_text(encoding="utf-8") == '[otel]\nexporter = "none"\n'
