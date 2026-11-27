"""`aops validate`: does the agent follow its template? A checklist, any time, nothing recorded.

The same rules run on a PC (no server), in the console, in Genie Code and as the first steps of the
Gate (`aops evaluate`). Each rule has a level:

- ERROR: the agent does not have the shape aops can register and deploy. The Gate rejects it.
- WARN: allowed, but worth fixing (a template leftover, a missing eval set, an unpinned dependency).

A check returns steps (`format`, `config`, `instructions`, `skills`, `evals`, `project`, `definition`,
`secrets`); every step lists its rules as `{id, level, passed, message, fix}`. A step fails when one of its
ERROR rules fails. Pure functions on the files of the agent: no Databricks calls.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

from . import package
from .evals import TEMPLATE_TEXT, read_questions, usable
from .init import INSTRUCTIONS_MD, SKILL_DESCRIPTION, SPEC_DESCRIPTION, TEMPLATE_SKILL

AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SKILL_NAME = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")  # Agent Skills: 1-64, no "--", no edge hyphen
CRITICALITY = ("low", "medium", "high")
SENSITIVITY = ("public", "internal", "confidential", "personal")
SECURITY_LEVELS = ("general", "important", "secret")  # the policy's default list
KINDS = ("skill", "code", "no_code")
SPEC_BODY = "이 에이전트가 하는 일, 쓰는 사람, 주의할 점을 적습니다."
SKILL_BODY = "폴더 이름과 위 `name`을 같은 스킬 이름으로 바꾸고"
PROJECT_FILES = ("agent.toml", "app.yaml", "pyproject.toml")
SAMPLE_TOOL = "agent/tools/sample_tool.py"
SECRET_PATTERNS = {
    "Databricks 토큰": re.compile(r"dapi[0-9a-f]{32}"),
    "GitHub 토큰": re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
    "AWS 키": re.compile(r"AKIA[0-9A-Z]{16}"),
    "개인 키": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
# .env files hold real values; .env.example and the like are templates (their content is still scanned)
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(?!\.(example|sample|template|dist)$)(\..*)?|.*\.pem|id_rsa)$")

# id: (level, what it checks, where to fix it). Grouped by step; `kinds` says which agents a step runs for.
RULES: dict[str, tuple[str, str, str]] = {
    "spec.present": ("ERROR", "SPEC.md가 있고 위쪽 --- 사이에 항목이 있다", "SPEC.md (aops init이 만든 형식)"),
    "spec.required": ("ERROR", "agent_id·name·build_type이 비어 있지 않다", "SPEC.md 위쪽 항목"),
    "spec.agent_id": ("ERROR", "agent_id가 영문 소문자·숫자·하이픈 63자 이하다", "SPEC.md agent_id"),
    "spec.build_type": ("ERROR", "build_type이 이 에이전트의 종류(skill · code · no_code)다", "SPEC.md build_type"),
    "spec.values": ("ERROR", "service_criticality ∈ low·medium·high, data_sensitivity ∈ public·internal·confidential·personal",
                    "SPEC.md service_criticality · data_sensitivity"),
    "spec.security_level": ("ERROR", "security_level이 정책의 보안 등급 중 하나다 (기본 general·important·secret)", "SPEC.md security_level"),
    "spec.description": ("ERROR", "description이 비어 있지 않고 init 템플릿 문구가 아니다", "SPEC.md description (이 에이전트가 하는 일 한 줄)"),
    "spec.body": ("WARN", "SPEC.md 본문(하는 일·쓰는 사람·주의할 점)을 적었다", "SPEC.md 아래쪽 본문"),
    "spec.approvers": ("WARN", "approvers(배포 요청을 승인할 사람)가 있다. 없으면 정책의 승인 그룹(없으면 요청자가 아닌 누구나)이 승인한다. "
                               "정책이 승인자 지정을 요구하면 ERROR", "SPEC.md approvers (이메일이나 group:<그룹>, 쉼표로)"),
    "spec.approvers_format": ("ERROR", "approvers의 값마다 이메일이나 group:<그룹> 형태다", "SPEC.md approvers"),
    "spec.approvers_other": ("ERROR", "approvers에 담당자(owner) 말고 한 명(또는 그룹) 이상 있다 (본인 요청은 본인이 승인할 수 없다)", "SPEC.md approvers"),
    "spec.approvers_exist": ("ERROR", "approvers의 사람·그룹이 이 워크스페이스에 있다 (서버에서만 확인)", "SPEC.md approvers (워크스페이스 계정·그룹 이름)"),
    "spec.target_workspace": ("ERROR", "target_workspace(운영 워크스페이스)를 적었으면 정책의 운영 워크스페이스 중 하나다 "
                                       "(비우면 정책의 기본 운영 워크스페이스)", "SPEC.md target_workspace (aops admin setup --workspace의 이름)"),
    "nocode.consumer_group": ("WARN", "공개 그룹(consumer_group)이 있다. 없으면 운영용 Genie는 담당자·담당 팀만 쓴다", "에이전트 폴더의 SPEC.md consumer_group"),
    "config.present": ("ERROR", "aops.toml이 있고 TOML로 읽힌다", "aops.toml (aops init이 만든 형식)"),
    "config.name": ("ERROR", "aops.toml [project] name이 SPEC.md agent_id와 같다", "aops.toml [project] name"),
    "config.type": ("ERROR", "aops.toml의 종류가 SPEC.md build_type과 같다 (코드 기반은 [project] type = \"code\")", "aops.toml [project] type"),
    "instructions.present": ("ERROR", "지시 파일(aops.toml [agent] instructions, 기본 agent/instructions.md)이 있고 비어 있지 않다", "agent/instructions.md"),
    "instructions.template": ("WARN", "지시를 이 에이전트에 맞게 고쳤다 (init 템플릿 그대로가 아니다)", "agent/instructions.md"),
    "skills.present": ("ERROR", "agent/skills/<이름>/SKILL.md가 하나 이상 있다", "agent/skills/<이름>/SKILL.md"),
    "skills.name": ("ERROR", "스킬 이름이 소문자·숫자·하이픈 64자 이하(하이픈으로 시작·끝·연속 불가)이고 SKILL.md name = 폴더 이름",
                    "agent/skills/<이름>/ 폴더 이름과 SKILL.md name"),
    "skills.description": ("ERROR", "SKILL.md description이 1~1024자이고 init 템플릿 문구가 아니다 (하네스가 이 설명을 보고 스킬을 고른다)",
                           "SKILL.md description"),
    "skills.template": ("ERROR", "init 템플릿 스킬(my-skill)이 남아 있지 않다", "agent/skills/my-skill을 내 스킬 이름으로"),
    "skills.body": ("WARN", "SKILL.md 본문에 규칙을 적었다 (템플릿 안내문 그대로가 아니다)", "SKILL.md 본문"),
    "evals.present": ("WARN", "평가셋 evals/questions.yaml이 있다 (배포 전 평가의 품질 섹션이 쓴다)", "evals/questions.yaml"),
    "evals.questions": ("WARN", "질문마다 text와 expected(정답)나 guideline(판정 기준)이 있고 템플릿 질문이 아니다", "evals/questions.yaml"),
    "project.files": ("ERROR", "Agent Bricks 프로젝트 파일 agent.toml·app.yaml·pyproject.toml이 있다", "agentbricks init이 만든 파일"),
    "project.agent_toml": ("ERROR", "agent.toml [agent] server = agentbricks, framework = langgraph · openai", "agent.toml [agent]"),
    "project.entry": ("WARN", "에이전트 코드 agent/agent.py가 있다 (Agent Bricks 템플릿의 진입점)", "agent/agent.py"),
    "project.command": ("WARN", "app.yaml에 앱을 띄우는 command가 있다", "app.yaml command"),
    "project.lock": ("WARN", "uv.lock으로 의존 버전이 고정돼 있다 (없으면 배포 때마다 최신 버전)", "uv lock으로 uv.lock 만들기"),
    "project.sample": ("WARN", "템플릿 샘플 도구(agent/tools/sample_tool.py)를 지웠거나 내 도구로 바꿨다", "agent/tools/"),
    "target.catalogs": ("ERROR", "운영 워크스페이스가 데이터 카탈로그를 정해 두었으면 데이터 소스가 모두 그 카탈로그에 있다 "
                                 "(개인 카탈로그의 데이터로 운영하지 않는다. 워크스페이스가 여럿일 때만)",
                        "Genie Data → 운영 워크스페이스가 허용한 카탈로그(예: project)의 테이블로"),
    "secrets.values": ("ERROR", "파일에 비밀값(" + "·".join(SECRET_PATTERNS) + ")이 없다", "비밀값은 Databricks secret·UC 연결로"),
    "secrets.files": ("ERROR", "금지 파일(.env, *.pem, id_rsa)이 없다 (.env.example 같은 예시는 됨)", "파일 지우기"),
}
STEPS: dict[str, dict[str, Any]] = {  # order, title and the kinds a step runs for
    "format": {"title": "SPEC.md", "kinds": KINDS},
    "config": {"title": "aops.toml", "kinds": ("skill", "code")},
    "instructions": {"title": "지시", "kinds": ("skill",)},
    "skills": {"title": "스킬", "kinds": ("skill",)},
    "evals": {"title": "평가셋", "kinds": ("skill",)},
    "project": {"title": "Agent Bricks 프로젝트", "kinds": ("code",)},
    "definition": {"title": "Genie 정의 규칙", "kinds": ("no_code",)},
    "target": {"title": "운영 위치", "kinds": ("no_code",)},
    "secrets": {"title": "비밀값·금지 파일", "kinds": KINDS},
}


def rules_of(step_name: str) -> list[str]:
    """The rule ids of a step (the no-code definition rules live in aops.dbx.genie_rules)."""
    heads = {"format": ("spec", "nocode")}.get(step_name, (step_name,))
    return [rid for rid in RULES if rid.split(".")[0] in heads]


def result(rule: str, passed: bool, message: str | None = None) -> dict[str, Any]:
    level, what, fix = RULES[rule]
    return {"id": rule, "level": level, "passed": bool(passed), "message": message or what, "fix": fix}


def step(name: str, rules: list[dict[str, Any]], ok: str, prefix: str = "") -> dict[str, Any]:
    """A step from its rule results: failed when an ERROR rule failed; the detail names only the problems."""
    errors = [r for r in rules if not r["passed"] and r["level"] == "ERROR"]
    warns = [r for r in rules if not r["passed"] and r["level"] != "ERROR"]
    if errors:
        detail = prefix + "; ".join(r["message"] for r in errors)
    else:
        detail = ok + (f" · 확인할 것 {len(warns)}건: " + "; ".join(r["message"] for r in warns) if warns else "")
    return {"name": name, "status": "failed" if errors else "passed", "detail": detail, "rules": rules}


def text_of(files: dict[str, bytes], rel: str) -> str:
    return files.get(rel, b"").decode("utf-8", "replace")


def spec_of(files: dict[str, bytes]) -> dict[str, str]:
    return package.front_matter(text_of(files, "SPEC.md"))


def kind_of(files: dict[str, bytes]) -> str:
    kind = str(spec_of(files).get("build_type") or "skill").replace("-", "_")
    return kind if kind in KINDS else "skill"


def body_of(text: str) -> str:
    return re.sub(r"^﻿?---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()


# ── steps ────────────────────────────────────────────────────────────────────

EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


def approvers_of(spec: dict[str, Any]) -> list[str]:
    """SPEC approvers as principals: `x@y.com` (a user) or `group:<name>`; `user:` is dropped, an empty list if none."""
    raw = spec.get("approvers") or ""
    items = raw if isinstance(raw, list) else str(raw).split(",")
    out = []
    for item in (str(x).strip() for x in items):
        if not item:
            continue
        if item.lower().startswith("user:"):
            item = item[5:].strip()
        out.append(item)
    return list(dict.fromkeys(out))


def approver_format_ok(principal: str) -> bool:
    return bool(EMAIL.match(principal)) or (principal.lower().startswith("group:") and bool(principal[6:].strip()))


def approver_rules(spec: dict[str, Any], policy: dict[str, Any] | None = None,
                   lookup: Any = None) -> list[dict[str, Any]]:
    """The approver rules of SPEC.md. `lookup(principal) -> True | False | None` checks the workspace (server only)."""
    approvers = approvers_of(spec)
    required = bool((policy or {}).get("approvers_required"))
    if not approvers:
        rule = result("spec.approvers", False, "approvers가 비어 있습니다" + (" (정책이 승인자 지정을 요구합니다)" if required else
                                                                      " (정책의 승인 그룹이나 요청자가 아닌 누구나 승인합니다)"))
        return [{**rule, "level": "ERROR"} if required else rule]
    bad = [a for a in approvers if not approver_format_ok(a)]
    owner = str(spec.get("owner") or "").strip().lower()
    others = [a for a in approvers if a.lower() != owner]
    rules = [result("spec.approvers", True, f"승인자 {', '.join(approvers)}"),
             result("spec.approvers_format", not bad, f"형태가 다른 값: {', '.join(bad)} (이메일이나 group:<그룹>)" if bad else None),
             result("spec.approvers_other", bool(others), None if others else "담당자 본인만 승인자입니다 (본인 요청은 본인이 승인할 수 없습니다)")]
    if lookup is not None and not bad:
        found = {a: lookup(a) for a in approvers}
        missing = [a for a, ok in found.items() if ok is False]
        unknown = [a for a, ok in found.items() if ok is None]
        if missing:
            rules.append(result("spec.approvers_exist", False, f"워크스페이스에 없는 승인자: {', '.join(missing)}"))
        elif unknown:
            rules.append({**result("spec.approvers_exist", True, f"확인하지 못한 승인자: {', '.join(unknown)} (서버가 계정 목록을 읽지 못함)"), "level": "WARN"})
        else:
            rules.append(result("spec.approvers_exist", True))
    return rules


def check_spec(files: dict[str, bytes], kind: str | None = None, policy: dict[str, Any] | None = None,
               lookup: Any = None) -> dict[str, Any]:
    """`format`: SPEC.md has its fields, allowed values, this kind, a real description and its approvers."""
    text = text_of(files, "SPEC.md")
    spec = package.front_matter(text)
    if not spec:
        return step("format", [result("spec.present", False, "SPEC.md가 없거나 위쪽 --- 사이 항목이 없습니다")], "", "SPEC.md: ")
    kind = kind or kind_of(files)
    rules = [result("spec.present", True)]
    missing = [k for k in ("agent_id", "name", "build_type") if not spec.get(k)]
    rules.append(result("spec.required", not missing, f"빈 항목 {', '.join(missing)}" if missing else None))
    rules.append(result("spec.agent_id", bool(AGENT_ID.match(spec.get("agent_id") or "")) or "agent_id" in missing,
                        None if AGENT_ID.match(spec.get("agent_id") or "") else f"agent_id {spec.get('agent_id')!r}가 형식에 맞지 않습니다"))
    given = (spec.get("build_type") or "").replace("-", "_")
    rules.append(result("spec.build_type", not given or given == kind, None if not given or given == kind else f"build_type이 {kind}가 아닙니다 ({spec['build_type']})"))
    bad = [k for k, allowed in (("service_criticality", CRITICALITY), ("data_sensitivity", SENSITIVITY)) if spec.get(k) not in allowed]
    rules.append(result("spec.values", not bad, f"허용되지 않는 값 {', '.join(f'{k}={spec.get(k)!r}' for k in bad)}" if bad else None))
    levels = [str(x.get("code") if isinstance(x, dict) else x) for x in ((policy or {}).get("security_levels") or SECURITY_LEVELS)]
    level = spec.get("security_level") or "general"
    rules.append(result("spec.security_level", level in levels, None if level in levels else f"security_level {level!r}가 정책의 보안 등급({', '.join(levels)})에 없습니다"))
    description = str(spec.get("description") or "").strip()
    if not description:
        rules.append(result("spec.description", False, "description이 비어 있습니다 (이 에이전트가 하는 일을 적으세요)"))
    else:
        rules.append(result("spec.description", description != SPEC_DESCRIPTION,
                            None if description != SPEC_DESCRIPTION else "description이 템플릿 그대로입니다 (이 에이전트가 하는 일을 적으세요)"))
    body = body_of(text)
    rules.append(result("spec.body", bool(body) and SPEC_BODY not in body and len(re.sub(r"^#.*$", "", body, flags=re.M).strip()) > 0,
                        None if body and SPEC_BODY not in body else "SPEC.md 본문이 템플릿 안내문 그대로입니다"))
    rules += approver_rules(spec, policy, lookup)
    rules += target_rules(spec, kind, policy)
    if kind == "no_code":
        rules.append(result("nocode.consumer_group", bool(spec.get("consumer_group"))))
    return step("format", rules, "SPEC.md 필수 항목과 값이 맞습니다", "SPEC.md: ")


def target_rules(spec: dict[str, Any], kind: str, policy: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`spec.target_workspace`, only when it is written or the policy lists workspaces (one workspace: as before)."""
    from . import workspaces

    name = workspaces.spec_target(spec)
    listed = workspaces.entries(policy)
    if not name and not listed:
        return []
    if kind == "skill":  # a skill's production is a UC skill: it is in no one workspace
        return [result("spec.target_workspace", True, None)]
    known = workspaces.Workspaces.from_policy(policy, "https://home.invalid")
    try:
        place = known.target(name)
    except KeyError:
        names = ", ".join(w.name for w in known.with_role("target"))
        return [result("spec.target_workspace", False, f"target_workspace {name!r}는 정책의 운영 워크스페이스({names})가 아닙니다")]
    return [result("spec.target_workspace", True, f"운영 워크스페이스 {place.name}")]


