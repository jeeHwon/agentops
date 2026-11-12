"""Lifecycle commands: `aops validate`, `aops register`, `aops deploy`, `aops promote`, and approvals.

Approval happens only through these routes. The server checks the approver group and blocks
self-approval, so the console screen, Omnigent UI and any Custom Front follow the same rules. An external
approval extension (ITSM) posts its result to `/approvals/external`.
"""

from collections.abc import AsyncIterable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from ..common import DBX, Page, command, pending, todo
from ... import package
from ...dbx.service import ServiceError
from ...dbx.sql import loads
from ..dbx import caller_of, service_for
from ..events import ProgressEvent
from .registry import AgentVersionDetail, AgentVersionSkill, Deployment, ValidationRun

router = APIRouter(tags=["검증·등록·배포"])

GateDecision = Literal["validating", "pending_approval", "approved", "rejected", "canceled"]


class ValidationCreate(BaseModel):
    mode: Literal["job", "local"] = Field("job", description="`--local`: 내 PC에서 참고용으로")
    version: str | None = Field(None, examples=["1.2.0"], description="`--version`: 비우면 마지막 등록 버전")


class RegisterRequest(BaseModel):
    version: str | None = Field(None, examples=["1.2.0"], description="`--version`. 비우면 register가 매긴다 (최초 1.0.0)")
    bump: Literal["fix", "minor", "major"] | None = Field(None, description="버전을 올리는 폭. 비우면 register 규칙을 따른다")


class DeployRequest(BaseModel):
    version: str | None = Field(None, examples=["1.2.0"], description="배포할 등록 버전. 노코드는 비운다(개발용 정의를 스냅샷으로 뜬다). 되돌리기는 이전 버전")
    reason: str | None = Field(None, examples=["벤치마크 문항 보강"], description="요청 사유")
    target_workspace: str | None = Field(None, description="`--target`. 비우면 현재 워크스페이스")
    dev: bool = Field(False, description="`--dev`: 승인 없는 개인 시험 배포 (agent-bricks-d<사용자 해시 4자>-<이름>)")


class PromoteRequest(BaseModel):
    principals: list[str] = Field(
        examples=[["group:finance-analysts", "user:user@example.com", "sp:1b2c3d4e-0000-0000-0000-000000000000"]],
        description="`--to`: `group:<그룹 이름>`·`user:<이메일>`·`sp:<application ID>`",
    )
    revoke: bool = Field(False, description="`--revoke`: 권한 회수")


class Grant(BaseModel):
    agent_id: str
    securable: str = Field(examples=["skill:main.aops.price-helper__pine-sql-style"], description="권한을 건 플랫폼 객체 (스킬, 운영용 Genie, 앱)")
    principal: str = Field(examples=["group:finance-analysts"])
    permission: str = Field(examples=["CAN_RUN", "CAN_USE", "READ_VOLUME"], description="노코드 CAN RUN, 코드 앱 CAN USE, 스킬 UC Skill 읽기")
    granted: bool = Field(description="false면 회수된 것")
    at: datetime


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
    upgrade_type: Literal["initial", "major", "minor", "patch", "rollback"] = Field(description="요청 시점 운영 버전 대비")
    validation_policy_mode: Literal["mandatory", "optional", "exempt"]
    approval_mode: Literal["required", "optional", "auto"]
    approval_channel: Literal["console", "external"]
    itsm_ticket_id: str | None = None
    itsm_ticket_url: str | None = None
    decision: GateDecision = Field(description="심사 상태 (`release_gates.decision`)")
    required_approvals: int = Field(examples=[1])
    approvals: int = Field(examples=[0])
    approved_by: str | None = Field(None, examples=["policy:auto"], description="마지막으로 승인한 사람. 정책의 자동 승인은 policy:auto")
    deployment_id: str | None = Field(None, description="승인 뒤 배포 Job이 만든 배포")
    deployment_status: Literal["running", "succeeded", "failed"] | None = Field(None, description="배포 Job의 진행 상태")
    decision_reason: str | None = Field(None, description="반려 사유 등")
    validation_run_id: str | None = Field(None, description="근거 검사 실행")


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
def create_validation(project_id: str, body: ValidationCreate, request: Request) -> ValidationRun:
    svc = service_for("aops validate")
    return ValidationRun(**svc.start_validation(caller_of(request, svc), project_id, body.version))


class GenieCheckRequest(BaseModel):
    space_id: str = Field(examples=["01f1d68538081bcfb0dec6f1a97f0cd7"], description="개발용 Genie 스페이스 ID")


