"""Registry (read side): agents, versions, validation runs, deployments, production, events.

Field names follow the registry tables (`agents`, `agent_versions`, `agent_version_skills`,
`validation_runs`, `deployments`, `agent_production`, `agent_events`). Enumerations are the English
codes stored in Delta; screens translate them to Korean labels.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..common import DBX, Link, Page, command, todo

router = APIRouter(tags=["레지스트리"])

BuildType = Literal["skill", "code", "no_code"]
Lifecycle = Literal["registered", "production", "archived"]
VersionStatus = Literal["candidate", "registered"]
RunStatus = Literal["queued", "running", "passed", "failed", "error"]


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
    artifact_path: str | None = Field(None, examples=["/Volumes/main/aops/agent_artifacts/agents/price-helper/1.2.0/ab12….tar.gz"])
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


class AgentVersionDetail(AgentVersion):
    skills: list[AgentVersionSkill] = Field(default_factory=list)
    spec: dict[str, Any] = Field(default_factory=dict, description="등록 때 SPEC.md 스냅샷")


class ValidationStep(BaseModel):
    name: Literal["format", "evaluate", "guardrails", "compat", "record"]
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
    status: RunStatus
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
    app_name: str = Field(examples=["agent-bricks-price-helper"])
    app_url: str | None = None
    status: Literal["running", "succeeded", "failed"]
    dev: bool = False
    guardrail_checks: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime
    ended_at: datetime | None = None


class AgentProduction(BaseModel):
    agent_id: str
    current_version: str = Field(examples=["1.2.0"])
    previous_version: str | None = Field(None, examples=["1.1.0"])
    updated_at: datetime
    updated_by: str = Field(examples=["owner@example.com"])


class AgentEvent(BaseModel):
    event_id: str
    agent_id: str
    version: str | None = None
    event_type: Literal["register", "validate", "gate_requested", "gate_approved", "gate_rejected", "deploy", "promote", "rollback"]
    actor: str
    at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


@router.get(
    "/agents", summary="에이전트 목록 (레지스트리)",
    **command(None, DBX, screen="console 레지스트리"),
)
def list_agents(
    build_type: BuildType | None = None, lifecycle: Lifecycle | None = None, owner: str | None = None,
    q: str | None = None, cursor: str | None = None, limit: int = Query(50, le=200),
) -> Page[AgentStatus]:
    raise todo(None)


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
def list_events(agent_id: str, cursor: str | None = None) -> Page[AgentEvent]:
    raise todo(None)
