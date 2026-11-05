"""aops command line."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from . import __version__, harness
from .project import Project
from .runner import run_turn
from .store import Store


def cmd_run(args: argparse.Namespace) -> int:
    result = run_turn(Project.find(), args.prompt, harness_name=args.harness)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    else:
        print(result["response"])
        handoff = f" · handoff {result['handoff_turns']} turn(s)" if result["handoff_turns"] else ""
        tools = f" · tools {', '.join(result['tools'])}" if result["tools"] else ""
        print(f"\n[{result['harness']} {result['harness_version']}{handoff}{tools}]")
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
        for segment in store.segments(session["id"]):
            print(f"\nsegment {segment['id']} · {segment['harness']} {segment['harness_version']} · {segment['native_session']}")
            for turn in (t for t in store.turns(session["id"]) if t["segment_id"] == segment["id"]):
                tools = f"  [{', '.join(turn['tools'])}]" if turn["tools"] else ""
                print(f"  {turn['id']:>3}. {_short(turn['prompt'])}\n       → {_short(turn['response'])}{tools}")
    return 0


def _short(text: str, size: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"run": cmd_run, "session": cmd_session}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="aops", description="AI 에이전트 라이프사이클 관리")
    root.add_argument("--version", action="version", version=f"aops {__version__}")
    sub = root.add_subparsers(dest="command")

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
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command is None:
        parser().print_help()
        return 0
    return COMMANDS[args.command](args)
