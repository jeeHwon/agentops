"""Evaluation: `aops evaluate` (MLflow scoring, red-team and handoff suites) and `aops memory`."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from ..common import ALL, Link, OperationStatus, Page, command, todo

router = APIRouter(tags=["평가·메모리"])


class EvaluationCreate(BaseModel):
    dataset: str | None = Field(None, examples=["evals/datasets/main"], description="비우면 `[release] evals`")
    suite: Literal["redteam", "handoff"] | None = Field(None, description="`--suite`: 가드레일 레드팀, 이어가기 품질")
    scorers: list[str] = Field(default_factory=list, examples=[["correctness", "relevance", "safety"]])
    variants: list[str] = Field(default_factory=list, examples=[["claude-guard-on", "codex-guard-off"]])
    target: Literal["project", "deployed"] = Field("project", description="`--target deployed`: 배포된 에이전트를 채점")


class Score(BaseModel):
    variant: str = Field(examples=["claude-guard-on"])
    scorer: str = Field(examples=["correctness"])
    value: float = Field(examples=[0.86])
    passed: bool | None = Field(None, description="정책 최소 점수와 비교한 결과 (있을 때)")


class Failure(BaseModel):
    variant: str
    question_id: str = Field(examples=["q5"])
    scorer: str = Field(examples=["safety"])
    rationale: str = Field(examples=["주민등록번호를 가리지 않고 답했다"])


class EvaluationSummary(BaseModel):
    evaluation_id: str = Field(examples=["e-20261106-1010"])
    status: OperationStatus
    dataset: str
    suite: str | None = None
    created_at: datetime


class Evaluation(EvaluationSummary):
    scores: list[Score] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list, description="MLflow 실행 등")


class MemoryItem(BaseModel):
    memory_id: str = Field(examples=["m-17"])
    status: Literal["proposed", "accepted"]
    text: str = Field(examples=["보고서는 항상 표 먼저, 설명은 세 줄 이하"])
    source_session: str | None = None
    created_at: datetime


@router.post("/projects/{project_id}/evaluations", status_code=202, summary="평가 시작", **command("aops evaluate", ALL))
def create_evaluation(project_id: str, body: EvaluationCreate) -> Evaluation:
    raise todo("aops evaluate")


@router.get("/projects/{project_id}/evaluations", summary="평가 목록", **command(None, ALL, screen="개발 화면 Evaluate"))
def list_evaluations(project_id: str, cursor: str | None = None) -> Page[EvaluationSummary]:
    raise todo(None)


@router.get("/evaluations/{evaluation_id}", summary="평가 결과 (점수표·실패 사례)", **command(None, ALL, screen="개발 화면 Evaluate"))
def get_evaluation(evaluation_id: str) -> Evaluation:
    raise todo(None)


@router.get("/projects/{project_id}/memory", summary="장기 메모리 보기·검색", **command("aops memory list|search", ALL))
def list_memory(project_id: str, status: Literal["proposed", "accepted"] | None = None, q: str | None = None) -> Page[MemoryItem]:
    raise todo("aops memory list|search")


@router.post("/projects/{project_id}/memory/{memory_id}/accept", summary="메모리 제안 승인", **command("aops memory accept", ALL))
def accept_memory(project_id: str, memory_id: str) -> MemoryItem:
    raise todo("aops memory accept")


@router.delete(
    "/projects/{project_id}/memory/{memory_id}", status_code=204, response_class=Response,
    summary="메모리 지우기", **command("aops memory forget", ALL),
)
def forget_memory(project_id: str, memory_id: str) -> None:
    raise todo("aops memory forget")
