"""Checks and harnesses: `aops doctor`, `aops harness list|use`.

Installing or upgrading a harness binary (`aops harness install|upgrade`) changes the machine
that runs it, so it stays a CLI command. In custom, harnesses run in Databricks Sandbox images.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..common import ALL, command, todo
from .projects import Check

router = APIRouter(tags=["점검·하네스"])


class DoctorReport(BaseModel):
    project_id: str | None = None
    checks: list[Check]


class FixRequest(BaseModel):
    item: Literal["sync", "migrate"] | str = Field(examples=["sync"], description="`--fix <항목>`")
    project_id: str | None = None


class HarnessInfo(BaseModel):
    name: str = Field(examples=["codex"])
    version: str | None = Field(None, examples=["0.166.0"])
    tested: bool = Field(description="호환표에 있는 버전인지")
    available: bool = Field(description="이 서버(또는 Sandbox 이미지)에서 실행할 수 있는지")


class HarnessUse(BaseModel):
    name: str = Field(examples=["codex"])
    version: str | None = Field(None, examples=["0.166.0"])
    untested: bool = Field(False, description="`--untested`: 호환표에 없는 버전도 경고와 함께")


@router.get("/doctor", summary="설치·설정 점검", **command("aops doctor", ALL))
def doctor(project_id: str | None = None) -> DoctorReport:
    raise todo("aops doctor")


@router.post("/doctor/fix", summary="고른 항목만 고치기", **command("aops doctor --fix", ALL))
def doctor_fix(body: FixRequest) -> DoctorReport:
    raise todo("aops doctor --fix")


@router.get("/harnesses", summary="하네스 목록·호환", **command("aops harness list", ALL))
def list_harnesses() -> list[HarnessInfo]:
    raise todo("aops harness list")


@router.put("/projects/{project_id}/harness", summary="프로젝트 하네스·버전 고르기", **command("aops harness use", ALL))
def use_harness(project_id: str, body: HarnessUse) -> HarnessInfo:
    raise todo("aops harness use")
