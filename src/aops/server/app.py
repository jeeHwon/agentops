"""Build the aops API app (`aops serve`)."""

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException

from .. import __version__
from .api import admin, batches, evaluations, lifecycle, meta, monitor, ops, projects, registry, sessions
from .common import NotImplementedYet, Problem, bearer

PREFIX = "/api/v1"

DESCRIPTION = """\
aops의 dbx 기능을 HTTP로 엽니다. Custom Front(custom), Omnigent UI, 로컬 개발 화면, console 화면이 모두 이 API를 씁니다.
각 요청 설명의 **CLI**는 같은 일을 하는 명령입니다. 전체 대응표는 `GET /api/v1/meta/commands`에 있습니다.

- **인증:** Databricks OAuth 토큰을 `Authorization: Bearer`로 보냅니다 (사람 U2M, 서비스 M2M).
- **오류:** 2xx가 아니면 `Problem`(RFC 9457). 화면은 `code`로 분기합니다.
- **목록:** `Page`. `next_cursor`를 다음 요청의 `cursor`로 넘깁니다.
- **오래 걸리는 일:** 검증·비교 실행·평가는 `202` + 상태, 진행은 `…/events`(SSE).
- **실행 스트림:** AG-UI 이벤트(`threadId`=세션, `runId`=턴). aops 정보는 `CUSTOM` 이벤트.
- **지금 상태:** 형식만 정해졌고 기능은 `501`을 돌려줍니다.
"""

TAGS = [
    {"name": "프로젝트", "description": "`aops init` · `add`·`remove` · `configure`, 설정 스위치"},
    {"name": "세션·실행", "description": '`aops run "<질문>"` · `aops session`: 하네스를 바꿔도 이어지는 세션'},
    {"name": "비교 실행", "description": "`aops run <질문 파일>`: 질문 × 구성 × 반복"},
    {"name": "평가·메모리", "description": "`aops evaluate` · `aops memory`"},
    {"name": "검증·등록·배포", "description": "`aops validate` · `register` · `deploy` · `promote`, 심사·승인"},
    {"name": "레지스트리", "description": "에이전트·버전·검증·배포·운영 기록 (console 레지스트리 화면)"},
    {"name": "모니터링", "description": "`aops monitor`: 사용량·예산·비용·운영 중인 에이전트"},
    {"name": "점검·하네스", "description": "`aops doctor` · `aops harness`"},
    {"name": "관리자", "description": "`aops admin setup` · `aops admin policy`, 단가표"},
    {"name": "서버", "description": "상태·버전·내 계정·명령 대응표"},
]

def problem(status: int, code: str, title: str, detail: str | None = None, command: str | None = None) -> JSONResponse:
    body = Problem(title=title, status=status, code=code, detail=detail, command=command)
    return JSONResponse(body.model_dump(), status_code=status, media_type="application/problem+json")


def create_app(*, edition: str = "custom", cors_origins: list[str] | None = None) -> FastAPI:
    app = FastAPI(
        title="aops API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=f"{PREFIX}/redoc",
    )
    app.state.edition = edition
    secured = [Depends(bearer)]
    for module in (projects, sessions, batches, evaluations, lifecycle, registry, monitor, ops, admin):
        app.include_router(module.router, prefix=PREFIX, dependencies=secured)
    app.include_router(meta.router, prefix=PREFIX)

    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(NotImplementedYet)
    async def not_implemented(_: Request, exc: NotImplementedYet) -> JSONResponse:
        what = f"`{exc.command}`에 해당하는 API" if exc.command else "이 API"
        return problem(501, "not_implemented", "아직 구현되지 않았습니다", f"{what}는 형식만 정해졌습니다.", exc.command)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return problem(exc.status_code, f"http_{exc.status_code}", str(exc.detail))

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(f"{PREFIX}/docs")

    return app