def check_target(files: dict[str, bytes], policy: dict[str, Any] | None) -> dict[str, Any] | None:
    """`target` (no-code, with workspaces): the data sources are in the catalogs the production workspace allows."""
    import json

    from . import workspaces

    if not workspaces.entries(policy):
        return None
    spec = spec_of(files)
    known = workspaces.Workspaces.from_policy(policy, "https://home.invalid")
    try:
        place = known.target(workspaces.spec_target(spec))
    except KeyError:
        return step("target", [result("target.catalogs", False, "운영 워크스페이스를 정하지 못했습니다 (SPEC.md target_workspace)")], "")
    try:
        data = (json.loads(files["genie/space.json"].decode("utf-8")).get("data_sources") or {})
    except (KeyError, ValueError):
        data = {}
    sources = {x.get("identifier", "") for k in ("tables", "metric_views") for x in (data.get(k) or [])}
    bad = workspaces.outside(sources, place.catalogs)
    message = (f"허용 밖: {', '.join(bad[:5])} (운영 워크스페이스 {place.name}의 카탈로그 {', '.join(place.catalogs)})" if bad
               else f"운영 워크스페이스 {place.name}" + (f": 카탈로그 {', '.join(place.catalogs)} 안의 데이터만 씁니다" if place.catalogs else ""))
    return step("target", [result("target.catalogs", not bad, message)], message)


