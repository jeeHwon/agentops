from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "codex-agentops"


def config_dir() -> Path:
    override = os.getenv("CODEX_AGENTOPS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    override = os.getenv("CODEX_AGENTOPS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def outbox_path() -> Path:
    return data_dir() / "outbox.db"


def uploader_log_path() -> Path:
    return data_dir() / "uploader.log"


def heartbeat_path() -> Path:
    return data_dir() / "last_hook.json"


def otel_heartbeat_path() -> Path:
    return data_dir() / "last_otel.json"


def collector_log_path() -> Path:
    return data_dir() / "collector.log"


def collector_pid_path() -> Path:
    return data_dir() / "collector.pid"


def codex_config_path() -> Path:
    return Path(os.getenv("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
