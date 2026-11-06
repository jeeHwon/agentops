"""Batch comparison: `aops run <질문 파일> --variants --repeat --concurrency`.

A batch runs every question × variant × repeat as a cell and compares answers, tool and skill
use, guardrail decisions, tokens and latency side by side.
"""

from collections.abc import AsyncIterable
from datetime import datetime

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from ..common import ALL, OperationStatus, Page, Usage, command, pending, todo
from ..events import ProgressEvent

router = APIRouter(tags=["비교 실행"])


class Question(BaseModel):
    id: str = Field(examples=["q3"])
    prompt: str = Field(examples=["지난달 매출 상위 5개 지역은?"])
    expected: str | None = None


class BatchCreate(BaseModel):
    questions: list[Question] | None = Field(None, description="질문을 바로 넣거나")
    questions_file: str | None = Field(None, examples=["evals/questions.yaml"], description="프로젝트 안 질문 파일")
    variants: list[str] = Field(default_factory=list, examples=[["claude-guard-on", "codex-guard-off"]], description="`[[variants]]` 이름. 비우면 기본 구성 하나")
    repeat: int = Field(1, ge=1, le=20)
    concurrency: int = Field(2, ge=1, le=8)


class Cell(BaseModel):
    question_id: str = Field(examples=["q3"])
    variant: str = Field(examples=["codex-guard-off"])
    repeat: int = Field(examples=[1])
    status: OperationStatus
    response: str | None = None
    tools: list[str] = Field(default_factory=list, examples=[["shell", "skill:pine-sql-style"]])
    guardrail_blocks: int = 0
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int | None = Field(None, examples=[8400])


class BatchSummary(BaseModel):
    batch_id: str = Field(examples=["b-20261106-0912"])
    status: OperationStatus
    questions: int = Field(examples=[8])
    variants: list[str]
    repeat: int
    done: int = Field(examples=[7])
    total: int = Field(examples=[16])
    created_at: datetime


class Batch(BatchSummary):
    cells: list[Cell] = Field(default_factory=list)


class UsageRate(BaseModel):
    """Batch heatmap: how many runs of a question used a skill or tool (`used / repeat`)."""

    question_id: str
    variant: str
    name: str = Field(examples=["skill:pine-sql-style"])
    used: int = Field(examples=[2])
    runs: int = Field(examples=[3])


@router.post("/projects/{project_id}/batches", status_code=202, summary="비교 실행 시작", **command("aops run <질문 파일>", ALL))
def create_batch(project_id: str, body: BatchCreate) -> Batch:
    raise todo("aops run <질문 파일>")


@router.get("/projects/{project_id}/batches", summary="비교 실행 목록", **command(None, ALL, screen="개발 화면 Batch"))
def list_batches(project_id: str, cursor: str | None = None) -> Page[BatchSummary]:
    raise todo(None)


@router.get("/batches/{batch_id}", summary="비교 결과 (칸별)", **command(None, ALL, screen="개발 화면 Batch"))
def get_batch(batch_id: str) -> Batch:
    raise todo(None)


@router.get(
    "/batches/{batch_id}/events", response_class=EventSourceResponse, dependencies=[pending(None)], summary="비교 진행 (SSE)",
    **command(None, ALL, screen="개발 화면 Batch 진행 표시"),
)
async def stream_batch(batch_id: str) -> AsyncIterable[ProgressEvent]:
    raise todo(None)
    yield  # pragma: no cover


@router.post("/batches/{batch_id}/cancel", summary="비교 실행 멈추기", **command(None, ALL, screen="개발 화면 Batch [멈춤]"))
def cancel_batch(batch_id: str) -> BatchSummary:
    raise todo(None)


@router.get("/batches/{batch_id}/usage", summary="질문별 스킬·도구 사용률", **command(None, ALL, screen="개발 화면 Batch 히트맵"))
def batch_usage(batch_id: str) -> list[UsageRate]:
    raise todo(None)
