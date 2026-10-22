from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .codex_config import configure_otel, remove_otel_config, validate_codex_config
from .catalog import catalog_record
from .collector import ensure_collector, restart_collector, serve_collector
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
    Profile,
    choose_profile,
    get_default_warehouse,
    get_profile,
    list_profiles,
    validate_profile,
)
from .doctor import run_doctor
from .hooks import handle_hook, reconcile_session_transcript, reconcile_session_websearch
from .manifest import ManifestError, create_agent, find_agent_root, validate_agent
from .mlflow_exporter import MlflowTurnExporter, export_pending
from .monitoring import configure_turn_monitoring
from .outbox import Outbox
from .paths import config_path, outbox_path
from .registry import RegistryClient, RegistryError, parse_registry
from .release import CodexRuntimeBuilder, ReleaseError
from .project import create_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aops", description="Unified Skill, Code and Genie AgentOps CLI")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    configure = commands.add_parser(
        "configure",
        help="기본 Databricks Profile, Registry와 MLflow를 설정합니다",
    )
    configure.add_argument("--profile", help="사용자가 선택한 Databricks CLI Profile")
    configure.add_argument(
        "--experiment",
        help=f"공통 MLflow Experiment 경로 (최초 기본값: {DEFAULT_EXPERIMENT})",
    )
    configure.add_argument(
        "--warehouse-id",
        help="공통 SQL Warehouse ID (최초 생략 시 선택한 Profile의 기본 Warehouse)",
    )
    configure.add_argument(
        "--registry",
        help="Agent Registry 및 공통 UC Trace 위치: <catalog>.<schema>",
    )
    content = configure.add_mutually_exclusive_group()
    content.add_argument("--no-content", action="store_false", dest="capture_content", help="입출력 본문을 수집하지 않습니다")
    content.add_argument("--capture-content", action="store_true", help="마스킹한 입출력 본문 수집에 동의합니다")
    configure.set_defaults(capture_content=None)
    configure.add_argument("--codex", action=argparse.BooleanOptionalAction, default=None,
                           help="Skill용 Codex Hook·OTel·Turn Scorer 연동 (최초 기본값: 비활성)")
    configure.add_argument("--otel-port", type=int, help="Codex 연동 시 로컬 OTel Port")

    init = commands.add_parser("init", help="표준 Agent 폴더를 생성합니다")
    init.add_argument("agent_id")
    init.add_argument("--path", type=Path, help="생성 경로 (기본값: ./<agent-id>)")
    init.add_argument("--type", choices=("skill", "code", "no-code"), default="skill", dest="build_type")
    init.add_argument("--framework", choices=("langgraph", "openai"), default="langgraph")
    init.add_argument("--profile", help="configure에서 선택한 Profile 대신 사용할 값")

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

    catalog_export = commands.add_parser(
        "catalog-export", help="접근 가능한 통합 Agent 카탈로그를 JSON으로 내보냅니다"
    )
    _add_registry_arguments(catalog_export)
    catalog_export.add_argument("--all-versions", action="store_true")

    register = commands.add_parser("register", help="Agent 폴더를 불변 버전으로 등록합니다")
    register.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    register.add_argument("--version", required=True, help="새 Agent 버전")
    _add_registry_arguments(register)

    load = commands.add_parser("load", help="Registry Agent를 로컬 폴더로 내려받습니다")
    load.add_argument("agent_id", help="내려받을 Agent ID")
    load.add_argument("--version", help="버전, 생략하면 가장 최근 등록 버전")
    load.add_argument("--path", type=Path, help="대상 폴더, 기본값은 ./<agent-id>")
    _add_registry_arguments(load)

    link = commands.add_parser("link", help="기존 App 또는 Genie 리소스를 검증해 연결합니다")
    link.add_argument("resource_id")
    link.add_argument("--path", type=Path, default=Path.cwd())
    link.add_argument("--profile", help="configure에서 선택한 Profile 대신 사용할 값")
    link.add_argument("--invocation-path", help="Code App의 실제 호출 경로")

    deploy = commands.add_parser("deploy", help="공식 Agent Bricks CLI 또는 Genie API로 배포합니다")
    deploy.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    deploy.add_argument("--profile", help="configure에서 선택한 Profile 대신 사용할 값")
    deploy.add_argument("--apply", action="store_true")
    deploy.add_argument("--name", help="Agent Bricks 배포 이름")
    deploy.add_argument("--warehouse-id", help="Genie SQL Warehouse")
    deploy.add_argument("--parent-path", help="Genie Workspace 상위 경로")

    run = commands.add_parser("run", help="유형별 공식 런타임으로 실행합니다")
    run.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    run.add_argument("--profile")
    run.add_argument("--input", type=Path, help="Code App의 전체 네이티브 요청 JSON")
    run.add_argument("--prompt", help="Genie 질문")
    run.add_argument("--conversation-id")
    run.add_argument("--routing-key")
    run_content = run.add_mutually_exclusive_group()
    run_content.add_argument("--capture-content", action="store_true", help="마스킹한 입출력 기록에 동의합니다")
    run_content.add_argument("--no-content", action="store_false", dest="capture_content", help="이번 실행의 본문은 수집하지 않습니다")
    run.set_defaults(capture_content=None)

    for command in ("traces", "trace", "evaluate", "review", "promote", "rollback", "production"):
        operation = commands.add_parser(command, help=f"통합 Agent {command}")
        operation.add_argument("agent_id")
        _add_registry_arguments(operation)
        if command != "production":
            operation.add_argument("--version", required=True)
        if command == "traces":
            operation.add_argument("--limit", type=int, default=20)
        if command == "trace":
            operation.add_argument("trace_id")
        if command == "evaluate":
            operation.add_argument("--data", type=Path, required=True, help="trace_id와 expectations 배열 JSON")
            operation.add_argument("--judge-model", required=True)
            operation.add_argument("--capture-content", action="store_true")
        if command == "review":
            operation.add_argument("--run-id", required=True)
            operation.add_argument("--trace-id", required=True)
        if command in {"promote", "rollback"}:
            operation.add_argument("--expected-version", required=True, help="현재 운영 버전, 최초 지정은 none")

    doctor = commands.add_parser("doctor", help="설치, Hook, 인증, MLflow와 Outbox를 진단합니다")
    doctor.add_argument("--write-test-trace", action="store_true")

    commands.add_parser("status", help="로컬 Outbox 상태를 표시합니다")
    commands.add_parser("collector-restart", help="진행 중 수집·업로드가 없을 때 OTel Collector를 안전하게 재시작합니다")
    audit_calls = commands.add_parser("audit-calls", help="세션별 관측 호출 ID를 본문 없이 표시합니다")
    audit_calls.add_argument("--session-id", required=True)
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
            if args.build_type != "skill":
                root = create_project(
                    args.agent_id, target, build_type=args.build_type,
                    framework=args.framework,
                    profile=_environment(args.profile)[0].name if args.build_type == "code" else None,
                )
                print(f"Agent 생성 완료: {root} ({args.build_type})")
                return 0
            root = create_agent(args.agent_id, target)
            _codex_runtime_builder().prepare(root)
            print(f"Agent 생성 완료: {root}")
            print(f"편집 안내: {root / 'README.md'}")
            print(f"Skill 템플릿: {root / 'skills/request-structurer/SKILL.md'}")
            print(f"다음 단계: cd {root} && codex (Codex 모델 공급자와 인증을 먼저 확인하세요)")
            return 0
        if args.command == "validate":
            manifest = validate_agent(_agent_root(args.path))
            if manifest.build_type == "skill":
                _codex_runtime_builder().prepare(manifest.root)
            print(f"OK: {manifest.agent_id} ({manifest.root})")
            return 0
        if args.command == "registry-init":
            registry = _registry_client(args)
            registry.initialize()
            print(f"Registry 준비 완료: {registry.location.schema_name}")
            print(f"Agent Table: {registry.location.agents_table_name}")
            print(f"Version Table: {registry.location.versions_table_name}")
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
                    f"type={getattr(item, 'agent_type', 'skill-based-agent')}\t"
                    f"skills={','.join(item.skill_names)}\tregistered_by={item.registered_by}"
                )
            return 0
        if args.command == "catalog-export":
            registry = _registry_client(args)
            records = [
                catalog_record(release, registry.location)
                for release in registry.list(
                    all_versions=args.all_versions,
                    authorized_only=True,
                )
            ]
            print(json.dumps(records, ensure_ascii=False, sort_keys=True))
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
            print("리소스 권한 및 Release 체크섬 검증 완료")
            if getattr(item, "agent_type", "skill-based-agent") == "skill-based-agent":
                print(f"실행: cd {target} && codex (Codex 모델 공급자와 인증을 먼저 확인하세요)")
            else:
                print(f"실행: aops run {target}")
            return 0
        if args.command in {"link", "deploy", "run"}:
            return _runtime_command(args)
        if args.command in {"traces", "trace", "evaluate", "review", "promote", "rollback", "production"}:
            return _release_command(args)
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
        if args.command == "collector-restart":
            restart_collector(load_settings())
            print("AgentOps OTel collector restarted.")
            return 0
        if args.command == "audit-calls":
            for call in Outbox(outbox_path()).call_inventory(args.session_id):
                print(json.dumps(call, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "flush":
            outbox = Outbox(outbox_path())
            outbox.requeue_failed()
            settings = load_settings()
            turn_result = export_pending(outbox, settings, limit=args.limit, require_usage=False)
            from .session_exporter import export_pending_sessions

            session_result = export_pending_sessions(outbox, settings, limit=args.limit)
            from .mlflow_exporter import ExportResult

            result = ExportResult(
                turn_result.uploaded + session_result.uploaded,
                turn_result.failed + session_result.failed,
                turn_result.trace_ids + session_result.trace_ids,
            )
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
            calls = reconcile_session_websearch(args.transcript_path, args.session_id, outbox)
            result = _upload_with_usage_wait(100, 2)
            print(f"token_records={inserted} websearch_calls={calls} uploaded={result.uploaded} failed={result.failed}")
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
    except Exception as exc:
        print(f"ERROR: Provider request failed ({type(exc).__name__}); inspect provider diagnostics.", file=sys.stderr)
        return 1
    return 2


def _runtime_command(args: argparse.Namespace) -> int:
    from .project import resolve_project
    from .runtime import NativeRuntime
    from .tracing import TraceStore

    root = _agent_root(args.path)
    manifest = validate_agent(root)
    if args.command == "run" and manifest.build_type == "skill":
        if args.input or args.prompt or args.conversation_id or args.routing_key or args.capture_content is not None:
            raise ManifestError("Skill run is interactive; configure Codex content capture with aops configure.")
        if args.profile and args.profile != load_settings().profile:
            raise ConfigError("Skill run uses the configured Profile. Select it with aops configure first.")
        _codex_runtime_builder().prepare(root)
        return subprocess.run(["codex"], cwd=root, check=False).returncode
    profile, settings = _environment(args.profile)
    runtime = NativeRuntime(profile.name, settings=settings)
    if args.command == "link":
        result = runtime.link(root, args.resource_id, invocation_path=args.invocation_path)
    elif args.command == "deploy":
        result = runtime.deploy(
            root, apply=args.apply, name=args.name,
            warehouse_id=args.warehouse_id, parent_path=args.parent_path,
        )
        print("배포 리소스를 갱신했습니다. Registry 등록·운영 지정은 별도입니다.")
    else:
        project = resolve_project(root, settings)
        runtime.check_host(project)
        if manifest.build_type == "no-code" and (args.input or args.routing_key):
            raise ManifestError("Genie run uses --prompt and optionally --conversation-id, not --input or --routing-key.")
        if manifest.build_type == "code" and (args.prompt or args.conversation_id):
            raise ManifestError("Code run uses the complete native --input payload.")
        payload = json.loads(args.input.read_text(encoding="utf-8")) if args.input else None
        store = TraceStore(profile.name, project["tracing"])
        result = store.run(
            root,
            lambda: runtime.invoke(
                root, payload=payload, prompt=args.prompt,
                conversation_id=args.conversation_id, routing_key=args.routing_key,
            ),
            inputs={"prompt": args.prompt} if manifest.build_type == "no-code" else payload,
            capture_content=(
                args.capture_content if args.capture_content is not None
                else bool(settings and settings.capture_content and settings.content_capture_consent)
            ),
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _release_command(args: argparse.Namespace) -> int:
    from .governance import ReleaseGovernance
    from .tracing import evaluate_traces, trace_summary, validate_trace_evidence

    registry = _registry_client(args)
    governance = ReleaseGovernance(registry)
    if args.command == "production":
        result = governance.production(args.agent_id)
    elif args.command == "review":
        result = governance.review(
            args.agent_id, args.version, run_id=args.run_id, trace_id=args.trace_id,
        )
    elif args.command in {"promote", "rollback"}:
        result = governance.select(
            args.agent_id, args.version, expected_version=args.expected_version,
            rollback=args.command == "rollback",
        )
        print("운영 카탈로그 포인터만 변경했습니다. 런타임 재배포·복원은 수행하지 않았습니다.")
    else:
        release = registry.get(args.agent_id, args.version)
        store = governance._store(release)
        if args.command == "traces":
            result = store.search(args.agent_id, args.version, limit=args.limit)
        elif args.command == "trace":
            trace = store.get(args.trace_id)
            validate_trace_evidence(trace, release)
            result = {
                **trace_summary(trace),
                "spans": [{"name": span.name, "type": span.span_type} for span in trace.data.spans],
            }
        else:
            result = evaluate_traces(
                store, release, json.loads(args.data.read_text(encoding="utf-8")),
                judge_model=args.judge_model, consent=args.capture_content,
            )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if args.command == "review" and result["decision"] != "approved" else 0


def _agent_root(path: Path) -> Path:
    root = find_agent_root(path)
    if root is None:
        raise ManifestError(f"SPEC.md를 찾을 수 없습니다: {path.expanduser().resolve()}")
    return root


def _add_registry_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="configure에서 선택한 Profile 대신 사용할 값")
    parser.add_argument("--registry", help="configure에서 선택한 Registry 대신 사용할 <catalog>.<schema>")
    parser.add_argument("--warehouse-id", help="configure에서 선택한 SQL Warehouse 대신 사용할 ID")
    parser.add_argument("--volume", default="agent_artifacts", help="Artifact Volume 이름")


def _registry_client(args: argparse.Namespace) -> RegistryClient:
    profile, settings = _environment(args.profile)
    registry_name = args.registry or (settings.registry if settings else "")
    if not registry_name:
        raise RegistryError(
            "Agent Registry가 설정되지 않았습니다. "
            "`aops configure --profile <profile> --registry <catalog.schema>`를 실행하세요."
        )

    warehouse_id = args.warehouse_id or (settings.warehouse_id if settings else "") or get_default_warehouse(profile.name)
    location = parse_registry(registry_name, volume=args.volume)
    return RegistryClient(
        profile=profile.name,
        warehouse_id=warehouse_id,
        location=location,
        settings=settings,
    )


def _codex_runtime_builder() -> CodexRuntimeBuilder:
    return CodexRuntimeBuilder()


def _environment(profile_name: str | None) -> tuple[Profile, Settings | None]:
    try:
        settings = load_settings()
    except ConfigError:
        if config_path().exists():
            raise
        settings = None
    if not profile_name and settings is None:
        raise ConfigError("Databricks Profile을 먼저 선택하세요. `aops configure`를 실행하세요.")
    profile = get_profile(profile_name or settings.profile, list_profiles())
    if settings and settings.profile == profile.name and settings.host.rstrip("/") != profile.host.rstrip("/"):
        raise ConfigError("선택된 Profile의 Workspace URL이 달라졌습니다. `aops configure`를 다시 실행하세요.")
    validate_profile(profile)
    if settings and settings.host.rstrip("/") != profile.host.rstrip("/"):
        settings = None
    return profile, settings


def _configure(args: argparse.Namespace) -> int:
    from .tracing import configure_tracing

    try:
        previous = load_settings()
    except ConfigError:
        previous = None
    profiles = list_profiles()
    if args.profile:
        profile = get_profile(args.profile, profiles)
    elif previous:
        profile = get_profile(previous.profile, profiles)
        if previous.host.rstrip("/") != profile.host.rstrip("/"):
            raise ConfigError("저장된 Profile의 Workspace가 바뀌었습니다. --profile로 대상을 다시 확인하세요.")
    elif sys.stdin.isatty():
        profile = choose_profile(profiles)
    else:
        raise ConfigError("비대화형 설정에는 사용자가 선택한 --profile이 필요합니다.")
    if previous and previous.host.rstrip("/") != profile.host.rstrip("/"):
        previous = None
    registry_name = args.registry or (previous.registry if previous else "")
    experiment = args.experiment or (previous.experiment if previous else DEFAULT_EXPERIMENT)
    warehouse_id = args.warehouse_id or (previous.warehouse_id if previous else "")
    if sys.stdin.isatty():
        if not registry_name:
            registry_name = input("Registry / UC Trace 위치 (<catalog>.<schema>): ").strip()
        if args.experiment is None and previous is None:
            experiment = input(f"MLflow Experiment [{experiment}]: ").strip() or experiment
        if args.warehouse_id is None and not warehouse_id:
            warehouse_id = input(f"SQL Warehouse ID [{warehouse_id or 'Profile 기본값'}]: ").strip() or warehouse_id
    if not registry_name:
        raise ConfigError("Registry를 지정하세요: aops configure --profile <profile> --registry <catalog.schema>")
    parse_registry(registry_name)
    identity = validate_profile(profile)
    warehouse_id = warehouse_id or get_default_warehouse(profile.name)
    capture_content = args.capture_content if args.capture_content is not None else bool(previous and previous.capture_content)
    settings = Settings(
        profile=profile.name,
        host=profile.host,
        user_name=identity,
        warehouse_id=warehouse_id,
        registry=registry_name,
        experiment=experiment,
        capture_content=capture_content,
        content_capture_consent=capture_content,
        otel_port=args.otel_port if args.otel_port is not None else (previous.otel_port if previous else DEFAULT_OTEL_PORT),
        codex_enabled=args.codex if args.codex is not None else bool(previous and previous.codex_enabled),
        configured_at=datetime.now(timezone.utc).isoformat(),
    )
    settings = Settings.from_dict(vars(settings))
    binding = configure_tracing(settings)
    settings = replace(settings, experiment_id=binding["experiment_id"])
    trace_id = MlflowTurnExporter(settings).write_test_trace()
    if settings.codex_enabled:
        validate_codex_config()
        scorers = configure_turn_monitoring(settings)
        otel_mode, codex_path = configure_otel(settings)
        settings = replace(settings, otel_mode=otel_mode)
    path = save_settings(settings)
    print(f"설정 완료: {path}")
    print(f"Databricks: {identity} @ {profile.host}")
    print(f"MLflow Experiment: {settings.experiment}")
    print(f"SQL Warehouse: {settings.warehouse_id}")
    print(f"Agent Registry: {settings.registry or '미설정'}")
    print(f"UC Trace: {settings.registry} (experiment_id={settings.experiment_id})")
    print(f"Test Trace: {trace_id}")
    if settings.codex_enabled:
        ensure_collector(settings)
        print(f"Codex OTel: {codex_path} ({settings.otel_mode})")
        if settings.otel_mode != "local":
            print("경고: 기존 전역 exporter를 보존했습니다. 토큰 수집 경로는 aops doctor로 확인하세요.")
        print("백그라운드 Turn Scorer: " + ", ".join(scorer.name for scorer in scorers))
    print("이후 명령은 이 환경을 기본값으로 사용합니다.")
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
    from .session_exporter import export_pending_sessions

    session_result = export_pending_sessions(outbox, settings, limit=limit)

    return ExportResult(
        uploaded + session_result.uploaded,
        failed + session_result.failed,
        tuple(trace_ids) + session_result.trace_ids,
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
