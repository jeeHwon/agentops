from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .codex_config import configure_otel, remove_otel_config, validate_codex_config
from .collector import ensure_collector, serve_collector
from .config import DEFAULT_EXPERIMENT, DEFAULT_OTEL_PORT, Settings, load_settings, save_settings
from .databricks import (
    DatabricksError,
    choose_profile,
    get_default_warehouse,
    get_profile,
    list_profiles,
    validate_profile,
)
from .doctor import run_doctor
from .hooks import handle_hook, reconcile_session_transcript
from .manifest import ManifestError, create_agent, validate_agent
from .mlflow_exporter import MlflowTurnExporter, export_pending
from .monitoring import configure_turn_monitoring
from .outbox import Outbox
from .paths import outbox_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-agentops", description="Codex AgentOps CLI")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    configure = commands.add_parser("configure", help="select a Databricks profile and verify MLflow")
    configure.add_argument("--profile", help="Databricks CLI profile selected by the user")
    configure.add_argument(
        "--experiment",
        default=DEFAULT_EXPERIMENT,
        help=f"existing MLflow experiment path (default: {DEFAULT_EXPERIMENT})",
    )
    configure.add_argument(
        "--warehouse-id",
        help="SQL warehouse used for background scorers (default: first available warehouse)",
    )
    configure.add_argument("--no-content", action="store_true", help="store metadata without prompt/output bodies")
    configure.add_argument("--otel-port", type=int, default=DEFAULT_OTEL_PORT, help="local Codex OTel port")

    init = commands.add_parser("init", help="create a standard Agent folder")
    init.add_argument("agent_id")
    init.add_argument("--path", type=Path, help="destination path (default: ./<agent-id>)")

    validate = commands.add_parser("validate", help="validate a standard Agent folder")
    validate.add_argument("path", nargs="?", type=Path, default=Path.cwd())

    doctor = commands.add_parser("doctor", help="diagnose installation, hooks, auth, MLflow, and outbox")
    doctor.add_argument("--write-test-trace", action="store_true")

    commands.add_parser("status", help="show local outbox status")
    flush = commands.add_parser("flush", help="retry and upload pending traces")
    flush.add_argument("--limit", type=int, default=100)

    commands.add_parser("_hook", help=argparse.SUPPRESS)
    upload = commands.add_parser("_upload", help=argparse.SUPPRESS)
    upload.add_argument("--limit", type=int, default=100)
    upload.add_argument("--wait-for-usage", type=float, default=0)
    commands.add_parser("_collector", help=argparse.SUPPRESS)
    commands.add_parser("_remove-otel-config", help=argparse.SUPPRESS)
    reconcile = commands.add_parser("_reconcile", help=argparse.SUPPRESS)
    reconcile.add_argument("--session-id", required=True)
    reconcile.add_argument("--transcript-path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "configure":
            return _configure(args)
        if args.command == "init":
            target = args.path or Path.cwd() / args.agent_id
            root = create_agent(args.agent_id, target)
            print(f"Created Agent: {root}")
            print(f"Edit guide: {root / 'README.md'}")
            print(f"Skill template: {root / '.agents/skills/example-skill/SKILL.md'}")
            print(f"Next: cd {root} && codex")
            return 0
        if args.command == "validate":
            manifest = validate_agent(args.path)
            print(f"OK: {manifest.agent_id} ({manifest.root})")
            return 0
        if args.command == "doctor":
            checks = run_doctor(write_test_trace=args.write_test_trace)
            for check in checks:
                print(f"[{check.level}] {check.name}: {check.detail}")
            return 1 if any(check.level == "FAIL" for check in checks) else 0
        if args.command == "status":
            stats = Outbox(outbox_path()).stats()
            for key, value in stats.items():
                print(f"{key}: {value}")
            return 0
        if args.command == "flush":
            outbox = Outbox(outbox_path())
            outbox.requeue_failed()
            result = export_pending(outbox, load_settings(), limit=args.limit, require_usage=True)
            print(f"uploaded={result.uploaded} failed={result.failed}")
            for trace_id in result.trace_ids:
                print(f"trace_id={trace_id}")
            return 1 if result.failed else 0
        if args.command == "_hook":
            return _hook_command()
        if args.command == "_upload":
            result = _upload_with_usage_wait(args.limit, args.wait_for_usage)
            print(f"uploaded={result.uploaded} failed={result.failed}")
            return 1 if result.failed else 0
        if args.command == "_collector":
            serve_collector()
            return 0
        if args.command == "_remove-otel-config":
            print("removed" if remove_otel_config() else "not-configured")
            return 0
        if args.command == "_reconcile":
            outbox = Outbox(outbox_path())
            inserted = reconcile_session_transcript(args.transcript_path, args.session_id, outbox)
            result = _upload_with_usage_wait(100, 2)
            print(f"token_records={inserted} uploaded={result.uploaded} failed={result.failed}")
            return 1 if result.failed else 0
    except (DatabricksError, ManifestError, RuntimeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


def _configure(args: argparse.Namespace) -> int:
    profiles = list_profiles()
    profile = get_profile(args.profile, profiles) if args.profile else choose_profile(profiles)
    identity = validate_profile(profile)
    warehouse_id = args.warehouse_id or get_default_warehouse(profile.name)
    settings = Settings(
        profile=profile.name,
        host=profile.host,
        user_name=identity,
        warehouse_id=warehouse_id,
        experiment=args.experiment,
        capture_content=not args.no_content,
        otel_port=args.otel_port,
        configured_at=datetime.now(timezone.utc).isoformat(),
    )
    otel_mode, codex_path = configure_otel(settings)
    settings = replace(settings, otel_mode=otel_mode)
    path = save_settings(settings)
    codex_version = validate_codex_config()
    if settings.otel_mode == "local":
        ensure_collector(settings)
    trace_id = MlflowTurnExporter(settings).write_test_trace()
    scorers = configure_turn_monitoring(settings)
    print(f"Configured: {path}")
    print(f"Databricks: {identity} @ {profile.host}")
    print(f"MLflow experiment: {settings.experiment}")
    print(f"SQL warehouse: {settings.warehouse_id}")
    if settings.otel_mode == "local":
        print(f"Codex OTel: {codex_path} -> http://127.0.0.1:{settings.otel_port}")
    else:
        print(f"Codex OTel: existing configuration preserved at {codex_path}")
    print(f"Codex: {codex_version}")
    print(f"Test trace: {trace_id}")
    print("Background turn scorers:")
    for scorer in scorers:
        print(
            f"  {scorer.name}: sample_rate={scorer.sample_rate:g}, "
            f"filter={scorer.filter_string}"
        )
    return 0


def _upload_with_usage_wait(limit: int, wait_seconds: float):
    outbox = Outbox(outbox_path())
    settings = load_settings()
    deadline = time.monotonic() + max(0, wait_seconds)
    uploaded = failed = 0
    trace_ids: list[str] = []
    first = True
    while first or time.monotonic() < deadline:
        first = False
        result = export_pending(outbox, settings, limit=limit, require_usage=True)
        uploaded += result.uploaded
        failed += result.failed
        trace_ids.extend(result.trace_ids)
        if result.uploaded or result.failed:
            break
        if wait_seconds <= 0:
            break
        time.sleep(0.25)
    from .mlflow_exporter import ExportResult

    return ExportResult(
        uploaded,
        failed,
        tuple(trace_ids),
    )


def _hook_command() -> int:
    try:
        raw = json.load(sys.stdin)
        if not isinstance(raw, dict):
            raise ValueError("hook input must be a JSON object")
        output = handle_hook(raw)
    except Exception as exc:
        output = {"systemMessage": f"Codex AgentOps telemetry warning: {type(exc).__name__}"}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