@router.post(
    "/no-code/checks", summary="노코드 자가 점검 (Genie 정의 규칙)",
    **command("aops validate", DBX, note="노코드: Gate의 정의 규칙(준수율)을 지금 바로 돌린다. 참고용이며 기록하지 않는다. 서버가 서비스 주체로 정의를 읽으므로 스페이스를 aops 서비스 주체와 공유(편집)해야 한다. 벤치마크는 배포 요청 때."),
)
def genie_check(body: GenieCheckRequest, request: Request) -> dict:
    svc = service_for("aops validate")
    return svc.genie_check(caller_of(request, svc), body.space_id.strip())


@router.get("/validations/{validation_run_id}", summary="검증 결과 (단계별)", **command(None, DBX, screen="검증 진행 화면"))
def get_validation(validation_run_id: str, request: Request) -> ValidationRun:
    svc = service_for(None)
    caller_of(request, svc)
    return ValidationRun(**svc.validation(validation_run_id))


@router.get(
    "/validations/{validation_run_id}/events", response_class=EventSourceResponse, dependencies=[pending(None)], summary="검증 진행 (SSE)",
    **command(None, DBX, screen="검증 진행 화면"),
)
async def stream_validation(validation_run_id: str) -> AsyncIterable[ProgressEvent]:
    raise todo(None)
    yield  # pragma: no cover


@router.post(
    "/projects/{project_id}/registrations", status_code=201, summary="버전 저장 (최초 1.0.0)",
    **command("aops register", DBX, note="검증을 요구하지 않는다(강제는 deploy 요청의 Gate). 에이전트 볼륨에 버전 zip을 한 번만 쓴다. 스킬의 UC Skill은 첫 deploy 때 배포 Job이 만든다(소유 aops 서비스 주체, 읽는 사람 없음). 코드는 UC 모델 버전도 만든다. 노코드는 deploy가 성공할 때 자동 등록한다."),
)
def register(
    project_id: str, request: Request,
    archive: UploadFile | None = File(None, description="버전 zip (aops.toml, SPEC.md, agent/…)"),
    files: list[UploadFile] | None = File(None, description="zip 대신 폴더의 파일들 (filename = 폴더 안 상대 경로). 서버가 `aops register`와 같은 규칙으로 묶는다"),
    version: str | None = Form(None, description="`--version`. 비우면 register가 매긴다 (최초 1.0.0)"),
    bump: Literal["fix", "minor", "major"] | None = Form(None, description="버전을 올리는 폭. 비우면 minor"),
) -> AgentVersionDetail:
    svc = service_for("aops register")
    if archive is not None:
        data = archive.file.read(MAX_UPLOAD + 1)
    elif files:
        data = folder_zip([(f.filename or "", f.file.read(MAX_UPLOAD + 1)) for f in files])
    if (archive is not None or files) and len(data) > MAX_UPLOAD:
        raise ServiceError(413, "too_large", f"버전은 {MAX_UPLOAD // 2**20}MB까지입니다 (데이터는 UC 테이블·볼륨에 두세요)", "aops register")
    else:
        raise ServiceError(400, "no_files", "버전 zip(archive)이나 폴더의 파일(files)을 보내세요", "aops register")
    row = svc.register(caller_of(request, svc), project_id, data, version, bump)
    skills = [AgentVersionSkill(**s) for s in row.pop("skills", [])]
    return AgentVersionDetail(**{**row, "created_at": row["registered_at"], "spec": loads(row.get("spec")) or {}}, skills=skills)


@router.post(
    "/projects/{project_id}/deploy-requests", status_code=201, summary="운영 반영 요청 (Gate → 승인 → 배포 Job)",
    **command("aops deploy", DBX, note="Gate(검증·평가 결과)를 확인하고 승인을 받으면 배포 Job이 운영 버전을 내보낸다: 노코드 운영용 Genie Agent 복제, 코드 운영 앱 배포, 스킬 레지스트리 상태 운영과 UC Skill 내용. 노코드는 `version` 없이 요청하고 Gate가 개발용 정의를 스냅샷으로 뜬다. 같은 에이전트에 진행 중인 요청이 있으면 409. 되돌리기는 이전 버전을 deploy한다. `dev=true`면 승인 없이 개인 시험 배포를 시작하고 배포를 돌려준다."),
)
def deploy(project_id: str, body: DeployRequest, request: Request) -> ReleaseGate | Deployment:
    svc = service_for("aops deploy")
    if body.dev:
        raise todo("aops deploy --dev")
    return _gate(svc.request_deploy(caller_of(request, svc), project_id, body.version, body.reason))


