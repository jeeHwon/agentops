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
from .config import (
    DEFAULT_EXPERIMENT,
    DEFAULT_OTEL_PORT,
    ConfigError,
    Settings,
    load_settings,
    save_settings,
)
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
from .manifest import ManifestError, create_agent, find_agent_root, validate_agent
from .mlflow_exporter import MlflowTurnExporter, export_pending
from .monitoring import configure_turn_monitoring
from .outbox import Outbox
from .paths import outbox_path
from .registry import RegistryClient, RegistryError, parse_registry
from .release import CodexRuntimeBuilder, ReleaseError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aops", description="Codex AgentOps CLI")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar=(
            "{configure,init,validate,registry-init,list,register,load,doctor,status,flush}"
        ),
    )

    configure = commands.add_parser(
        "configure",
        help="기본 Databricks Profile, Registry와 MLflow를 설정합니다",
    )
    configure.add_argument("--profile", help="사용자가 선택한 Databricks CLI Profile")
    configure.add_argument(
        "--experiment",
        default=DEFAULT_EXPERIMENT,
        help=f"기존 MLflow Experiment 경로 (기본값: {DEFAULT_EXPERIMENT})",
    )
    configure.add_argument(
        "--warehouse-id",
        help="백그라운드 Scorer용 SQL Warehouse ID (기본값: 접근 가능한 첫 Warehouse)",
    )
    configure.add_argument(
        "--registry",
        help="기본 Agent Registry 위치: <catalog>.<schema>",
    )
    configure.add_argument("--no-content", action="store_true", help="Prompt와 응답 본문 없이 메타데이터만 저장합니다")
    configure.add_argument("--otel-port", type=int, default=DEFAULT_OTEL_PORT, help="로컬 Codex OTel Port")

    init = commands.add_parser("init", help="표준 Agent 폴더를 생성합니다")
    init.add_argument("agent_id")
    init.add_argument("--path", type=Path, help="생성 경로 (기본값: ./<agent-id>)")

    validate = commands.add_parser("validate", help="표준 Agent 폴더 구조를 검증합니다")
    validate.add_argument("path", nargs="?", type=Path, default=Path.cwd())

    registry_init = commands.add_parser(
        "registry-init", help="UC Volume과 Delta Table로 Agent Registry를 준비합니다"
    )
    _add_registry_arguments(registry_init)

    registry_list = commands.add_parser("list", help="접근 가능한 Registry Agent를 조회합니다")
    _add_registry_arguments(registry_list)
    registry_list.add_argument("--all-versions", action="store_true", help="모든 버전을 표시합니다")
    registry_list.add_argument(
        "--include-unavailable",
        action="store_true",
        help="관리자 점검용으로 UC Skill 권한이 없는 Agent도 표시합니다",
    )

    register = commands.add_parser("register", help="Agent 폴더를 불변 버전으로 등록합니다")
    register.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    register.add_argument("--version", required=True, help="새 Agent 버전")
    _add_registry_arguments(register)

    load = commands.add_parser("load", help="Registry Agent를 로컬 폴더로 내려받습니다")
    load.add_argument("agent_id", help="내려받을 Agent ID")
    load.add_argument("--version", help="버전, 생략하면 가장 최근 등록 버전")
    load.add_argument("--path", type=Path, help="대상 폴더, 기본값은 ./<agent-id>")
    _add_registry_arguments(load)

    doctor = commands.add_parser("doctor", help="설치, Hook, 인증, MLflow와 Outbox를 진단합니다")
    doctor.add_argument("--write-test-trace", action="store_true")

    commands.add_parser("status", help="로컬 Outbox 상태를 표시합니다")
    flush = commands.add_parser("flush", help="전송 대기 Trace를 다시 업로드합니다")
    flush.add_argument("--limit", type=int, default=100)

    commands.add_parser("_hook")
    upload = commands.add_parser("_upload")
    upload.add_argument("--limit", type=int, default=100)
    upload.add_argument("--wait-for-usage", type=float, default=0)
    commands.add_parser("_collector")
    commands.add_parser("_remove-otel-config")
    reconcile = commands.add_parser("_reconcile")
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
            _codex_runtime_builder().prepare(root)
            print(f"Agent 생성 완료: {root}")
            print(f"편집 안내: {root / 'README.md'}")
            print(f"Skill 템플릿: {root / 'skills/request-structurer/SKILL.md'}")
            print(f"다음 단계: cd {root} && codex")
            return 0
        if args.command == "validate":
            manifest = validate_agent(_agent_root(args.path))
            _codex_runtime_builder().prepare(manifest.root)
            print(f"OK: {manifest.agent_id} ({manifest.root})")
            return 0
        if args.command == "registry-init":
            registry = _registry_client(args)
            registry.initialize()
            print(f"Registry 준비 완료: {registry.location.schema_name}")
            print(f"Agent Table: {registry.location.agents_table_name}")
            print(f"Version Table: {registry.location.versions_table_name}")
            print(f"Legacy 호환 Table: {registry.location.table_name}")
            print(f"Artifact Volume: {registry.location.volume_root}")
            return 0
        if args.command == "list":
            registry = _registry_client(args)
            versions = registry.list(
                all_versions=args.all_versions,
                authorized_only=not args.include_unavailable,
            )
            if not versions:
                print("등록된 Agent가 없습니다.")
            for item in versions:
                print(
                    f"{item.agent_id}@{item.version}\t{item.name}\t"
                    f"skills={','.join(item.skill_names)}\tregistered_by={item.registered_by}"
                )
            return 0
        if args.command == "register":
            registry = _registry_client(args)
            item = registry.register(_agent_root(args.path), args.version)
            print(f"Agent 등록 완료: {item.agent_id}@{item.version}")
            print(f"Artifact: {item.artifact_path}")
            print(f"SHA-256: {item.artifact_sha256}")
            return 0
        if args.command == "load":
            registry = _registry_client(args)
            destination = args.path or Path.cwd() / args.agent_id
            target, item = registry.load(
                args.agent_id,
                version=args.version,
                destination=destination,
            )
            print(f"Agent 로드 완료: {item.agent_id}@{item.version}")
            print(f"Local Path: {target}")
            print(f"UC Skill 권한 및 Release 체크섬 검증 완료: {len(item.skill_names)}개")
            print(f"검증: cd {target} && aops validate")
            print(f"실행: cd {target} && codex")
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
    except (
        DatabricksError,
        ManifestError,
        RegistryError,
        ReleaseError,
        RuntimeError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


def _agent_root(path: Path) -> Path:
    root = find_agent_root(path)
    if root is None:
        raise ManifestError(f"spec.md와 AGENTS.md를 찾을 수 없습니다: {path.expanduser().resolve()}")
    return root


def _add_registry_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="configure에 저장된 Profile 대신 사용할 값")
    parser.add_argument("--registry", help="configure에 저장된 Registry 대신 사용할 <catalog>.<schema>")
    parser.add_argument("--warehouse-id", help="configure에 저장된 SQL Warehouse 대신 사용할 ID")
    parser.add_argument("--volume", default="agent_artifacts", help="Artifact Volume 이름")
    parser.add_argument("--table", default="agent_releases", help="Metadata Delta Table 이름")


