from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass

from .config import ConfigError
from .paths import registry_context_path
from .registry import parse_registry


@dataclass(frozen=True)
class RegistryContext:
    profile: str
    host: str
    registry: str
    warehouse_id: str

    def __post_init__(self) -> None:
        if not self.profile or not self.host.startswith("https://"):
            raise ConfigError("Registry Profile 또는 Workspace URL이 올바르지 않습니다. `aops use`를 다시 실행하세요.")
        parse_registry(self.registry)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.warehouse_id):
            raise ConfigError("SQL Warehouse ID가 올바르지 않습니다. `aops use`를 다시 실행하세요.")


def load_registry_context() -> RegistryContext | None:
    try:
        data = json.loads(registry_context_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("저장된 Registry 선택을 읽을 수 없습니다. `aops use`를 다시 실행하세요.") from exc
    if not isinstance(data, dict):
        raise ConfigError("저장된 Registry 선택이 올바르지 않습니다. `aops use`를 다시 실행하세요.")
    try:
        return RegistryContext(**data)
    except (TypeError, ValueError) as exc:
        raise ConfigError("저장된 Registry 선택이 올바르지 않습니다. `aops use`를 다시 실행하세요.") from exc


def save_registry_context(context: RegistryContext) -> None:
    target = registry_context_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="registry-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(asdict(context), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