def read_toml(files: dict[str, bytes], rel: str) -> tuple[dict[str, Any] | None, str | None]:
    if rel not in files:
        return None, f"{rel} 파일이 없습니다"
    try:
        return tomllib.loads(text_of(files, rel)), None
    except tomllib.TOMLDecodeError as error:
        return None, f"{rel} 파일을 읽지 못했습니다 ({error})"


def check_config(files: dict[str, bytes], kind: str) -> dict[str, Any]:
    """`config`: aops.toml belongs to this agent and says what kind it is."""
    toml, problem = read_toml(files, "aops.toml")
    if toml is None:
        return step("config", [result("config.present", False, problem)], "")
    project = toml.get("project") or {}
    agent_id = spec_of(files).get("agent_id") or ""
    name = project.get("name") or ""
    declared = "code" if project.get("type") == "code" else "skill"
    return step("config", [
        result("config.present", True),
        result("config.name", not agent_id or name == agent_id, None if not agent_id or name == agent_id else f"[project] name {name!r}가 agent_id {agent_id!r}와 다릅니다"),
        result("config.type", declared == kind, None if declared == kind else f"aops.toml은 {declared}인데 SPEC.md는 {kind}입니다"),
    ], "aops.toml이 이 에이전트의 것입니다")


def check_instructions(files: dict[str, bytes]) -> dict[str, Any]:
    """`instructions`: the instruction file the harnesses read exists and was written for this agent."""
    toml, _ = read_toml(files, "aops.toml")
    rel = str(((toml or {}).get("agent") or {}).get("instructions") or "agent/instructions.md")
    text = text_of(files, rel).strip()
    agent_id = spec_of(files).get("agent_id") or ""
    template = INSTRUCTIONS_MD.format(name=agent_id).strip()
    return step("instructions", [
        result("instructions.present", bool(text), None if text else f"{rel} 파일이 없거나 비어 있습니다"),
        result("instructions.template", bool(text) and text != template, None if text != template else f"{rel} 파일이 init 템플릿 그대로입니다"),
    ], f"{rel} 파일이 있습니다")


