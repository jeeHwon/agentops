"""Lifecycle commands: `aops validate`, `aops register`, `aops deploy`, `aops promote`, and approvals.

Approval happens only through these routes. The server checks the approver group and blocks
self-approval, so the console screen, Omnigent UI and any Custom Front follow the same rules. An external
approval extension (ITSM) posts its result to `/approvals/external`.
"""

from collections.abc import AsyncIterable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from ..common import DBX, Page, command, pending, todo
from ..events import ProgressEvent
from .registry import AgentProduction, AgentVersion, Deployment, ValidationRun

router = APIRouter(tags=["검증·등록·배포"])

GateDecision = Literal["pending_approval", "approved", "rejected", "canceled"]


class ValidationCreate(BaseModel):
    mode: Literal["job", "local"] = Field("job", description="`--local`: 내 PC에서 참고용으로")


class RegisterRequest(BaseModel):
    version: str | None = Field(None, examples=["1.2.0"], description="`--version`. 비우면 `[release] version`")


class DeployRequest(BaseModel):
    version: str = Field(examples=["1.2.0"])
    target_workspace: str | None = Field(None, description="`--target`. 비우면 현재 워크스페이스")
    dev: bool = Field(False, description="`--dev`: 승인 없는 개인 시험 배포 (agent-bricks-dev-<user>-<name>)")


class PromoteRequest(BaseModel):
    version: str = Field(examples=["1.2.0"], description="이전 운영 버전을 주면 되돌리기")
    expected_version: str | None = Field(None, examples=["1.1.0"], description="`--expected-version`: 동시 변경 방지")


class Approval(BaseModel):
    approval_id: str
    gate_id: str
    approver: str = Field(examples=["approver@example.com"])
    decision: Literal["approve", "reject"]
    comment: str | None = None
    channel: Literal["console", "external"] = "console"
    decided_at: datetime


class ReleaseGate(BaseModel):
    gate_id: str = Field(examples=["g-207"])
    agent_id: str = Field(examples=["price-helper"])
    version: str = Field(examples=["1.2.0"])
    target_workspace: str | None = None
    app_name: str | None = Field(None, examples=["agent-bricks-price-helper"])
    requester: str = Field(examples=["dev@example.com"])
    requested_at: datetime
    upgrade_type: Literal["initial", "major", "minor", "patch"] = Field(description="요청 시점 운영 버전 대비")
    validation_policy_mode: Literal["mandatory", "optional", "exempt"]
    approval_mode: Literal["required", "optional", "auto"]
    approval_channel: Literal["console", "external"]
    itsm_ticket_id: str | None = None
    itsm_ticket_url: str | None = None
    decision: GateDecision = Field(description="심사 상태 (`release_gates.decision`)")
    required_approvals: int = Field(examples=[1])
    approvals: int = Field(examples=[0])
    deployment_id: str | None = Field(None, description="승인 뒤 배포 Job이 만든 배포")


class ReleaseGateDetail(ReleaseGate):
    validation: ValidationRun | None = None
    history: list[Approval] = Field(default_factory=list)


class DecisionBody(BaseModel):
    comment: str | None = Field(None, examples=["평가·가드레일 확인함"])


class RejectBody(BaseModel):
    reason: str = Field(examples=["민감도 confidential인데 kr-pii 정책이 없음"])


class ExternalDecision(BaseModel):
    gate_id: str
    decision: Literal["approve", "reject"]
    approver: str = Field(description="외부 결재자의 Databricks 계정 (같은 승인 규칙으로 검사)")
    itsm_ticket_id: str = Field(examples=["CHG0031337"])
    itsm_ticket_url: str | None = None
    comment: str | None = None
    decided_at: datetime


@router.post("/projects/{project_id}/validations", status_code=202, summary="검증 시작 (관리자 정책)", **command("aops validate", DBX))
def create_validation(project_id: str, body: ValidationCreate) -> ValidationRun:
    raise todo("aops validate")


@router.get("/validations/{validation_run_id}", summary="검증 결과 (단계별)", **command(None, DBX, screen="검증 진행 화면"))
def get_validation(validation_run_id: str) -> ValidationRun:
    raise todo(None)


@router.get(
    "/validations/{validation_run_id}/events", response_class=EventSourceResponse, dependencies=[pending(None)], summary="검증 진행 (SSE)",
    **command(None, DBX, screen="검증 진행 화면"),
)
async def stream_validation(validation_run_id: str) -> AsyncIterable[ProgressEvent]:
    raise todo(None)
    yield  # pragma: no cover


@router.post(
    "/projects/{project_id}/registrations", status_code=201, summary="버전 등록 (후보 → registered)",
    **command("aops register", DBX, note="스킬은 스냅샷과 해시만 남긴다. 게시는 운영 지정(`promote`) 때 한다."),
)
def register(project_id: str, body: RegisterRequest) -> AgentVersion:
    raise todo("aops register")


@router.post(
    "/projects/{project_id}/deploy-requests", status_code=201, summary="배포 요청 (심사 대기)",
    **command("aops deploy", DBX, note="`dev=true`면 승인 없이 개인 시험 배포를 시작하고 배포를 돌려준다."),
)
def deploy(project_id: str, body: DeployRequest) -> ReleaseGate | Deployment:
    raise todo("aops deploy")


@router.post(
    "/agents/{agent_id}/promote", summary="운영 버전 지정 (되돌리기 포함)",
    **command("aops promote", DBX, note="스킬 기반은 이때 그 버전의 스킬을 Unity Gateway Skills에 게시한다(되돌리면 이전 버전을 다시 게시)."),
)
def promote(agent_id: str, body: PromoteRequest) -> AgentProduction:
    raise todo("aops promote")


@router.get(
    "/release-gates", summary="배포 심사 목록",
    **command(None, DBX, screen="console 배포 요청·승인"),
)
def list_gates(
    decision: GateDecision | None = None, mine: bool = False, to_approve: bool = False, cursor: str | None = None,
) -> Page[ReleaseGate]:
    raise todo(None)


@router.get("/release-gates/{gate_id}", summary="배포 심사 보기", **command(None, DBX, screen="console 배포 요청·승인"))
def get_gate(gate_id: str) -> ReleaseGateDetail:
    raise todo(None)


@router.post(
    "/release-gates/{gate_id}/approve", summary="승인",
    **command(None, DBX, screen="console [승인]", note="승인 그룹만, 요청자 본인은 불가. 필요한 승인 수가 차면 배포 Job이 시작된다."),
)
def approve(gate_id: str, body: DecisionBody) -> ReleaseGate:
    raise todo(None)


@router.post("/release-gates/{gate_id}/reject", summary="반려", **command(None, DBX, screen="console [반려]"))
def reject(gate_id: str, body: RejectBody) -> ReleaseGate:
    raise todo(None)


@router.post("/release-gates/{gate_id}/cancel", summary="요청 취소 (요청자)", **command(None, DBX, screen="console 배포 요청"))
def cancel_gate(gate_id: str) -> ReleaseGate:
    raise todo(None)


@router.post(
    "/approvals/external", summary="외부 결재 결과 받기 (확장용)",
    **command(None, DBX, note="정책의 결재 채널이 `external`일 때 외부 결재 확장이 부른다. `X-Aops-Signature`로 서명을 검사한다."),
)
def external_decision(body: ExternalDecision, x_aops_signature: str = Header()) -> Approval:
    raise todo(None)
