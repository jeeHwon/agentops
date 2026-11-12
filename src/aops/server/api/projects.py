"""Projects: `aops init`, `aops add`·`remove`, `aops configure`, and the `aops.toml` switches."""

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..common import ALL, DBX, Page, command, todo

router = APIRouter(tags=["프로젝트"])

AgentType = Literal["skill", "code", "no_code"]


class ProjectCreate(BaseModel):
    name: str = Field(examples=["price-helper"], description="프로젝트·에이전트 이름")
    type: AgentType = Field("skill", description="`--type`: skill(누구나) · code(AI 개발자) · no_code(Genie)")
    harness: str = Field("claude", examples=["claude", "codex"], description="`--harness` 기본 하네스")
    edition: Literal["local", "dbx", "omni", "custom"] = "dbx"
    genie_space_id: str | None = Field(None, description="`--genie`: no_code일 때 가져올 Genie Agent. 없으면 새로 만든다")
    location: str | None = Field(
        None,
        examples=["/Workspace/Users/me@example.com/aops/price-helper"],
        description="Workspace 폴더(Git 폴더)나 UC Volume 경로. custom에서는 Sandbox가 여기서 가져간다",
    )


class ProjectSummary(BaseModel):
    project_id: str = Field(examples=["price-helper"])
    name: str = Field(examples=["price-helper"])
    type: AgentType
    edition: str = Field(examples=["dbx"])
    harness: str = Field(examples=["claude"])
    location: str
    agent_id: str | None = Field(None, examples=["price-helper"], description="레지스트리의 에이전트 ID (SPEC.md)")


class Project(ProjectSummary):
    model: str | None = Field(None, examples=["databricks-claude-sonnet-4-6"])
    release_version: str | None = Field(None, examples=["1.2.0"], description="`[release] version`")
    skills: list[str] = Field(default_factory=list, examples=[["pine-sql-style"]])
    tools: list[str] = Field(default_factory=list, examples=[["system.ai.dbsql"]])


class ProjectConfig(BaseModel):
    """`aops.toml` as JSON. Sections follow the file: agent, guardrails, session, memory, variants, release, dbx."""

    config_version: int = 1
    agent: dict[str, Any] = Field(examples=[{"harness": "claude", "model": "databricks-claude-sonnet-4-6"}])
    guardrails: dict[str, Any] = Field(default_factory=dict, examples=[{"instructions": True, "rules": "log"}])
    session: dict[str, Any] = Field(default_factory=dict, examples=[{"handoff_tokens": {"codex": 2000, "claude": 4000}}])
    memory: dict[str, Any] = Field(default_factory=dict)
    variants: list[dict[str, Any]] = Field(default_factory=list, examples=[[{"name": "codex-guard-off", "harness": "codex", "rules": "off"}]])
    release: dict[str, Any] = Field(default_factory=dict, examples=[{"spec": "SPEC.md", "evals": "evals/datasets/main"}])
    dbx: dict[str, Any] = Field(default_factory=dict)


class ConfigPatch(BaseModel):
    """Values to change. Only given keys change; the server writes `aops.toml` and regenerates harness files."""

    changes: dict[str, Any] = Field(examples=[{"guardrails.rules": "enforce", "agent.harness": "codex"}])


ComponentKind = Literal["skill", "tool", "mcp"]


class Component(BaseModel):
    kind: ComponentKind
    name: str = Field(examples=["pine-sql-style"])
    source: str = Field(examples=["agent/skills/pine-sql-style", "main.team_skills.sql-guide", "system.ai.dbsql"])
    publication_mode: Literal["agent", "shared_reference"] | None = Field(
        None, description="스킬만: agent(전용, 운영 지정 때 게시) · shared_reference(공용 스킬 참조)"
    )


class ComponentAdd(BaseModel):
    kind: ComponentKind
    target: str = Field(
        examples=["./skills/pine-sql-style", "main.team_skills.sql-guide", "system.ai.dbsql"],
        description="스킬 폴더, 공용 스킬 UC 이름, MCP·UC function·Genie 이름",
    )


class ConfigureRequest(BaseModel):
    edition: Literal["dbx", "omni", "custom"] = "dbx"
    dry_run: bool = Field(True, description="`--dry-run`: 확인과 이전 계획만 보여준다")


class Check(BaseModel):
    name: str = Field(examples=["Unity Gateway Skills 프리뷰"])
    status: Literal["ok", "warn", "fail", "off"]
    detail: str | None = None
    fix: str | None = Field(None, examples=["aops doctor --fix sync"])


class ConfigureResult(BaseModel):
    checks: list[Check]
    providers: dict[str, str] = Field(examples=[{"trace": "dbx", "memory": "local"}], description="제안하는 `[providers]`")
    migration: list[str] = Field(default_factory=list, examples=[["세션 3개 → Agent sessions", "평가 기록 → Databricks Experiment"]])


