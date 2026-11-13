"""Long-term memory (`aops memory`). The Gate evaluation (`aops evaluate`) is in lifecycle."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from ..common import ALL, Page, command, todo

router = APIRouter(tags=["메모리"])


class MemoryItem(BaseModel):
    memory_id: str = Field(examples=["m-17"])
    status: Literal["proposed", "accepted"]
    text: str = Field(examples=["보고서는 항상 표 먼저, 설명은 세 줄 이하"])
    source_session: str | None = None
    created_at: datetime


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
