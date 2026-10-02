from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import config_path


DEFAULT_EXPERIMENT = "/Shared/codex-agentops"
DEFAULT_OTEL_PORT = 4319


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    profile: str
    host: str
    user_name: str = ""
    warehouse_id: str = ""
    experiment: str = DEFAULT_EXPERIMENT
    capture_content: bool = True
    otel_port: int = DEFAULT_OTEL_PORT
    otel_mode: str = "auto"
    configured_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        profile = str(raw.get("profile", "")).strip()
        host = str(raw.get("host", "")).strip().rstrip("/")
        experiment = str(raw.get("experiment", DEFAULT_EXPERIMENT)).strip()
        if not profile:
            raise ConfigError("Databricks Profile이 없습니다. `aops configure`를 실행하세요.")
        if not host.startswith("https://"):
            raise ConfigError("Databricks host must be an https URL.")
        if not experiment.startswith("/"):
            raise ConfigError("MLflow experiment must be an absolute workspace path.")
        otel_port = int(raw.get("otel_port", DEFAULT_OTEL_PORT))
        if not 1024 <= otel_port <= 65535:
            raise ConfigError("OTel collector port must be between 1024 and 65535.")
        otel_mode = str(raw.get("otel_mode", "auto"))
        if otel_mode not in {"auto", "local", "external"}:
            raise ConfigError("OTel mode must be auto, local, or external.")
        return cls(
            profile=profile,
            host=host,
            user_name=str(raw.get("user_name", "")),
            warehouse_id=str(raw.get("warehouse_id", "")),
            experiment=experiment,
            capture_content=bool(raw.get("capture_content", True)),
            otel_port=otel_port,
            otel_mode=otel_mode,
            configured_at=str(raw.get("configured_at", "")),
        )


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError("Codex AgentOps가 설정되지 않았습니다. `aops configure`를 실행하세요.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration at {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Configuration at {target} must be a JSON object.")
    return Settings.from_dict(raw)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    if not payload["configured_at"]:
        payload["configured_at"] = datetime.now(timezone.utc).isoformat()
    fd, temp_name = tempfile.mkstemp(prefix="config-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target
