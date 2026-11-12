"""Projects: `aops init`, `aops add`·`remove`, `aops configure`, and the `aops.toml` switches."""

from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..common import ALL, DBX, Link, Page, command, todo

router = APIRouter(tags=["프로젝트"])

AgentType = Literal["skill", "code", "no_code"]


class ProjectCreate(BaseModel):
    name: str = Field(examples=["price-helper"], description="프로젝트·에이전트 이름")
    type: AgentType = Field("skill", description="`--type`: skill(누구나) · code(AI 개발자) · no_code(Genie)")
    harness: str = Field("claude", examples=["claude", "codex"], description="`--harness` 기본 하네스")
    edition: Literal["local", "dbx", "omni", "custom"] = "dbx"
    genie_space_id: str | None = Field(None, description="`--genie`: no_code일 때 가져올 Genie Agent. 없으면 새로 만든다")
    genie_mode: Literal["new", "copy", "move"] | None = Field(
        None, description="no_code: new(테이블로 새로 만들기) · copy(genie_space_id를 에이전트 폴더로 복사) · move(genie_space_id를 에이전트 폴더로 옮기기). "
                          "비우면 genie_space_id가 있을 때 copy, 없으면 new")
    tables: list[str] = Field(default_factory=list, examples=[["main.sales.orders"]], description="no_code new: 데이터 소스 <catalog>.<schema>.<table> (1~30개)")
    warehouse_id: str | None = Field(None, description="no_code: Genie가 쓸 SQL warehouse. 비우면 복사 원본이나 aops 서버의 warehouse")
    display_name: str | None = Field(None, examples=["매출 도우미"], description="SPEC name (표시 이름). 비우면 name")
    description: str | None = Field(None, examples=["지역별 매출과 할인 효과를 답한다"], description="SPEC description (이 에이전트가 하는 일)")
    owner_group: str | None = Field(None, description="SPEC owner_group (담당 팀, 계정 그룹)")
    service_criticality: Literal["low", "medium", "high"] = "medium"
    data_sensitivity: Literal["public", "internal", "confidential", "personal"] = "internal"
    security_level: str = Field("general", description="관리자 정책의 보안 등급 코드")
    consumer_group: str | None = Field(None, description="no_code: 공개 그룹 (배포 때 운영 Genie에 CAN RUN)")
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

    from ..dbx import caller_of, service_for

    try:
        svc = service_for("aops init")
    except Exception:
        svc = None  # no registry configured (API contract only): the template needs nothing from it
    owner = caller_of(request, svc).email if svc is not None else ""
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
        if body.description:  # the SPEC fields from the screen become SPEC.md
            spec = {"agent_id": body.name, "name": body.display_name or body.name, "build_type": body.type, "owner": owner,
                    "owner_group": body.owner_group, "service_criticality": body.service_criticality,
                    "data_sensitivity": body.data_sensitivity, "security_level": body.security_level, "description": body.description}
            (project.root / "SPEC.md").write_text(init.render_spec(spec), encoding="utf-8")
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(project.root.rglob("*")):
                if path.is_file():
                    zf.write(path, f"{body.name}/{path.relative_to(project.root).as_posix()}")
    return Response(buffer.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{body.name}.zip"'})


class ProjectCreated(BaseModel):
    agent_id: str = Field(examples=["sales-genie"])
    type: AgentType
    location: str = Field(examples=["/Workspace/aops/no-code/sales-genie"], description="에이전트 폴더 (SPEC.md가 있는 곳)")
    dev_space_id: str | None = Field(None, description="no_code: 개발용 Genie 스페이스")
    genie_mode: str | None = None
    warehouse_id: str | None = None
    links: list[Link] = Field(default_factory=list, description="Genie에서 열기, 폴더 열기")
    warnings: list[str] = Field(default_factory=list)


@router.post(
    "/projects", status_code=201, summary="새 에이전트 만들기 (노코드)",
    **command("aops init", ALL, note=(
        "no_code: aops 서비스 주체가 에이전트 폴더(/Workspace/aops/no-code/<이름>)를 만들고 요청자(와 담당 팀)에게 CAN MANAGE를 준 뒤 "
        "SPEC.md를 쓴다. 개발용 Genie는 요청자의 토큰(앱의 genie 사용자 권한 범위)으로 그 폴더에 만든다: 테이블로 새로(new), "
        "기존 스페이스 복사(copy), 기존 스페이스 옮기기(move). 서비스 주체는 폴더에서 권한을 받아 정의를 읽는다. "
        "레지스트리에 agents·genie_agents 행과 create 기록을 남긴다. 버전은 첫 배포가 성공할 때 생긴다."
    )),
)
def create_project(body: ProjectCreate, request: Request) -> ProjectCreated:
    from ...dbx.service import ServiceError

    from ..dbx import caller_of, service_for

    svc = service_for("aops init")
    caller = caller_of(request, svc)
    if body.type != "no_code":
        raise ServiceError(501, "not_implemented", "스킬 기반·코드 기반은 지금 폴더 받기(POST /projects/template)로 만듭니다", "aops init")
    row = svc.create_no_code(caller, body.model_dump())
    links = [Link(label="Genie에서 열기 (개발용)", url=row["dev_url"])] if row.get("dev_url") else []
    if row.get("folder_url"):
        links.append(Link(label="에이전트 폴더", url=row["folder_url"]))
    return ProjectCreated(agent_id=row["agent_id"], type="no_code", location=row["folder_path"], dev_space_id=row["dev_space_id"],
                          genie_mode=row["genie_mode"], warehouse_id=row["warehouse_id"], links=links, warnings=row["warnings"])


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
