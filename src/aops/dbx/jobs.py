"""`aops-job`: the entry point of the validate and deploy Lakeflow Jobs (python wheel task).

Each run prints what it checked or changed, so the Job run page shows the whole story: target, rules,
results and where the result is recorded.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import __version__
from .service import CHECK_RULES, Config, Service


def print_validation(run_id: str, registry: str, report: dict[str, Any]) -> None:
    say = lambda text="": print(text, flush=True)  # noqa: E731
    say("aops validate — 배포 전 검사 (Gate)")
    say(f"  에이전트    {report['agent_id']} {report['version']}  (validation_run_id {run_id})")
    if report.get("artifact_path"):
        say(f"  검사 대상   {report['artifact_path']}  (파일 {len(report['files'])}개, sha256 {(report['sha256'] or '')[:12]})")
    say(f"  실행 코드   aops_cli {__version__} · aops.dbx.service.check_skill_agent")
    for i, step in enumerate(report.get("steps") or [], 1):
        mark = "✓" if step["status"] == "passed" else "✗"
        say(f"\n  [{i}] {step['name']:<8}{mark} {step.get('detail') or ''}")
        if step["name"] in CHECK_RULES:
            say(f"      규칙: {CHECK_RULES[step['name']]}")
    if report.get("files"):
        say("\n  검사한 파일")
        for rel in report["files"]:
            say(f"    {rel}")
    if report.get("reason"):
        say(f"\n  사유: {report['reason']}")
    say(f"\n결과: {report['status']} → {registry}.validation_runs (validation_run_id = '{run_id}')")


def print_deploy(gate_id: str, registry: str, report: dict[str, Any]) -> None:
    say = lambda text="": print(text, flush=True)  # noqa: E731
    say("aops deploy — 운영 반영")
    say(f"  에이전트    {report['agent_id']} {report['version']}  (이전 운영 버전 {report.get('previous') or '없음'}, gate_id {gate_id})")
    say(f"  승인        {report.get('approval_mode') or '-'} · 요청 {report.get('requester')}")
    say(f"  버전 zip    {report.get('artifact_path')}  (승인 상태와 해시를 다시 확인함)")
    say(f"  실행 코드   aops_cli {__version__} · aops.dbx.service.Service.run_deploy")
    words = {"created": "새로 만들고 내용을 넣음", "updated": "내용을 이 버전으로 바꿈", "unchanged": "이미 이 버전이라 그대로"}
    for skill in report.get("skills") or []:
        say(f"\n  UC Skill {skill['uc_name']}: {words.get(skill['action'], skill['action'])}")
        for rel in skill["files"]:
            say(f"    {rel}")
    for name in report.get("not_in_version") or []:
        say(f"\n  UC Skill {name}: 이 버전에는 없는 스킬 (지우지 않고 그대로 둠. 쓰던 사람은 이전 내용을 계속 받음)")
    git = report.get("git")
    if git:
        line = git.get("url") if git["status"] == "synced" else git.get("error")
        say(f"\n  Git production 브랜치: {git['status']} {line or ''} (실패해도 배포는 그대로)")
    say(f"\n결과: {report['status']} → {registry}.agent_production · deployments · agent_events")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aops-job")
    parser.add_argument("--kind", choices=["validate", "deploy"], required=True)
    parser.add_argument("--id", required=True, help="validation_run_id (validate) or gate_id (deploy)")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--warehouse", required=True)
    args = parser.parse_args(argv)
    service = Service(Config(registry=args.registry, warehouse_id=args.warehouse))
    if args.kind == "validate":
        report = service.run_validation(args.id)
        print_validation(args.id, args.registry, report)
        return 0 if report["status"] in ("passed", "failed") else 1
    report = service.run_deploy(args.id)
    print_deploy(args.id, args.registry, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
