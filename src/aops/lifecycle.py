"""`aops register`, `validate`, `deploy`, `gates`·`approve`·`reject`, `promote`, `list` and `load` (dbx edition, through the aops server)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from . import package
from .client import Server, ServerError
from .project import Project
from .sync import sync

DONE = ("passed", "failed", "stale", "error")


def _project() -> Project:
    project = Project.find()
    if not project.is_project or not project.name:
        raise ValueError("aops 프로젝트 폴더가 아닙니다 (aops.toml [project] name 필요)")
    return project


def _say(text: str) -> None:
    print(text, flush=True)


def cmd_register(args: argparse.Namespace) -> int:
    project = _project()
    archive = package.build(project)
    _say(f"버전 zip: 파일 {len(archive.files)}개, {len(archive.data):,} bytes, 해시 {archive.sha256[:12]}")
    row = Server(project).upload(f"/projects/{project.name}/registrations", {"version": args.version, "bump": args.bump},
                                 "archive", f"{project.name}.zip", archive.data)
    _say(f"등록: {project.name} {row['version']} (상태 {row['status']})")
    _say(f"  볼륨: {row['artifact_path']}")
    for skill in row.get("skills") or []:
        _say(f"  스킬 {skill['skill_alias']} → 배포하면 UC Skill {skill.get('uc_name')}에 반영")
    git = row.get("git")
    if git and git["status"] == "synced":
        _say(f"  Git: {git['url']}")
    elif git and git["status"] == "skipped":
        _say(f"  Git: 올리지 않음 ({git.get('error')})")
    elif git:
        _say(f"  Git 반영 실패: {git.get('error')}\n    등록은 됐습니다. 다음 register나 deploy 때 다시 올립니다")
    return 0


def _target(args: argparse.Namespace) -> tuple[Server, str]:
    """The agent a command is about: `--agent` (a no-code agent has no folder), or the aops project folder here."""
    if getattr(args, "agent", None):
        here = Project.find()
        return Server(here if here.is_project else None), args.agent
    project = _project()
    return Server(project), project.name


def cmd_validate(args: argparse.Namespace) -> int:
    """Self-check: the Gate's form checks, now. Nothing is recorded and deploy does not ask for it.

    In an agent folder it runs here, without the server, on what `aops register` would send. With `--agent`
    the server checks that agent: a no-code agent's dev Genie, or a registered version (`--version`).
    """
    if not args.agent:
        from .dbx.service import check_agent

        project = _project()
        archive = package.build(project)
        files = package.read(archive.data)
        _say(f"셀프 체크: {project.name} (이 폴더, 파일 {len(archive.files)}개 · 기록하지 않음, 배포 조건 아님)")
        steps = check_agent(files)
    else:
        server, name = _target(args)
        result = server.upload(f"/projects/{name}/validations", {"version": args.version})
        _say(f"셀프 체크: {name} ({result['source']} · 기록하지 않음, 배포 조건 아님)")
        steps = result["steps"]
    _print_steps({"steps": steps})
    ok = all(step["status"] != "failed" for step in steps)
    _say("  통과: 배포 전 평가는 aops evaluate (배포 요청 때도 자동으로 돈다)" if ok else "  고칠 곳이 있습니다")
    return 0 if ok else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    """The Gate: the evaluate Job checks the form again and evaluates quality; recorded, and deploy reuses a pass."""
    server, name = _target(args)
    run = server.request("POST", f"/projects/{name}/evaluations", body={"version": args.version})
    _say(f"평가 시작: {name} {run['version']} (evaluate Job, Gate)")
    run = _wait_evaluation(server, run["validation_run_id"])
    _print_steps(run)
    for result in run.get("results") or []:
        if result["assessment"] != "GOOD":
            _say(f"    {result['assessment']} {result['question_id']} {(result.get('question') or '')[:50]} · "
                 f"{'; '.join(str(x) for x in result.get('reasons') or [])[:120]}")
    if run.get("failure_reason") and run["status"] != "failed":
        _say(f"  {run['failure_reason']}")
    _say(f"  평가 {run['status']}" + (": 같은 내용으로 배포를 요청하면 이 결과를 씁니다" if run["status"] == "passed" else ""))
    return 0 if run["status"] == "passed" else 1


def _poll(server: Server, path: str, tries: int = 5) -> dict[str, Any]:
    """GET while waiting on a Job. A passing server error (restart, write conflict) is asked again."""
    for attempt in range(tries):
        try:
            return server.request("GET", path)
        except ServerError as error:
            if error.status < 500 or attempt == tries - 1:
                raise
            time.sleep(3)
    raise AssertionError("unreachable")


def _wait_evaluation(server: Server, run_id: str) -> dict[str, Any]:
    last = None
    while True:
        run = _poll(server, f"/evaluations/{run_id}")
        if run["status"] != last:
            _say(f"  평가 {run['status']}")
            last = run["status"]
        if run["status"] in DONE:
            return run
        time.sleep(3)


def _print_steps(run: dict[str, Any]) -> None:
    for step in run.get("steps") or []:
        mark = {"passed": "✓", "skipped": "–"}.get(step["status"], "✗")
        _say(f"  {mark} {step['name']}: {step.get('detail') or ''}")


def cmd_deploy(args: argparse.Namespace) -> int:
    server, name = _target(args)
    if args.cancel:
        return _cancel(server, name)
    gate = server.request("POST", f"/projects/{name}/deploy-requests", body={"version": args.version, "reason": args.reason})
    _say(f"배포 요청: {name} {gate['version']} (변경 폭 {gate['upgrade_type']}, 평가 {gate['validation_policy_mode']}, "
         f"승인 {gate['approval_mode']})")
    seen: set[str] = set()
    pending_since: float | None = None
    while True:
        detail = _poll(server, f"/release-gates/{gate['gate_id']}")
        state = (detail["decision"], detail.get("deployment_status"))
        if state not in seen:
            seen.add(state)
            _report_gate(detail)
        if detail["decision"] in ("rejected", "canceled") or detail.get("deployment_status") in ("succeeded", "failed"):
            return 0 if detail.get("deployment_status") == "succeeded" else 1
        waiting_for_person = person_decides(detail)
        pending_since = (pending_since or time.time()) if waiting_for_person else None
        if waiting_for_person and (detail["approval_mode"] == "required" or detail.get("decision_reason")
                                   or time.time() - pending_since > 8):
            _say(f"  승인자에게 요청됨 (gate {detail['gate_id']}). 승인자: aops approve {detail['gate_id']} · "
                 f"반려: aops reject {detail['gate_id']} --reason <이유>")
            _say("  aops deploy를 다시 치면 이어서 보여 줍니다 · 취소: aops deploy --cancel")
            return 0
        time.sleep(3)



def person_decides(gate: dict[str, Any]) -> bool:
    """A person has to act: approval required/optional without auto approval, or a failed check under optional validation."""
    return gate["decision"] == "pending_approval" and (gate["approval_mode"] != "auto" or bool(gate.get("decision_reason")))


def _cancel(server: Server, agent_id: str) -> int:
    page = server.request("GET", f"/release-gates?agent_id={agent_id}")
    open_gates = [g for g in page["items"] if g["decision"] in ("validating", "pending_approval")]
    if not open_gates:
        _say(f"{agent_id}: 취소할 배포 요청이 없습니다")
        return 0
    gate = server.request("POST", f"/release-gates/{open_gates[0]['gate_id']}/cancel")
    _say(f"취소: {agent_id} {gate['version']} (gate {gate['gate_id']}, {gate['decision']})")
    return 0


def cmd_gates(args: argparse.Namespace) -> int:
    here = Project.find()
    server = Server(here if here.is_project else None)
    if args.mine:
        query = "?mine=true"
    elif args.agent:
        query = f"?agent_id={args.agent}"
    else:
        query = "?to_approve=true"
    rows = server.request("GET", f"/release-gates{query}")["items"]
    if not rows:
        _say("승인할 배포 요청이 없습니다" if query == "?to_approve=true" else "배포 요청이 없습니다")
        return 0
    _say(f"{'gate':<34} {'에이전트':<18} {'버전':<8} {'변경 폭':<8} {'상태':<17} 요청자")
    for g in rows:
        _say(f"{g['gate_id']:<34} {g['agent_id']:<18} {g['version']:<8} {g['upgrade_type']:<8} {g['decision']:<17} {g['requester']}")
        if g.get("decision_reason"):
            _say(f"{'':<34} └ {g['decision_reason']}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    here = Project.find()
    gate = Server(here if here.is_project else None).request(
        "POST", f"/release-gates/{args.gate_id}/approve", body={"comment": args.comment})
    state = "배포 Job 시작" if gate["decision"] == "approved" else f"승인 {gate['approvals']}/{gate['required_approvals']}"
    _say(f"승인: {gate['agent_id']} {gate['version']} (gate {gate['gate_id']}) → {state}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    here = Project.find()
    gate = Server(here if here.is_project else None).request(
        "POST", f"/release-gates/{args.gate_id}/reject", body={"reason": args.reason})
    _say(f"반려: {gate['agent_id']} {gate['version']} (gate {gate['gate_id']}) · {gate.get('decision_reason') or ''}")
    return 0


def _report_gate(gate: dict[str, Any]) -> None:
    decision, deployment = gate["decision"], gate.get("deployment_status")
    if decision == "validating":
        _say("  Gate: evaluate Job으로 평가 중…")
    elif decision == "pending_approval" and gate.get("decision_reason"):
        if gate.get("evaluation"):
            _print_steps(gate["evaluation"])
        _say(f"  {gate['decision_reason']}")
    elif decision == "pending_approval":
        _say("  Gate 통과 → " + ("정책 자동 승인 중…" if gate["approval_mode"] == "auto" else "승인 대기"))
    elif decision == "approved" and deployment == "running":
        if gate.get("evaluation"):
            _print_steps(gate["evaluation"])
        who = gate.get("approved_by")
        _say(f"  승인됨 ({'정책 자동 승인' if who == 'policy:auto' else f'승인자 {who}' if who else '승인'}) → 배포 Job 실행 중…")
    elif deployment == "succeeded":
        _say(f"  운영 반영 완료: {gate['agent_id']} {gate['version']} (운영 대상이 이 버전으로 바뀌었습니다: 스킬은 UC Skill, 코드는 앱, 노코드는 운영용 Genie)")
    elif deployment == "failed":
        _say("  배포 실패: 서버 기록(deployments)을 확인하세요")
    elif decision == "rejected":
        _say(f"  반려: {gate.get('decision_reason') or ''}")


def cmd_promote(args: argparse.Namespace) -> int:
    project = Project.find()
    grants = Server(project).request("POST", f"/agents/{args.agent_id}/promote", body={"principals": args.to, "revoke": args.revoke})
    for grant in grants:
        verb = "부여" if grant["granted"] else "회수"
        _say(f"{grant['principal']}: {grant['permission']} {verb} ({grant['securable']})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    project = Project.find()
    params = f"?build_type={args.type.replace('-', '_')}" if args.type else ""
    page = Server(project).request("GET", f"/agents{params}")
    rows = page["items"]
    if not rows:
        _say("등록된 에이전트가 없습니다")
        return 0
    _say(f"{'에이전트':<22} {'종류':<8} {'운영 버전':<10} {'최신':<8} {'상태':<11} 담당")
    for row in rows:
        _say(f"{row['agent_id']:<22} {row['build_type']:<8} {row.get('production_version') or '-':<10} "
             f"{row.get('latest_version') or '-':<8} {row['lifecycle']:<11} {row['owner']}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    here = Project.find()
    server = Server(here if here.is_project else None)
    info = server.request("POST", f"/agents/{args.agent_id}/load", body={"version": args.version})
    if not info["allowed"]:
        _say("이 에이전트를 가져올 권한이 없습니다. 담당자에게 promote를 요청하세요")
        return 1
    if info.get("link"):
        _say(f"{args.agent_id}: {info['link']}")
        return 0
    dest = Path(args.dir or args.agent_id).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest}가 비어 있지 않습니다. --dir로 다른 폴더를 고르세요")
    data = server.request("GET", f"/agents/{args.agent_id}/versions/{info['version']}/archive", raw=True)
    files = package.unpack(data, dest, info["archive"]["sha256"])
    project = Project.load(dest)
    sync(project)
    _say(f"가져옴: {args.agent_id} {info['version']} → {dest} (파일 {len(files)}개, 해시 확인)")
    for folder in project.skill_folders():
        _say(f"  skill {folder.name}")
    _say(f'next: cd {dest} && aops run "<질문>"')
    return 0


def add_commands(sub: argparse._SubParsersAction) -> dict[str, Any]:
    p = sub.add_parser("register", help="버전 저장 (dbx)")
    p.add_argument("--version")
    p.add_argument("--bump", choices=["fix", "minor", "major"])
    p = sub.add_parser("validate", help="셀프 체크: 형식 검사 (언제든, 기록 안 함, 배포 조건 아님)")
    p.add_argument("--agent", help="서버가 이 에이전트를 점검 (노코드: 개발용 Genie, 그 밖: 등록 버전). 없으면 이 폴더를 바로 점검")
    p.add_argument("--version", help="--agent와 함께: 점검할 등록 버전 (비우면 마지막)")
    p = sub.add_parser("evaluate", help="배포 전 평가 Gate: 형식 + 품질 평가 (evaluate Job, 기록, 배포 조건)")
    p.add_argument("--agent", help="이 폴더 대신 이 에이전트 (노코드는 지금 개발용 정의를 평가)")
    p.add_argument("--version", help="평가할 등록 버전 (비우면 마지막)")
    p = sub.add_parser("deploy", help="배포 요청 → 평가(Gate)·승인 → 운영 반영")
    p.add_argument("--agent", help="이 폴더 대신 이 에이전트 (노코드는 버전 없이: 개발용 정의를 스냅샷으로)")
    p.add_argument("--version")
    p.add_argument("--reason")
    p.add_argument("--cancel", action="store_true", help="진행 중인 배포 요청 취소 (요청자·담당 팀)")
    p = sub.add_parser("gates", help="배포 요청 목록 (기본: 내가 승인할 차례인 것)")
    p.add_argument("--mine", action="store_true", help="내가 낸 요청")
    p.add_argument("--agent", help="이 에이전트의 요청")
    p = sub.add_parser("approve", help="배포 요청 승인 (승인 그룹, 요청자 본인 불가)")
    p.add_argument("gate_id")
    p.add_argument("--comment")
    p = sub.add_parser("reject", help="배포 요청 반려 (승인 그룹, 요청자 본인 불가)")
    p.add_argument("gate_id")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("promote", help="대상자 권한 부여")
    p.add_argument("agent_id")
    p.add_argument("--to", action="append", required=True, help="group:<계정 그룹> | user:<이메일> | sp:<application ID>")
    p.add_argument("--revoke", action="store_true")
    p = sub.add_parser("list", help="등록된 에이전트 목록")
    p.add_argument("--type", choices=["skill", "code", "no-code"])
    p = sub.add_parser("load", help="등록된 에이전트를 가져와 쓰기")
    p.add_argument("agent_id")
    p.add_argument("--version")
    p.add_argument("--dir")
    return {"register": cmd_register, "validate": cmd_validate, "evaluate": cmd_evaluate, "deploy": cmd_deploy, "gates": cmd_gates,
            "approve": cmd_approve, "reject": cmd_reject, "promote": cmd_promote,
            "list": cmd_list, "load": cmd_load}


def run(handler: Any, args: argparse.Namespace) -> int:
    try:
        return handler(args)
    except ServerError as error:
        print(f"aops: {error} (HTTP {error.status})", file=sys.stderr)
        return 2
