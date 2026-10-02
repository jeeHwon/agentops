from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from .config import ConfigError, load_settings
from .codex_config import is_otel_configured
from .collector import collector_health
from .databricks import Profile, validate_profile
from .mlflow_exporter import MlflowTurnExporter
from .outbox import Outbox
from .paths import heartbeat_path, otel_heartbeat_path, outbox_path


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    detail: str


def run_doctor(*, write_test_trace: bool = False) -> list[Check]:
    checks: list[Check] = []
    try:
        settings = load_settings()
        checks.append(Check("OK", "configuration", f"profile={settings.profile}, experiment={settings.experiment}"))
    except ConfigError as exc:
        return [Check("FAIL", "configuration", str(exc))]

    checks.append(_plugin_check())
    checks.append(_hook_check())
    if settings.otel_mode == "local":
        checks.append(
            Check(
                "OK" if is_otel_configured(settings) else "FAIL",
                "codex-otel-config",
                f"metrics/logs -> 127.0.0.1:{settings.otel_port}",
            )
        )
        checks.append(
            Check(
                "OK" if collector_health(settings.otel_port) else "FAIL",
                "otel-collector",
                f"127.0.0.1:{settings.otel_port}",
            )
        )
        checks.append(_otel_signal_check())
    else:
        checks.append(
            Check(
                "WARN",
                "codex-otel-config",
                "existing exporter preserved; exact Tool duration requires routing codex.tool_result to Codex AgentOps",
            )
        )
    try:
        identity = validate_profile(Profile(settings.profile, settings.host))
        checks.append(Check("OK", "databricks-auth", identity))
    except Exception as exc:
        checks.append(Check("FAIL", "databricks-auth", str(exc)))
        return checks

    try:
        exporter = MlflowTurnExporter(settings)
        client, _ = exporter._client_and_experiment()
        experiment = client.get_experiment_by_name(settings.experiment)
        if experiment is None:
            raise RuntimeError(f"Experiment not found: {settings.experiment}")
        detail = f"experiment_id={experiment.experiment_id}, trace_location={experiment.trace_location}"
        if write_test_trace:
            detail += f", test_trace_id={exporter.write_test_trace()}"
        checks.append(Check("OK", "mlflow", detail))
    except Exception as exc:
        checks.append(Check("FAIL", "mlflow", str(exc)))

    stats = Outbox(outbox_path()).stats()
    level = "WARN" if stats["failed"] else "OK"
    checks.append(Check(level, "outbox", ", ".join(f"{key}={value}" for key, value in stats.items())))
    return checks


def _plugin_check() -> Check:
    try:
        process = subprocess.run(
            ["codex", "plugin", "list", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(process.stdout)
        match = next(
            (
                item
                for item in payload.get("installed", [])
                if item.get("name") == "codex-agentops" and item.get("installed") is True
            ),
            None,
        )
        if not match:
            return Check("FAIL", "codex-plugin", "codex-agentops is not installed")
        if not match.get("enabled", False):
            return Check("FAIL", "codex-plugin", "codex-agentops is installed but disabled")
        return Check("OK", "codex-plugin", str(match.get("pluginId", "codex-agentops")))
    except Exception as exc:
        return Check("FAIL", "codex-plugin", str(exc))


def _hook_check() -> Check:
    target = heartbeat_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        observed_ns = int(raw["observed_at_ns"])
        age_seconds = max(0, int((time.time_ns() - observed_ns) / 1_000_000_000))
        return Check(
            "OK",
            "codex-hook",
            f"last_event={raw.get('event_name')}, agent={raw.get('agent_id')}, age={age_seconds}s",
        )
    except FileNotFoundError:
        return Check("WARN", "codex-hook", "no hook event observed; run Codex in an Agent folder and trust it with /hooks")
    except Exception as exc:
        return Check("WARN", "codex-hook", f"cannot read heartbeat: {exc}")


def _otel_signal_check() -> Check:
    target = otel_heartbeat_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        observed_ns = int(raw["observed_at_ns"])
        age_seconds = max(0, int((time.time_ns() - observed_ns) / 1_000_000_000))
        return Check(
            "OK",
            "codex-otel-signal",
            f"last={raw.get('signal')}, token_events={raw.get('token_events_inserted')}, age={age_seconds}s",
        )
    except FileNotFoundError:
        return Check("WARN", "codex-otel-signal", "no OTel payload observed; start a new Codex session")
    except Exception as exc:
        return Check("WARN", "codex-otel-signal", f"cannot read OTel heartbeat: {exc}")