class GenieSpace(BaseModel):
    space_id: str = Field(examples=["01f0a1b2c3d4"])
    title: str = Field(examples=["매출 분석"])
    owner: str | None = None
    can_edit: bool = Field(description="가져오려면 CAN EDIT가 필요하다")


@router.get("/projects", summary="프로젝트 목록", **command(None, ALL, screen="프로젝트 선택"))
def list_projects(cursor: str | None = None, limit: int = Query(50, le=200)) -> Page[ProjectSummary]:
    raise todo(None)


_TEMPLATE = command("aops init", ALL, note="`aops init <이름> --edition dbx`와 같은 폴더를 zip으로 내준다. 받은 폴더를 PC나 워크스페이스에 풀고 스킬·SPEC을 고친 뒤 등록한다. 서버 주소가 aops.toml에 들어 있다. 지금은 스킬 기반만")
_TEMPLATE["responses"] = {200: {"content": {"application/zip": {}}, "description": "새 에이전트 폴더 zip"}, **_TEMPLATE["responses"]}


@router.post("/projects/template", summary="새 에이전트 폴더 받기 (zip)", response_class=Response, **_TEMPLATE)
def project_template(body: ProjectCreate, request: Request) -> Response:
    import io
    import tempfile
    import zipfile
    from pathlib import Path

    from ... import init
    from ...dbx.service import ServiceError

    if body.type not in ("skill", "code"):
        raise ServiceError(501, "not_implemented", "지금은 스킬 기반·코드 기반 에이전트를 만듭니다 (노코드는 준비 중)", "aops init")
    host = request.headers.get("x-forwarded-host")
    server = f"https://{host}" if host else str(request.base_url).rstrip("/")
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            if body.type == "code":
                from ...dbx import code

                project = init.create_code(Path(tmp), body.name, agentbricks=str(code.agentbricks_cli()))
            else:
                project = init.create(Path(tmp), body.name, harness=body.harness, edition="dbx")
        except ValueError as error:
            raise ServiceError(400, "bad_name", "이름은 영문 소문자·숫자·하이픈 63자 이하입니다", "aops init") from error
        toml = project.root / "aops.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace('server = ""', f'server = "{server}"', 1), encoding="utf-8")
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(project.root.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{body.name}/{path.relative_to(project.root).as_posix()}")
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{body.name}.zip"'})


@router.post("/projects", status_code=201, summary="프로젝트 만들기", **command("aops init", ALL))
def create_project(body: ProjectCreate) -> Project:
    raise todo("aops init")


@router.get("/projects/{project_id}", summary="프로젝트 보기", **command(None, ALL))
def get_project(project_id: str) -> Project:
    raise todo(None)


@router.get("/projects/{project_id}/config", summary="설정 읽기 (aops.toml)", **command(None, ALL, screen="개발 화면 설정"))
def get_config(project_id: str) -> ProjectConfig:
    raise todo(None)


@router.patch(
    "/projects/{project_id}/config",
    summary="설정 바꾸기 (가드레일·하네스·모델 스위치)",
    **command(None, ALL, screen="개발 화면 스위치", note="`aops.toml`을 고치면 하네스 설정 파일을 다시 만든다."),
)
def patch_config(project_id: str, body: ConfigPatch) -> ProjectConfig:
    raise todo(None)


@router.get("/projects/{project_id}/components", summary="스킬·도구·MCP 목록", **command(None, ALL))
def list_components(project_id: str, kind: ComponentKind | None = None) -> list[Component]:
    raise todo(None)


@router.post("/projects/{project_id}/components", status_code=201, summary="스킬·도구·MCP 추가", **command("aops add", ALL))
def add_component(project_id: str, body: ComponentAdd) -> Component:
    raise todo("aops add")


@router.delete(
    "/projects/{project_id}/components/{kind}/{name}", status_code=204, summary="스킬·도구·MCP 제거",
    response_class=Response, **command("aops remove", ALL),
)
def remove_component(project_id: str, kind: ComponentKind, name: str) -> None:
    raise todo("aops remove")


@router.post("/projects/{project_id}/configure", summary="Databricks 연결 확인·에디션 이전", **command("aops configure", DBX))
def configure(project_id: str, body: ConfigureRequest) -> ConfigureResult:
    raise todo("aops configure")


@router.get(
    "/genie/spaces", summary="Genie Agent 목록 (노코드 프로젝트용)",
    **command("aops init --type no-code", DBX, screen="노코드 프로젝트 만들기"),
)
def list_genie_spaces(q: str | None = None, cursor: str | None = None) -> Page[GenieSpace]:
    raise todo("aops init --type no-code")
