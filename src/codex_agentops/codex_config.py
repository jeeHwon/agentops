from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .config import ConfigError, Settings
from .paths import codex_config_path


BEGIN_MARKER = "# BEGIN CODEX AGENTOPS MANAGED OTEL"
END_MARKER = "# END CODEX AGENTOPS MANAGED OTEL"


def otel_block(settings: Settings) -> str:
    base = f"http://127.0.0.1:{settings.otel_port}"
    return (
        f"{BEGIN_MARKER}\n"
        "[otel]\n"
        'environment = "codex-agentops"\n'
        "log_user_prompt = false\n"
        "log_agent_responses = false\n"
        f'exporter = {{ otlp-http = {{ endpoint = "{base}/v1/logs", protocol = "json" }} }}\n'
        f'metrics_exporter = {{ otlp-http = {{ endpoint = "{base}/v1/metrics", protocol = "json" }} }}\n'
        f"{END_MARKER}\n"
    )


def install_otel_config(settings: Settings, path: Path | None = None) -> Path:
    target = path or codex_config_path()
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    stripped = _remove_managed_block(existing)
    if _has_external_otel_config(stripped):
        raise ConfigError(
            f"Existing Codex [otel] configuration at {target} cannot be replaced automatically. "
            "Route that collector to Codex AgentOps or remove the existing OTel configuration first."
        )
    content = stripped.rstrip()
    if content:
        content += "\n\n"
    content += otel_block(settings)
    _atomic_write(target, content)
    return target


def configure_otel(settings: Settings, path: Path | None = None) -> tuple[str, Path]:
    target = path or codex_config_path()
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if BEGIN_MARKER in existing:
        return "local", install_otel_config(settings, target)
    if _has_external_otel_config(existing):
        return "external", target
    return "local", install_otel_config(settings, target)


def remove_otel_config(path: Path | None = None) -> bool:
    target = path or codex_config_path()
    if not target.exists():
        return False
    existing = target.read_text(encoding="utf-8")
    updated = _remove_managed_block(existing)
    if updated == existing:
        return False
    _atomic_write(target, updated.rstrip() + ("\n" if updated.strip() else ""))
    return True


def is_otel_configured(settings: Settings, path: Path | None = None) -> bool:
    target = path or codex_config_path()
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    return BEGIN_MARKER in content and otel_block(settings).strip() in content


def validate_codex_config() -> str:
    process = subprocess.run(
        ["codex", "--strict-config", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return process.stdout.strip()


def _remove_managed_block(content: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(BEGIN_MARKER)}\n.*?^\s*{re.escape(END_MARKER)}\n?"
    )
    return pattern.sub("", content)


def _has_external_otel_config(content: str) -> bool:
    for line in content.splitlines():
        clean = line.split("#", 1)[0].strip()
        if clean == "[otel]" or re.match(r"^otel(?:\.|\s*=)", clean):
            return True
    return False


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    fd, temp_name = tempfile.mkstemp(prefix="config-", suffix=".toml", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