def skill_folders(files: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """agent/skills/<alias>/... grouped by alias; only folders with a SKILL.md count."""
    out: dict[str, dict[str, bytes]] = {}
    for rel, data in files.items():
        parts = rel.split("/")
        if len(parts) >= 4 and parts[0] == "agent" and parts[1] == "skills":
            out.setdefault(parts[2], {})["/".join(parts[3:])] = data
    return {alias: content for alias, content in out.items() if "SKILL.md" in content}


def check_skills(files: dict[str, bytes]) -> dict[str, Any]:
    """`skills`: Agent Skills format, and no init template left."""
    skills = skill_folders(files)
    rules = [result("skills.present", bool(skills), None if skills else "agent/skills 아래에 SKILL.md가 있는 스킬이 없습니다")]
    names, descriptions, bodies = [], [], []
    for alias, content in sorted(skills.items()):
        text = content["SKILL.md"].decode("utf-8", "replace")
        front = package.front_matter(text)
        if not SKILL_NAME.match(alias):
            names.append(f"{alias}: 스킬 이름은 소문자·숫자·하이픈 64자 이하 (하이픈으로 시작·끝·연속 불가)")
        if front.get("name") != alias:
            names.append(f"{alias}: SKILL.md의 name이 폴더 이름과 다릅니다")
        description = str(front.get("description") or "").strip()
        if not 1 <= len(description) <= 1024:
            descriptions.append(f"{alias}: description은 1~1024자")
        elif description == SKILL_DESCRIPTION and alias != TEMPLATE_SKILL:
            descriptions.append(f"{alias}: description이 템플릿 그대로입니다 (언제 이 스킬을 쓰는지 적으세요)")
        if SKILL_BODY in text or not body_of(text):
            bodies.append(f"{alias}: 본문이 템플릿 안내문 그대로이거나 비어 있습니다")
    if skills:
        rules += [result("skills.template", TEMPLATE_SKILL not in skills,
                         None if TEMPLATE_SKILL not in skills else f"{TEMPLATE_SKILL}: init 템플릿입니다. 폴더 이름과 SKILL.md를 내 스킬로 바꾸세요"),
                  result("skills.name", not names, "; ".join(names) or None),
                  result("skills.description", not descriptions, "; ".join(descriptions) or None),
                  result("skills.body", not bodies, "; ".join(bodies) or None)]
    return step("skills", rules, f"스킬 {len(skills)}개 형식이 맞습니다 ({', '.join(sorted(skills))})")


def check_evals(files: dict[str, bytes]) -> dict[str, Any]:
    """`evals`: an eval set the Gate's quality section can judge (warnings only)."""
    raw = files.get("evals/questions.yaml")
    if raw is None:
        return step("evals", [result("evals.present", False, "evals/questions.yaml 파일이 없습니다 (배포 전 평가의 품질 섹션이 실패로 나옵니다)")], "")
    questions = read_questions(raw.decode("utf-8", "replace"))
    good = usable(questions)
    template = [q["id"] for q in questions if q["text"] == TEMPLATE_TEXT]
    unjudged = [q["id"] for q in questions if q["text"] != TEMPLATE_TEXT and not (q.get("expected") or q.get("guideline"))]
    problems = ([f"템플릿 질문 {', '.join(template)}"] if template else []) + ([f"정답·판정 기준이 없는 질문 {', '.join(unjudged)}"] if unjudged else [])
    if not good:
        problems.insert(0, "채점할 수 있는 질문이 없습니다 (품질 섹션이 실패로 나옵니다)")
    return step("evals", [result("evals.present", True),
                          result("evals.questions", not problems, "; ".join(problems) or None)], f"평가셋 {len(good)}문항")


def check_project(files: dict[str, bytes]) -> dict[str, Any]:
    """`project` (code agents): an Agent Bricks project on the durable server."""
    missing = [name for name in PROJECT_FILES if name not in files]
    rules = [result("project.files", not missing, f"없는 파일: {', '.join(missing)}" if missing else None)]
    toml, problem = read_toml(files, "agent.toml")
    if toml is not None:
        agent = toml.get("agent") or {}
        problems = ([f"agent.toml [agent] server가 {agent.get('server')!r}입니다 (agentbricks만)"] if agent.get("server", "agentbricks") != "agentbricks" else []) \
            + (["agent.toml [agent] framework는 langgraph 또는 openai"] if agent.get("framework") not in ("langgraph", "openai") else [])
        rules.append(result("project.agent_toml", not problems, "; ".join(problems) or None))
    elif "agent.toml" in files:
        rules.append(result("project.agent_toml", False, problem))
    rules.append(result("project.entry", "agent/agent.py" in files))
    command = re.search(r"(?m)^command\s*:", text_of(files, "app.yaml")) is not None
    rules.append(result("project.command", command or "app.yaml" not in files))
    rules.append(result("project.lock", "uv.lock" in files, None if "uv.lock" in files else "uv.lock이 없어 배포 때 의존 버전이 고정되지 않습니다"))
    rules.append(result("project.sample", SAMPLE_TOOL not in files, None if SAMPLE_TOOL not in files else f"{SAMPLE_TOOL} 파일이 남아 있습니다 (쓰지 않으면 지우세요)"))
    return step("project", rules, "Agent Bricks 프로젝트 형식이 맞습니다")


def find_secrets(files: dict[str, bytes]) -> list[str]:
    """Files with a secret-looking value or a forbidden name (the Gate's secrets check, also run before Git)."""
    leaks = [f"{rel} ({label})" for rel, data in files.items() for label, pattern in SECRET_PATTERNS.items()
             if pattern.search(data.decode("utf-8", "ignore"))]
    return leaks + [f"{rel} (금지 파일)" for rel in files if FORBIDDEN_FILES.search(rel)]


def check_secrets(files: dict[str, bytes]) -> dict[str, Any]:
    values = [f"{rel} ({label})" for rel, data in files.items() for label, pattern in SECRET_PATTERNS.items()
              if pattern.search(data.decode("utf-8", "ignore"))]
    forbidden = [rel for rel in files if FORBIDDEN_FILES.search(rel)]
    return step("secrets", [result("secrets.values", not values, "비밀값: " + ", ".join(values) if values else None),
                            result("secrets.files", not forbidden, "금지 파일: " + ", ".join(forbidden) if forbidden else None)],
                "비밀값·금지 파일이 없습니다")


def check_definition(files: dict[str, bytes], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """`definition` (no-code): the Genie definition rules of the snapshot (`genie/space.json`)."""
    import json

    from .dbx import genie_rules

    no_code = (policy or {}).get("no_code") or {}
    try:
        definition = json.loads(files["genie/space.json"].decode("utf-8"))
    except (KeyError, ValueError) as error:
        return {"name": "definition", "status": "failed", "detail": f"genie/space.json을 읽지 못했습니다 ({error})", "rules": []}
    report = genie_rules.format_step(definition, lint_min=float(no_code.get("lint_min", 0.9)), rules=no_code.get("rules"))
    return {**report, "name": "definition", "metrics": {"score": report["score"]}}


def check(files: dict[str, bytes], kind: str | None = None, policy: dict[str, Any] | None = None,
          lookup: Any = None) -> list[dict[str, Any]]:
    """Every step for this kind of agent (from SPEC.md when not given), in checklist order. `lookup` checks that
    the approvers exist in the workspace (the server passes it; a PC cannot)."""
    kind = kind or kind_of(files)
    steps = [check_spec(files, kind, policy, lookup)]
    if kind in ("skill", "code"):
        steps.append(check_config(files, kind))
    if kind == "skill":
        steps += [check_instructions(files), check_skills(files), check_evals(files)]
    elif kind == "code":
        steps.append(check_project(files))
    else:
        steps.append(check_definition(files, policy))
        target = check_target(files, policy)
        if target:
            steps.append(target)
    steps.append(check_secrets(files))
    return steps


def passed(steps: list[dict[str, Any]]) -> bool:
    return all(s["status"] != "failed" for s in steps)


def catalog() -> list[dict[str, Any]]:
    """Every rule with its step and the kinds it applies to (docs/validate.md is made from this)."""
    from .dbx.genie_rules import RULES as GENIE

    def step_of(rule: str) -> str:
        head = rule.split(".")[0]
        return {"spec": "format", "nocode": "format"}.get(head, head)

    rows = [{"step": step_of(rid), "id": rid, "level": level, "check": what, "fix": fix,
             "kinds": ("no_code",) if rid.startswith("nocode.") else STEPS[step_of(rid)]["kinds"]}
            for rid, (level, what, fix) in RULES.items()]
    rows += [{"step": "definition", "id": rid, "level": str(rule["level"]).upper(), "check": rule.get("check") or rid, "fix": rule["fix"],
              "kinds": ("no_code",)} for rid, rule in GENIE.items()]
    order = list(STEPS)
    return sorted(rows, key=lambda r: order.index(r["step"]))


KIND_LABEL = {"skill": "스킬", "code": "코드", "no_code": "노코드"}


def markdown() -> str:
    """docs/validate.md: every rule, by step, with the kinds it runs for (scripts/validate_docs.py writes it)."""
    lines = [
        "# 셀프 체크 (`aops validate`) 규칙",
        "",
        "> 이 문서는 `scripts/validate_docs.py`가 `aops.validate`의 규칙 목록으로 만듭니다. 손으로 고치지 않습니다.",
        "",
        "셀프 체크는 에이전트가 **템플릿대로 잘 구성됐는지** 보는 체크리스트입니다. 언제든 돌리고, 기록하지 않으며, 배포 조건이 아닙니다.",
        "같은 규칙이 내 PC(`aops validate`, 서버 없이), console [셀프 체크], Genie Code에서 돌고, 배포 전 평가(`aops evaluate`)의 첫 단계로 다시 돕니다.",
        "",
        "- **ERROR:** aops가 등록·배포할 수 있는 형태가 아닙니다. 배포 전 평가가 반려합니다.",
        "- **WARN:** 막지 않지만 고칠 것 (템플릿 흔적, 평가셋 빠짐, 의존 버전 고정 안 됨 등).",
        "",
        "| 종류 | 단계 |",
        "| --- | --- |",
    ]
    for kind in KINDS:
        lines.append(f"| {KIND_LABEL[kind]} | " + " → ".join(f"`{name}`" for name, info in STEPS.items() if kind in info["kinds"]) + " |")
    rows = catalog()
    for name, info in STEPS.items():
        mine = [r for r in rows if r["step"] == name]
        kinds = " · ".join(KIND_LABEL[k] for k in info["kinds"])
        lines += ["", f"## `{name}` {info['title']} ({kinds})", ""]
        if name == "definition":
            lines += ["노코드의 Genie 정의(`genie/space.json`) 규칙입니다. 수준과 값은 정책의 `no_code.rules`로 바꿀 수 있고, "
                      "ERROR 위반이 없으며 준수율이 정책 기준(`no_code.lint_min`, 기본 90%) 이상이어야 통과합니다.", ""]
        lines += ["| 규칙 | 수준 | 보는 것 | 고치는 곳 |", "| --- | --- | --- | --- |"]
        for r in mine:
            only = f" ({KIND_LABEL[r['kinds'][0]]})" if len(r["kinds"]) == 1 and len(info["kinds"]) > 1 else ""
            lines.append(f"| `{r['id']}`{only} | {r['level']} | {r['check']} | {r['fix']} |")
    lines += ["", "## 출력", "",
              "단계마다 `✓`(통과)·`✗`(ERROR 위반) 한 줄, 그 아래 위반·확인할 규칙을 `규칙 ID · 고치는 곳`과 함께 보여 줍니다. "
              "API(`POST /api/v1/projects/{id}/validations`)는 단계마다 `rules: [{id, level, passed, message, fix}]`를 돌려줍니다.", ""]
    return "\n".join(lines)
