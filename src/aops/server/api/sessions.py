"""Sessions and turns: `aops run "<질문>"` and `aops session`.

A session is aops's own record. It is split into segments, one per harness session; switching
harness starts a new segment and hands the unseen turns over (README "세션 이어가기").
"""

from collections.abc import AsyncIterable
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from ..common import ALL, Page, Usage, command, pending, todo
from ..events import TurnEvent

router = APIRouter(tags=["세션·실행"])


class SessionSummary(BaseModel):
    session_id: str = Field(examples=["9d321c9c133d484897ce14f979375f2f"])
    project_id: str = Field(examples=["price-helper"])
    next_harness: str = Field(examples=["codex"], description="다음 턴을 돌릴 하네스")
    turns: int = Field(examples=[3])
    created_at: datetime
    last_turn_at: datetime | None = None


class ToolUse(BaseModel):
    kind: Literal["tool", "skill", "mcp", "subagent"]
    name: str = Field(examples=["shell", "pine-sql-style", "databricks.sql"])
    ok: bool | None = None
    detection: Literal["explicit", "inferred"] = "explicit"
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Turn(BaseModel):
    turn_id: int = Field(examples=[4])
    segment_id: int = Field(examples=[2])
    prompt: str = Field(examples=["내 이름이 뭐였지?"])
    response: str = Field(examples=["한별이라고 하셨어요."])
    tools: list[ToolUse] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    handoff_turns: int = Field(0, description="이 턴 앞에 넘겨받은 턴 수")
    started_at: datetime
    ended_at: datetime | None = None


class Segment(BaseModel):
    segment_id: int = Field(examples=[2])
    harness: str = Field(examples=["codex"])
    harness_version: str = Field(examples=["0.166.0"])
    native_session: str | None = Field(None, description="하네스 고유 세션 ID (같은 하네스로 돌아오면 재개)")
    started_at: datetime
    turns: list[Turn] = Field(default_factory=list)


class Session(SessionSummary):
    segments: list[Segment] = Field(default_factory=list)


class SessionCreate(BaseModel):
    harness: str | None = Field(None, examples=["claude"], description="비우면 프로젝트 기본 하네스")


class SwitchRequest(BaseModel):
    harness: str = Field(examples=["codex"])


class TurnRequest(BaseModel):
    prompt: str = Field(examples=["내 이름이 뭐였지?"])
    harness: str | None = Field(None, description="`--harness`: 이 턴부터 쓸 하네스")


class TurnResult(BaseModel):
    """Same fields as `aops run --json`."""

    session: str = Field(examples=["9d321c9c133d484897ce14f979375f2f"])
    segment: int = Field(examples=[2])
    turn: int = Field(examples=[4])
    harness: str = Field(examples=["codex"])
    harness_version: str = Field(examples=["0.166.0"])
    handoff_turns: int = Field(examples=[2])
    response: str = Field(examples=["한별이라고 하셨어요."])
    tools: list[str] = Field(examples=[["shell", "skill:pine-sql-style"]])
    usage: Usage


class Handoff(BaseModel):
    after_turn: int = Field(examples=[3])
    to_harness: str = Field(examples=["codex"])
    turns: int = Field(examples=[2])
    file: str = Field(examples=[".aops/handoff/9d32-codex-after-3.md"])
    method: Literal["file_notice", "inject_items"]
    created_at: datetime


class SessionImport(BaseModel):
    harness: str = Field(examples=["claude"])
    native_session: str = Field(examples=["5f0c8e2a-…"], description="터미널에서 쓰던 하네스 세션 ID")


class SessionExport(BaseModel):
    format: Literal["aops-session/v1"] = "aops-session/v1"
    session: Session
    events_url: str | None = Field(None, description="원본 이벤트(JSONL) 내려받기 주소")


@router.get("/projects/{project_id}/sessions", summary="세션 목록", **command("aops session list", ALL))
def list_sessions(project_id: str, cursor: str | None = None) -> Page[SessionSummary]:
    raise todo("aops session list")


@router.post("/projects/{project_id}/sessions", status_code=201, summary="새 세션", **command("aops session new", ALL))
def create_session(project_id: str, body: SessionCreate) -> Session:
    raise todo("aops session new")


@router.get("/sessions/{session_id}", summary="세션 보기 (구간·턴)", **command("aops session show", ALL))
def get_session(session_id: str) -> Session:
    raise todo("aops session show")


@router.post(
    "/sessions/{session_id}/switch", summary="하네스 전환 (다음 턴부터)",
    **command("aops session switch --harness", ALL),
)
def switch_harness(session_id: str, body: SwitchRequest) -> SessionSummary:
    raise todo("aops session switch --harness")


@router.post(
    "/sessions/{session_id}/turns", summary="질문 하나 실행 (끝까지 기다림)",
    **command('aops run "<질문>" --json', ALL),
)
def run_turn(session_id: str, body: TurnRequest) -> TurnResult:
    raise todo('aops run "<질문>" --json')


@router.post(
    "/sessions/{session_id}/turns/stream", response_class=EventSourceResponse, dependencies=[pending('aops run "<질문>"')],
    summary="질문 하나 실행 (SSE, AG-UI 이벤트)",
    **command('aops run "<질문>"', ALL, screen="개발 화면 Chat",
              note="응답은 `text/event-stream`. 이벤트 형식은 AG-UI이고, `threadId`=세션, `runId`=턴이다."),
)
async def stream_turn(session_id: str, body: TurnRequest) -> AsyncIterable[TurnEvent]:
    raise todo('aops run "<질문>"')
    yield  # pragma: no cover - marks this route as a stream


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/cancel", status_code=202, summary="실행 중인 턴 멈추기",
    **command(None, ALL, screen="개발 화면 Chat [멈춤]", note="CLI에서는 Ctrl+C."),
)
def cancel_turn(session_id: str, turn_id: int) -> TurnResult:
    raise todo(None)


@router.get(
    "/sessions/{session_id}/turns/{turn_id}/tools", summary="턴의 도구·스킬 사용",
    **command(None, ALL, screen="개발 화면 사용 칩"),
)
def list_turn_tools(session_id: str, turn_id: int) -> list[ToolUse]:
    raise todo(None)


@router.get("/sessions/{session_id}/handoffs", summary="인계 기록", **command(None, ALL, screen="개발 화면 세션 탭"))
def list_handoffs(session_id: str) -> list[Handoff]:
    raise todo(None)


@router.post(
    "/projects/{project_id}/sessions/import", status_code=201, summary="터미널 세션 가져오기",
    **command("aops session import", ALL),
)
def import_session(project_id: str, body: SessionImport) -> Session:
    raise todo("aops session import")


@router.get("/sessions/{session_id}/export", summary="세션 내보내기", **command("aops session export", ALL))
def export_session(session_id: str) -> SessionExport:
    raise todo("aops session export")
