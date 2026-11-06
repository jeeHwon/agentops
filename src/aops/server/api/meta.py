"""Server information: health, version, the signed-in user, and the CLI ↔ API map."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ... import __version__
from ..common import ALL, bearer, command, todo
from ..mapping import command_map

router = APIRouter(prefix="/meta", tags=["서버"])


class Health(BaseModel):
    status: Literal["ok"] = "ok"


class Version(BaseModel):
    aops: str = Field(examples=["0.1.0.dev0"])
    api: str = Field("v1", examples=["v1"])
    edition: str = Field(examples=["custom"])


class Me(BaseModel):
    user: str = Field(examples=["dev@example.com"], description="토큰의 Databricks 계정")
    groups: list[str] = Field(examples=[["agent-developers"]])
    roles: list[Literal["developer", "approver", "admin"]] = Field(examples=[["developer"]])


class CommandRow(BaseModel):
    group: str = Field(examples=["등록·배포"])
    command: str = Field(examples=["aops validate"])
    routes: list[str] = Field(examples=[["POST /api/v1/projects/{project_id}/validations"]])
    cli_only: str | None = Field(None, description="API가 없는 이유 (CLI 전용일 때)")


@router.get("/health", summary="서버 상태")
def health() -> Health:
    return Health()


@router.get("/version", summary="버전")
def version(request: Request) -> Version:
    return Version(aops=__version__, edition=request.app.state.edition)


@router.get("/me", summary="내 계정·역할", dependencies=[Depends(bearer)], **command(None, ALL, screen="모든 화면 (권한별 버튼 표시)"))
def me() -> Me:
    raise todo(None)


@router.get("/commands", summary="명령 ↔ API 대응표")
def commands(request: Request) -> list[CommandRow]:
    return [CommandRow(**row) for row in command_map(request.app)]
