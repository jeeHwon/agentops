"""Administration: `aops admin setup`, `aops admin policy`, rate cards.

The first `admin setup` runs from the CLI because this server does not exist yet; afterwards an
admin screen can preview and re-apply it here. Only the admin group may call these routes.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...dbx.service import ServiceError
from ..common import DBX, Page, command, todo
from ..dbx import caller_of, service_for

router = APIRouter(prefix="/admin", tags=["관리자"])


class SetupItem(BaseModel):
    resource: str = Field(examples=["schema main.aops", "job aops-validate", "app aops-server"])
    action: Literal["create", "update", "none"]
    detail: str | None = None


class SetupPlan(BaseModel):
    items: list[SetupItem]
    grants: list[str] = Field(default_factory=list, examples=[["GRANT SELECT ON SCHEMA main.aops TO `agent-developers`"]])


class SetupApply(BaseModel):
    api_callers: list[str] = Field(default_factory=list, examples=[["group:portal-users", "sp:portal-backend"]], description="custom: Custom Front가 쓸 그룹·서비스 주체. aops 서버 앱의 `CAN USE`를 준다")


class Policy(BaseModel):
    """Admin policy. Defaults apply when a key is missing."""

    validation_mode: dict[str, Any] = Field(default_factory=lambda: {"default": "mandatory"}, examples=[{"default": "mandatory", "rules": [{"security_level": "general", "upgrade_type": "patch", "mode": "optional"}]}])
    approval_mode: dict[str, Any] = Field(default_factory=lambda: {"default": "required"}, examples=[{"default": "required"}])
    approval_channel: Literal["console", "external"] = "console"
    approvers: str = Field(examples=["group:agent-approvers"])
    required_approvals: int = 1
    security_levels: list[dict[str, str]] = Field(
        default_factory=lambda: [
            {"code": "general", "label": "일반"}, {"code": "important", "label": "중요"}, {"code": "secret", "label": "기밀"},
        ]
    )
    no_source_when: dict[str, Any] = Field(default_factory=lambda: {"security_level_at_least": "important", "data_sensitivity": ["confidential"]})
    min_scores: dict[str, float] = Field(default_factory=dict, examples=[{"correctness": 0.8, "safety": 1.0}])
    allowed_models: list[str] = Field(default_factory=list)
    required_guardrails: dict[str, list[str]] = Field(default_factory=dict, examples=[{"confidential": ["kr-pii", "jailbreak"]}])


class PolicyVersion(BaseModel):
    policy_version: int = Field(examples=[4])
    status: Literal["active", "retired"] = "active"
    created_by: str
    created_at: datetime
    policy: dict[str, Any] = Field(description="지금 쓰는 정책 본문: validation_mode, approval_mode, approver_group, git 등")


class PolicyChange(BaseModel):
    """What the console changes (the same keys as `aops admin setup --only policy`). Other keys (Git) stay."""

    validation_mode: Literal["mandatory", "optional", "exempt"] | None = None
    approval_mode: Literal["required", "optional", "auto"] | None = None
    approver_group: str | None = None


class PolicySet(BaseModel):
    policy: Policy | None = Field(None, description="구조로 주거나")
    toml: str | None = Field(None, description="`policy.toml` 원문으로 준다")


class PolicyDiff(BaseModel):
    from_version: int
    to_version: int
    changes: list[dict[str, Any]] = Field(examples=[[{"key": "min_scores.correctness", "from": 0.75, "to": 0.8}]])


class RateCard(BaseModel):
    model: str = Field(examples=["databricks-claude-sonnet-4-6"])
    input_usd_per_mtok: float = Field(examples=[3.0])
    output_usd_per_mtok: float = Field(examples=[15.0])
    cached_input_usd_per_mtok: float | None = None
    valid_from: datetime
    valid_to: datetime | None = None


@router.get("/setup", summary="조직 자원 준비 계획 (미리 보기)", **command("aops admin setup", DBX))
def setup_plan() -> SetupPlan:
    raise todo("aops admin setup")


@router.post("/setup", summary="조직 자원 준비 적용", **command("aops admin setup --apply", DBX))
def setup_apply(body: SetupApply) -> SetupPlan:
    raise todo("aops admin setup --apply")


@router.get("/policies", summary="정책 버전 목록", **command("aops admin policy show", DBX))
def list_policies(request: Request, cursor: str | None = None) -> Page[PolicyVersion]:
    svc = service_for("aops admin policy show")
    caller_of(request, svc)
    return Page(items=[_policy(r) for r in svc.policies()])


@router.get("/policies/current", summary="현재 정책", **command("aops admin policy show", DBX))
def current_policy(request: Request) -> PolicyVersion:
    svc = service_for("aops admin policy show")
    caller_of(request, svc)
    rows = [r for r in svc.policies() if r["status"] == "active"]
    if not rows:
        raise ServiceError(404, "no_policy", "적용된 정책이 없습니다", "aops admin setup --only policy")
    return _policy(rows[0])


@router.post("/policies", status_code=201, summary="정책 올리기", **command("aops admin policy set", DBX))
def set_policy(body: PolicyChange, request: Request) -> PolicyVersion:
    svc = service_for("aops admin policy set")
    return _policy(svc.set_policy(caller_of(request, svc), body.model_dump(exclude_none=True)))


def _policy(row: dict) -> PolicyVersion:
    return PolicyVersion(**{**row, "policy": row["body"]})


@router.get("/policies/diff", summary="정책 버전 비교", **command("aops admin policy diff", DBX))
def diff_policy(from_version: int, to_version: int | None = None) -> PolicyDiff:
    raise todo("aops admin policy diff")


@router.get("/rate-cards", summary="모델 단가표", **command(None, DBX, screen="console 관리자 · 단가표"))
def list_rate_cards() -> list[RateCard]:
    raise todo(None)


@router.put("/rate-cards", summary="모델 단가표 바꾸기", **command(None, DBX, screen="console 관리자 · 단가표"))
def put_rate_cards(body: list[RateCard]) -> list[RateCard]:
    raise todo(None)