@router.post(
    "/agents/{agent_id}/promote", summary="대상자 권한 부여",
    **command("aops promote", DBX, note=(
        "노코드는 deploy 때 자동으로 공개 그룹에 CAN RUN, 코드는 운영 앱 CAN USE, 스킬은 UC Skill READ_VOLUME(있어야 load 가능). "
        "요청자는 담당자·담당 팀, 운영 버전이 있어야 하고, 대상과 보안 등급별 공개 범위를 서버가 검사한다. "
        "권한의 정본은 플랫폼 권한(UC grants 등)이고, 이력은 agent_events에 남는다."
    )),
)
def promote(agent_id: str, body: PromoteRequest, request: Request) -> list[Grant]:
    from datetime import datetime, timezone
    svc = service_for("aops promote")
    at = datetime.now(timezone.utc)
    return [Grant(**g, at=at) for g in svc.promote(caller_of(request, svc), agent_id, body.principals, body.revoke)]


@router.get(
    "/release-gates", summary="배포 심사 목록",
    **command("aops gates", DBX, screen="console 배포 요청·승인",
              note="`to_approve`: 이 사람이 승인할 차례인 요청(본인 요청 제외, 승인 그룹일 때). `mine`: 내가 낸 요청. 최신 100개"),
)
def list_gates(
    request: Request, decision: GateDecision | None = None, mine: bool = False, to_approve: bool = False,
    agent_id: str | None = None, cursor: str | None = None,
) -> Page[ReleaseGate]:
    svc = service_for("aops gates")
    rows = svc.gates(caller_of(request, svc), decision, mine, to_approve, agent_id)
    return Page[ReleaseGate](items=[_gate(r) for r in rows], next_cursor=None)


@router.get("/release-gates/{gate_id}", summary="배포 심사 보기", **command(None, DBX, screen="console 배포 요청·승인"))
def get_gate(gate_id: str, request: Request) -> ReleaseGateDetail:
    svc = service_for(None)
    caller_of(request, svc)
    gate = svc.advance(gate_id)
    validation = svc.validation(gate["validation_run_id"]) if gate.get("validation_run_id") else None
    return ReleaseGateDetail(**_gate(gate).model_dump(), validation=ValidationRun(**validation) if validation else None)


@router.post(
    "/release-gates/{gate_id}/approve", summary="승인",
    **command("aops approve", DBX, screen="console [승인]", note="승인 그룹만, 요청자 본인은 불가. 필요한 승인 수가 차면 배포 Job이 시작된다."),
)
def approve(gate_id: str, body: DecisionBody, request: Request) -> ReleaseGate:
    svc = service_for("aops approve")
    return _gate(svc.approve(caller_of(request, svc), gate_id, body.comment))


@router.post(
    "/release-gates/{gate_id}/reject", summary="반려",
    **command("aops reject", DBX, screen="console [반려]", note="승인과 같은 규칙(승인 그룹, 요청자 본인 불가). 요청자는 취소를 쓴다."),
)
def reject(gate_id: str, body: RejectBody, request: Request) -> ReleaseGate:
    svc = service_for("aops reject")
    return _gate(svc.reject(caller_of(request, svc), gate_id, body.reason))


@router.post(
    "/release-gates/{gate_id}/cancel", summary="요청 취소 (요청자)",
    **command("aops deploy --cancel", DBX, screen="console 배포 요청", note="요청자나 담당자·담당 팀이 검사 중·승인 대기인 요청을 취소한다."),
)
def cancel_gate(gate_id: str, request: Request) -> ReleaseGate:
    svc = service_for("aops deploy --cancel")
    return _gate(svc.cancel(caller_of(request, svc), gate_id))


@router.post(
    "/approvals/external", summary="외부 결재 결과 받기 (확장용)",
    **command(None, DBX, note="정책의 결재 채널이 `external`일 때 외부 결재 확장이 부른다. `X-Aops-Signature`로 서명을 검사한다."),
)
def external_decision(body: ExternalDecision, x_aops_signature: str = Header()) -> Approval:
    raise todo(None)


MAX_UPLOAD = 100 * 2**20


def folder_zip(files: list[tuple[str, bytes]]) -> bytes:
    """A browser folder upload as the version zip: one top folder is dropped, `aops register` rules apply."""
    paths = [name.replace("\\", "/").lstrip("/") for name, _ in files]
    tops = {p.split("/", 1)[0] for p in paths if "/" in p}
    strip = len(tops) == 1 and all("/" in p for p in paths)
    entries = []
    for path, (_, data) in zip(paths, files):
        rel = path.split("/", 1)[1] if strip else path
        if package.included(rel):
            entries.append((rel, data, 0o644))
    return package.build_entries(entries).data


def _gate(row: dict) -> ReleaseGate:
    return ReleaseGate(**{**row, "required_approvals": int(row.get("required_approvals") or 1),
                          "approvals": int(row.get("approvals") or 0)})
