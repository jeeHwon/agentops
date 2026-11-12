"""Registry (read side): agents, versions, validation runs, deployments, production, events.

Field names follow the registry tables (`agents`, `agent_versions`, `agent_version_skills`,
`validation_runs`, `deployments`, `agent_production`, `agent_events`). Enumerations are the English
codes stored in Delta; screens translate them to Korean labels.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..common import DBX, Link, Page, command, todo
from ..dbx import caller_of, service_for

router = APIRouter(tags=["레지스트리"])

BuildType = Literal["skill", "code", "no_code"]
Lifecycle = Literal["registered", "production", "archived"]
VersionStatus = Literal["registered", "production", "retired"]
RunStatus = Literal["queued", "running", "passed", "failed", "stale", "error"]


class Agent(BaseModel):
    agent_id: str = Field(examples=["price-helper"])
    name: str = Field(examples=["가격 도우미"])
    build_type: BuildType
    owner: str = Field(examples=["owner@example.com"])
    owner_group: str | None = Field(None, examples=["AI-CoE"])
    service_criticality: Literal["low", "medium", "high"] = Field(examples=["medium"])
    data_sensitivity: Literal["public", "internal", "confidential", "personal"] = Field(examples=["internal"])
    security_level: str = Field(examples=["general"], description="관리자 정책의 등급 목록 코드 (기본 general · important · secret)")
    lifecycle: Lifecycle
    success_criteria: dict[str, float] = Field(default_factory=dict, examples=[{"correctness_min": 0.8, "latency_p90_seconds": 10}])


class AgentStatus(Agent):
    """One row of the registry screen (`agent_status` view)."""

    latest_version: str | None = Field(None, examples=["1.3.0"])
    latest_status: VersionStatus | None = None
    production_version: str | None = Field(None, examples=["1.2.0"])
    last_validation: RunStatus | None = None
    pending_gates: int = Field(0, examples=[1])
    source_missing: bool = Field(False, description="고등급이라 소스 없이 정보만 등록됨 (\"소스 없음\")")


class AgentVersionSkill(BaseModel):
    skill_alias: str = Field(examples=["pine-sql-style"])
    uc_name: str | None = Field(None, examples=["main.aops.price-helper__pine-sql-style"], description="게시를 건너뛴 워크스페이스에서는 비어 있음")
    skill_sha256: str
    source: str = Field(examples=["agent/skills/pine-sql-style", "main.team_skills.sql-guide"])
    publication_mode: Literal["agent", "shared_reference"]


class AgentVersion(BaseModel):
    agent_id: str = Field(examples=["price-helper"])
    version: str = Field(examples=["1.2.0"])
    status: VersionStatus
    source_sha256: str | None = Field(None, description="고등급(소스 없음)이면 비어 있음")
    artifact_path: str | None = Field(None, examples=["/Volumes/main/aops/price-helper/1.2.0.zip"], description="에이전트 볼륨의 버전 zip (한 번만 씀)")
    harness_sha256: str | None = None
    skills_sha256: str | None = None
    git_commit: str | None = Field(None, examples=["ff6530d"])
    harness: str | None = Field(None, examples=["claude"])
    harness_version: str | None = Field(None, examples=["2.1.317"])
    uc_model_version: int | None = Field(None, examples=[3])
    validation_run_id: str | None = None
    policy_version: int | None = Field(None, examples=[4])
    created_at: datetime
    registered_at: datetime | None = None


class GitRecord(BaseModel):
    status: Literal["synced", "failed", "skipped"] = Field(description="synced: 커밋·태그를 올림, failed: 다음 register·deploy 때 다시, skipped: 비밀값이 있어 올리지 않음")
    repo: str | None = Field(None, examples=["my-org/aops-price-helper"])
    url: str | None = Field(None, examples=["https://github.com/my-org/aops-price-helper/tree/v1.2.0"])
    commit_sha: str | None = None
    tag: str | None = Field(None, examples=["v1.2.0"])
    error: str | None = None


class AgentVersionDetail(AgentVersion):
    skills: list[AgentVersionSkill] = Field(default_factory=list)
    spec: dict[str, Any] = Field(default_factory=dict, description="등록 때 SPEC.md 스냅샷")
    git: GitRecord | None = Field(None, description="Git 사본 (정책에 git이 있을 때만). 실패해도 등록은 성공")


class ValidationStep(BaseModel):
    name: str = Field(examples=["format", "skills", "secrets", "evaluate", "guardrails", "compat", "record"])
    status: Literal["pending", "running", "passed", "failed", "skipped"]
    detail: str | None = Field(None, examples=["correctness 0.86 ≥ 0.80"])
    metrics: dict[str, float] = Field(default_factory=dict)


class ValidationRun(BaseModel):
    validation_run_id: str = Field(examples=["v-8f2c"])
    agent_id: str
    version: str
    source_sha256: str | None = None
    policy_version: int = Field(examples=[4])
    mode: Literal["job", "local"] = Field("job", description="local은 참고용이고 등록 근거가 되지 않는다")
    status: RunStatus = Field(description="`stale`: 점검 중에 개발용 정의가 바뀜 (노코드)")
    steps: list[ValidationStep] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict, examples=[{"correctness": 0.86, "relevance": 0.91, "safety": 1.0, "latency_p90_s": 7.2}])
    redteam_pass_rate: float | None = Field(None, examples=[0.97])
    failure_reason: str | None = None
    links: list[Link] = Field(default_factory=list, description="Job 실행, MLflow 실행")
    started_at: datetime
    ended_at: datetime | None = None


class Deployment(BaseModel):
    deployment_id: str = Field(examples=["d-31"])
    gate_id: str | None = Field(None, description="`--dev` 개인 시험 배포는 비어 있음")
    agent_id: str
    version: str
    environment: Literal["dev", "production"] = Field(description="dev: 개인 시험 배포, production: 승인된 deploy의 운영 반영")
    app_name: str = Field(examples=["agent-bricks-price-helper"], description="실제 앱 이름(agent-bricks-<base>) 또는 운영용 Genie Agent 이름. 스킬은 비어 있음")
    app_url: str | None = None
    status: Literal["running", "succeeded", "failed"]
    dependencies_match: bool | None = Field(None, description="앱에 깔린 의존 버전이 등록 때 uv.lock과 같은지")
    changed_files: list[str] = Field(default_factory=list, examples=[["app.yaml", "agent.toml"]], description="배포 도구가 고쳐 쓴 파일 (스냅샷 사본 기준)")
    guardrail_checks: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime | None = None


class AgentProduction(BaseModel):
    agent_id: str
    current_version: str = Field(examples=["1.2.0"])
    previous_version: str | None = Field(None, examples=["1.1.0"])
    app_name: str | None = Field(None, examples=["agent-bricks-price-helper"], description="운영 앱 이름 (스킬 기반은 비어 있음)")
    updated_at: datetime
    updated_by: str = Field(examples=["owner@example.com"])


class AgentEvent(BaseModel):
    event_id: str
    agent_id: str
    version: str | None = None
    event_type: Literal["register", "validate", "gate_requested", "gate_approved", "gate_rejected", "gate_canceled", "deploy",
                        "promote", "rollback", "load", "git"]
    actor: str
    at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "/agents", summary="에이전트 목록 (레지스트리)",
    **command("aops list", DBX, screen="console 레지스트리", note="권한이 없는 에이전트도 목록에는 보인다. 가져오기(load)만 막는다."),
)
def list_agents(
    request: Request, build_type: BuildType | None = None, lifecycle: Lifecycle | None = None, owner: str | None = None,
    q: str | None = None, cursor: str | None = None, limit: int = Query(50, le=200),
) -> Page[AgentStatus]:
    svc = service_for("aops list")
    caller_of(request, svc)
    rows = [r for r in svc.list_agents(build_type) if (not lifecycle or r["lifecycle"] == lifecycle) and (not owner or r["owner"] == owner)
            and (not q or q.lower() in (r["agent_id"] + " " + (r.get("name") or "")).lower())]
    return Page(items=[AgentStatus(**{**r, "pending_gates": int(r.get("pending_gates") or 0)}) for r in rows[:limit]])


class LoadArchive(BaseModel):
    url: str = Field(examples=["/api/v1/agents/price-helper/versions/1.2.0/archive"], description="버전 zip을 받는 이 서버의 경로")
    sha256: str = Field(description="안에 든 파일 내용의 해시 (CLI가 풀기 전에 확인)")
    size_bytes: int


class LoadInfo(BaseModel):
    """What `aops load` needs: a link for no-code and code agents, the version zip for skill agents."""

    agent_id: str
    build_type: BuildType
    version: str = Field(examples=["1.2.0"], description="가져오는 버전 (기본: 운영 버전)")
    link: str | None = Field(None, description="노코드: 운영용 Genie Agent, 코드: Databricks App 주소")
    archive: LoadArchive | None = Field(None, description="스킬: 에이전트 폴더로 풀 버전 zip")
    allowed: bool = Field(description="false면 권한이 없다 (목록에는 보이지만 가져올 수 없음)")


class LoadRequest(BaseModel):
    version: str | None = Field(None, description="`--version`: 비우면 운영 버전. 다른 버전은 담당자만")


@router.post(
    "/agents/{agent_id}/load", summary="에이전트 가져오기",
    **command("aops load", DBX, note=(
        "노코드는 운영용 Genie Agent 링크, 코드는 앱 링크. 스킬은 서버가 판정한다: 요청자 신원 → 버전(기본 운영 버전, 다른 버전은 담당자·담당 팀만, "
        "운영 버전이 없으면 409) → 담당자·담당 팀이 아니면 요청자의 전달 토큰으로 UC Skill의 SKILL.md를 읽어 본다(200 허용, 404 거부, UC가 그룹 소속까지 계산. `files` 범위가 막힌 워크스페이스는 서비스 주체가 실효 권한을 조회) → 없으면 403. "
        "통과하면 버전 zip 경로와 해시를 주고 agent_events에 load를 남긴다. CLI는 해시를 확인하고 풀어 하네스 설정을 만든 뒤 세션을 연다."
    )),
)
def load(agent_id: str, body: LoadRequest, request: Request) -> LoadInfo:
    svc = service_for("aops load")
    return LoadInfo(**svc.load(caller_of(request, svc), agent_id, body.version))


_ARCHIVE = command("aops load", DBX, note="`load`와 같은 판정을 거친다. 서버(서비스 주체)가 에이전트 볼륨의 `<version>.zip`을 읽어 내준다. 대상자에게는 볼륨 권한을 주지 않는다.")
_ARCHIVE["responses"] = {200: {"content": {"application/zip": {}}, "description": "버전 zip"}, **_ARCHIVE["responses"]}


@router.get("/agents/{agent_id}/versions/{version}/archive", summary="버전 zip 받기", response_class=Response, **_ARCHIVE)
def get_archive(agent_id: str, version: str, request: Request) -> Response:
    svc = service_for("aops load")
    data = svc.archive(caller_of(request, svc), agent_id, version)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{agent_id}-{version}.zip"'})


@router.get("/agents/{agent_id}", summary="에이전트 보기", **command(None, DBX, screen="console 레지스트리"))
def get_agent(agent_id: str) -> AgentStatus:
    raise todo(None)


@router.get("/agents/{agent_id}/versions", summary="버전 목록", **command(None, DBX, screen="console 레지스트리"))
def list_versions(agent_id: str, status: VersionStatus | None = None, cursor: str | None = None) -> Page[AgentVersion]:
    raise todo(None)


@router.get("/agents/{agent_id}/versions/{version}", summary="버전 보기 (스킬 포함)", **command(None, DBX, screen="console 레지스트리"))
def get_version(agent_id: str, version: str) -> AgentVersionDetail:
    raise todo(None)


@router.get("/agents/{agent_id}/validation-runs", summary="검증 이력", **command(None, DBX, screen="console 레지스트리"))
def list_validation_runs(agent_id: str, cursor: str | None = None) -> Page[ValidationRun]:
    raise todo(None)


@router.get("/agents/{agent_id}/deployments", summary="배포 이력", **command(None, DBX, screen="console 운영 지정 이력"))
def list_deployments(agent_id: str, cursor: str | None = None) -> Page[Deployment]:
    raise todo(None)


@router.get("/deployments/{deployment_id}", summary="배포 보기", **command(None, DBX))
def get_deployment(deployment_id: str) -> Deployment:
    raise todo(None)


@router.get("/agents/{agent_id}/production", summary="운영 버전", **command(None, DBX, screen="console 운영 지정 이력"))
def get_production(agent_id: str) -> AgentProduction:
    raise todo(None)


@router.get("/agents/{agent_id}/events", summary="수명주기 이력 (감사 로그)", **command(None, DBX, screen="console 운영 지정 이력"))
def list_events(agent_id: str, request: Request, cursor: str | None = None) -> Page[AgentEvent]:
    svc = service_for(None)
    caller_of(request, svc)
    return Page(items=[AgentEvent(**r) for r in svc.events(agent_id)])
