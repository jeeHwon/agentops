"""aops command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, admin, harness, init, lifecycle
from .project import Project
from .runner import run_turn
from .store import Store


def cmd_init(args: argparse.Namespace) -> int:
    if args.type == "code":
        import shutil

        if not shutil.which("agentbricks"):
            print("aops: 코드 기반은 Agent Bricks CLI가 필요합니다: uv tool install databricks-agentbricks", file=sys.stderr)
            return 2
        project = init.create_code(Path.cwd(), args.name)
        print(f"created {project.root} (Agent Bricks 프로젝트 + aops.toml·SPEC.md)")
        print(f"next: cd {args.name} → SPEC.md의 description, agent/agent.py를 고친 뒤 agentbricks dev로 시험, aops register")
        return 0
    if args.type != "skill":
        print("aops: 노코드 에이전트는 aops console의 [새 에이전트] → 노코드에서 만듭니다 (에이전트 폴더·SPEC.md·개발용 Genie).", file=sys.stderr)
        return 2
    harness.get(args.harness)  # fail early on an unknown harness
    project = init.create(Path.cwd(), args.name, harness=args.harness, edition=args.edition)
    print(f"created {project.root}")
    for folder in project.skill_folders():
        note = "  (템플릿: 폴더 이름과 SKILL.md를 내 스킬로 바꿔 쓰세요)" if folder.name == init.TEMPLATE_SKILL else ""
        print(f"  skill {folder.name}{note}")
    print(f"next: cd {args.name} → SPEC.md, agent/instructions.md, agent/skills/를 채운 뒤 aops run \"<질문>\"")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    result = run_turn(Project.find(), args.prompt, harness_name=args.harness)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    else:
        print(result["response"])
        handoff = f" · handoff {result['handoff_turns']} turn(s)" if result["handoff_turns"] else ""
        tools = f" · tools {', '.join(result['tools'])}" if result["tools"] else ""
        print(f"\n[{result['harness']} {result['harness_version']}{handoff}{tools}]")
        if result.get("trace"):
            print(f"기록: MLflow trace {result['trace']['trace_id']}  {result['trace']['experiment']}")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    store = Store(Project.find())
    current = store.current_session()
    if args.action == "new":
        session = store.create_session(args.harness or Project.find().harness)
        print(f"new session {session['id']} ({session['harness']})")
    elif args.action == "switch":
        harness.get(args.harness)  # fail early on an unknown harness
        session = current or store.create_session(args.harness)
        store.set_harness(session["id"], args.harness)
        print(f"session {session['id']}: next turn runs on {args.harness}")
    elif args.action == "list":
        for row in store.sessions():
            mark = "*" if current and row["id"] == current["id"] else " "
            print(f"{mark} {row['id']}  {row['harness']:<7} {row['turns']:>3} turn(s)  {row['last_turn_at'] or row['created_at']}")
    elif args.action == "show":
        session = store.session(args.id) if args.id else current
        if session is None:
            print("no session")
            return 1
        print(f"session {session['id']} · next harness {session['harness']}")
        first = next((store.trace_of(t["id"]) for t in store.turns(session["id"]) if store.trace_of(t["id"])), None)
        if first:
            print(f"MLflow: {first['experiment']}  {first['experiment_url']}")
        for segment in store.segments(session["id"]):
            print(f"\nsegment {segment['id']} · {segment['harness']} {segment['harness_version']} · {segment['native_session']}")
            for turn in (t for t in store.turns(session["id"]) if t["segment_id"] == segment["id"]):
                tools = f"  [{', '.join(turn['tools'])}]" if turn["tools"] else ""
                handed = "  (인계받음)" if turn["sent"] != turn["prompt"] else ""
                print(f"  {turn['id']:>3}. {_short(turn['prompt'])}{handed}\n       → {_short(turn['response'])}{tools}")
                info = store.trace_of(turn["id"])
                if info:
                    print(f"       trace {info['trace_id']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .server import create_app
    except ImportError:
        print("aops serve needs the serve extra: uv tool install 'aops-cli[serve]'", file=sys.stderr)
        return 1
    app = create_app(cors_origins=args.cors_origin or None)
    print(f"aops API docs: http://{args.host}:{args.port}/api/v1/docs")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _short(text: str, size: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": cmd_init, "run": cmd_run, "session": cmd_session, "serve": cmd_serve,
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aops", description="AI 에이전트 라이프사이클 관리")
    root.add_argument("--version", action="version", version=f"aops {__version__}")
    sub = root.add_subparsers(dest="command")

    init_ = sub.add_parser("init", help="새 에이전트 폴더 만들기 (스킬 기반 템플릿)")
    init_.add_argument("name")
    init_.add_argument("--type", choices=["skill", "code", "no-code"], default="skill")
    init_.add_argument("--harness", default="claude", help="기본 하네스 (claude, codex)")
    init_.add_argument("--edition", choices=["local", "dbx", "omni", "custom"], default="local")

    run = sub.add_parser("run", help="질문 하나를 현재 세션에서 실행")
    run.add_argument("prompt")
    run.add_argument("--harness", help="이 턴부터 쓸 하네스 (claude, codex)")
    run.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")

    session = sub.add_parser("session", help="세션 보기·전환")
    actions = session.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="세션 목록")
    show = actions.add_parser("show", help="세션의 구간과 턴")
    show.add_argument("id", nargs="?")
    switch = actions.add_parser("switch", help="다음 턴부터 다른 하네스로")
    switch.add_argument("--harness", required=True)
    new = actions.add_parser("new", help="새 세션 시작")
    new.add_argument("--harness")

    serve = sub.add_parser("serve", help="aops API 서버 (custom 에디션, /api/v1/docs)")
    serve.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (이 PC에서만)")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--cors-origin", action="append", help="브라우저에서 직접 부를 화면 주소 (여러 번 가능)")

    for name, handler in lifecycle.add_commands(sub).items():
        COMMANDS[name] = lambda args, handler=handler: lifecycle.run(handler, args)
    admin.add_command(sub)
    COMMANDS["admin"] = admin.cmd_admin
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command is None:
        parser().print_help()
        return 0
    try:
        return COMMANDS[args.command](args)
    except ValueError as error:  # bad input the user can fix (unknown harness, bad name, ...)
        print(f"aops: {error}", file=sys.stderr)
        return 2
