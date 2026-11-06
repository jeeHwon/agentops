"""Shared pieces of the aops API: errors, pages, usage, and the CLI mapping on each route."""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from fastapi import Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field

T = TypeVar("T")

Edition = Literal["local", "dbx", "omni", "custom"]
ALL: list[Edition] = ["local", "dbx", "omni", "custom"]
DBX: list[Edition] = ["dbx", "omni", "custom"]

OperationStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]

bearer = HTTPBearer(auto_error=False, scheme_name="DatabricksOAuth", description="Databricks OAuth 토큰 (U2M·M2M)")


class Problem(BaseModel):
    """Error body for every non-2xx response (RFC 9457 problem details)."""

    type: str = Field("about:blank", examples=["https://aops.dev/problems/not-implemented"])
    title: str = Field(examples=["아직 구현되지 않았습니다"])
    status: int = Field(examples=[501])
    detail: str | None = Field(None, examples=["`aops validate`에 해당하는 API는 아직 구현 전입니다."])
    code: str = Field(examples=["not_implemented"], description="화면이 분기할 때 쓰는 고정 코드")
    command: str | None = Field(None, examples=["aops validate"], description="같은 일을 하는 CLI 명령")


class Page(BaseModel, Generic[T]):
    """One page of a list. Pass `next_cursor` back as `cursor` to read the next page."""

    items: list[T]
    next_cursor: str | None = Field(None, examples=[None])


class Usage(BaseModel):
    """Token usage of one turn or one run."""

    input_tokens: int = Field(0, examples=[1820])
    output_tokens: int = Field(0, examples=[412])
    cached_input_tokens: int = Field(0, examples=[1024])
    reasoning_output_tokens: int = Field(0, examples=[96])
    total_tokens: int = Field(0, examples=[2232])


class Link(BaseModel):
    """A page in Databricks the UI can open (MLflow run, Job run, app)."""

    label: str = Field(examples=["MLflow run"])
    url: str = Field(examples=["https://<workspace>/ml/experiments/123/runs/abc"])


class Timestamps(BaseModel):
    created_at: datetime
    updated_at: datetime | None = None


class NotImplementedYet(Exception):
    """Raised by route stubs. The app turns it into a 501 `Problem`."""

    def __init__(self, command: str | None) -> None:
        super().__init__(command)
        self.command = command


PROBLEMS: dict[int | str, dict[str, Any]] = {
    status: {"model": Problem, "description": text}
    for status, text in {
        400: "요청 형식이 틀림",
        401: "Databricks OAuth 토큰이 없거나 만료됨",
        403: "권한 없음 (예: 승인 그룹이 아님, 자기 요청 승인)",
        404: "대상이 없음",
        409: "현재 상태에서 할 수 없음 (예: 이미 등록된 버전, 정책 버전이 바뀜)",
        501: "아직 구현되지 않음",
    }.items()
}


def command(
    cli: str | None, editions: list[Edition], *, screen: str | None = None, note: str | None = None
) -> dict[str, Any]:
    """Route options that record which CLI command (or screen) the route stands for.

    The values land in OpenAPI as `x-aops-command`, `x-aops-editions` and `x-aops-screen`, and
    `aops.server.mapping` reads them back to build the command ↔ API table.
    """
    extra: dict[str, Any] = {"x-aops-command": cli, "x-aops-editions": editions}
    if screen:
        extra["x-aops-screen"] = screen
    lines = [f"**CLI:** `{cli}`" if cli else "**CLI:** 없음 (API·화면 전용)"]
    if screen:
        lines.append(f"**화면:** {screen}")
    lines.append("**에디션:** " + " · ".join(editions))
    if note:
        lines.append(note)
    return {"openapi_extra": extra, "responses": dict(PROBLEMS), "description": "\n\n".join(lines)}


def todo(cli: str | None) -> NotImplementedYet:
    return NotImplementedYet(cli)


def pending(cli: str | None) -> Any:
    """Dependency for stream stubs: raises before the stream starts, so the client gets a 501."""

    def check() -> None:
        raise NotImplementedYet(cli)

    return Depends(check)