def _registry_client(args: argparse.Namespace) -> RegistryClient:
    settings = None
    if not args.profile or not args.registry:
        settings = load_settings()
    elif not args.warehouse_id:
        try:
            settings = load_settings()
        except ConfigError:
            pass

    profile_name = args.profile or settings.profile
    registry_name = args.registry or settings.registry
    if not registry_name:
        raise RegistryError(
            "Agent Registry가 설정되지 않았습니다. "
            "`aops configure --registry <catalog.schema>`를 실행하세요."
        )

    profile = get_profile(profile_name, list_profiles())
    validate_profile(profile)
    configured_warehouse = (
        settings.warehouse_id
        if settings and settings.profile == profile.name
        else ""
    )
    warehouse_id = args.warehouse_id or configured_warehouse or get_default_warehouse(profile.name)
    location = parse_registry(registry_name, volume=args.volume, table=args.table)
    return RegistryClient(
        profile=profile.name,
        warehouse_id=warehouse_id,
        location=location,
        codex_otel_port=(settings.otel_port if settings else DEFAULT_OTEL_PORT),
    )


def _codex_runtime_builder() -> CodexRuntimeBuilder:
    try:
        port = load_settings().otel_port
    except ConfigError:
        port = DEFAULT_OTEL_PORT
    return CodexRuntimeBuilder(otel_port=port)


def _validate_profile(profile_name: str) -> None:
    profile = get_profile(profile_name, list_profiles())
    validate_profile(profile)


def _resolve_profile(profile_name: str | None) -> str:
    resolved = profile_name or load_settings().profile
    _validate_profile(resolved)
    return resolved


def _resolve_warehouse(profile_name: str, warehouse_id: str | None) -> str:
    if warehouse_id:
        return warehouse_id
    try:
        settings = load_settings()
    except ConfigError:
        settings = None
    if settings and settings.profile == profile_name and settings.warehouse_id:
        return settings.warehouse_id
    return get_default_warehouse(profile_name)


def _configure(args: argparse.Namespace) -> int:
    profiles = list_profiles()
    profile = get_profile(args.profile, profiles) if args.profile else choose_profile(profiles)
    identity = validate_profile(profile)
    warehouse_id = args.warehouse_id or get_default_warehouse(profile.name)
    registry_name = (args.registry or "").strip()
    if registry_name:
        parse_registry(registry_name)
    else:
        try:
            previous = load_settings()
        except ConfigError:
            previous = None
        if previous and previous.profile == profile.name:
            registry_name = previous.registry
    settings = Settings(
        profile=profile.name,
        host=profile.host,
        user_name=identity,
        warehouse_id=warehouse_id,
        registry=registry_name,
        experiment=args.experiment,
        capture_content=not args.no_content,
        otel_port=args.otel_port,
        configured_at=datetime.now(timezone.utc).isoformat(),
    )
    otel_mode, codex_path = configure_otel(settings)
    settings = replace(settings, otel_mode=otel_mode)
    path = save_settings(settings)
    codex_version = validate_codex_config()
    ensure_collector(settings)
    trace_id = MlflowTurnExporter(settings).write_test_trace()
    scorers = configure_turn_monitoring(settings)
    print(f"설정 완료: {path}")
    print(f"Databricks: {identity} @ {profile.host}")
    print(f"MLflow Experiment: {settings.experiment}")
    print(f"SQL Warehouse: {settings.warehouse_id}")
    print(f"Agent Registry: {settings.registry or '미설정'}")
    if settings.otel_mode == "local":
        print(f"Codex OTel: {codex_path} -> http://127.0.0.1:{settings.otel_port}")
    else:
        print(f"Codex OTel: 기존 전역 설정을 보존했습니다: {codex_path}")
        print(f"Agent 프로젝트 OTel: http://127.0.0.1:{settings.otel_port}")
    print(f"Codex: {codex_version}")
    print(f"Test Trace: {trace_id}")
    print("백그라운드 Turn Scorer:")
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
