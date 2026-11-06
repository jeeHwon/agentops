"""The CLI ↔ API map, read from the routes themselves.

`COMMANDS` lists every CLI command of the dbx edition. A command either has routes (their
`x-aops-command` names it) or says why it stays CLI-only. Tests check both directions, so a new
command or route cannot be added without updating this list.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI


@dataclass(frozen=True)
class Command:
    group: str
    name: str
    cli_only: str | None = None


COMMANDS: list[Command] = [
    Command("개발", "aops init"),
    Command("개발", "aops init --type no-code"),
    Command("개발", "aops dev", cli_only="내 PC에 개발 화면을 여는 명령이다. 그 화면이 이 API를 쓴다"),
    Command("개발", 'aops run "<질문>"'),
    Command("개발", 'aops run "<질문>" --json'),
    Command("개발", "aops run <질문 파일>"),
    Command("개발", "aops evaluate"),
    Command("개발", "aops session list"),
    Command("개발", "aops session new"),
    Command("개발", "aops session show"),
    Command("개발", "aops session switch --harness"),
    Command("개발", "aops session import"),
    Command("개발", "aops session export"),
    Command("개발", "aops memory list|search"),
    Command("개발", "aops memory accept"),
    Command("개발", "aops memory forget"),
    Command("개발", "aops add"),
    Command("개발", "aops remove"),
    Command("등록·배포", "aops configure"),
    Command("등록·배포", "aops validate"),
    Command("등록·배포", "aops register"),
    Command("등록·배포", "aops deploy"),
    Command("등록·배포", "aops promote"),
    Command("운영·관리", "aops monitor"),
    Command("운영·관리", "aops monitor dashboard"),
    Command("운영·관리", "aops monitor --web", cli_only="console 화면을 브라우저로 여는 일이다"),
    Command("운영·관리", "aops doctor"),
    Command("운영·관리", "aops doctor --fix"),
    Command("운영·관리", "aops harness list"),
    Command("운영·관리", "aops harness use"),
    Command("운영·관리", "aops harness install", cli_only="실행 파일을 그 PC에 설치하는 일이다. custom에서는 Sandbox 이미지로 관리한다"),
    Command("운영·관리", "aops harness upgrade", cli_only="실행 파일을 그 PC에서 올리는 일이다. custom에서는 Sandbox 이미지로 관리한다"),
    Command("운영·관리", "aops admin setup"),
    Command("운영·관리", "aops admin setup --apply"),
    Command("운영·관리", "aops admin policy show"),
    Command("운영·관리", "aops admin policy set"),
    Command("운영·관리", "aops admin policy diff"),
    Command("운영·관리", "aops serve", cli_only="이 API 서버를 띄우는 명령이다"),
]


def routes(app: FastAPI) -> list[dict[str, Any]]:
    """Every API operation with its CLI command, screen and editions, read from the OpenAPI document."""
    out = []
    for path, item in app.openapi()["paths"].items():
        for method, op in item.items():
            out.append({
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary"),
                "tag": (op.get("tags") or [None])[0],
                "command": op.get("x-aops-command"),
                "screen": op.get("x-aops-screen"),
                "editions": op.get("x-aops-editions", []),
            })
    return out


def command_map(app: FastAPI) -> list[dict[str, Any]]:
    """One row per CLI command: its routes, or why it has none."""
    by_command: dict[str, list[str]] = {}
    for row in routes(app):
        if row["command"]:
            by_command.setdefault(row["command"], []).append(f"{row['method']} {row['path']}")
    return [
        {"group": c.group, "command": c.name, "routes": by_command.get(c.name, []), "cli_only": c.cli_only}
        for c in COMMANDS
    ]


def markdown(app: FastAPI) -> str:
    """`docs/api.md`: conventions plus the command ↔ API tables, generated from the routes."""
    lines = [
        "# aops API",
        "",
        "[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)",
        "",
        "`aops serve`가 여는 HTTP API입니다. Custom Front(custom), Omnigent UI, 개발 화면, console 화면이 모두 이 API를 씁니다. "
        "dbx 에디션의 명령과 1:1로 맞습니다. 서버를 띄우면 `/api/v1/docs`에서 요청·응답 형식을 보고 바로 호출해 볼 수 있습니다.",
        "",
        "> 이 문서는 `scripts/api_docs.py`가 코드에서 만듭니다. 손으로 고치지 마세요.",
        "",
        "## 규칙",
        "",
        "- **경로:** 모두 `/api/v1` 아래입니다. OpenAPI 문서는 `/api/v1/openapi.json`입니다.",
        "- **인증:** Databricks OAuth 토큰을 `Authorization: Bearer`로 보냅니다. 사람은 U2M, 서비스는 서비스 주체 M2M입니다.",
        "- **오류:** 2xx가 아니면 본문은 `Problem`(RFC 9457)입니다. 화면은 `code`로 분기하고, `command`는 같은 일을 하는 CLI 명령입니다.",
        "- **목록:** `Page`로 옵니다. `next_cursor`를 다음 요청의 `cursor`로 넘깁니다.",
        "- **오래 걸리는 일:** 검증·비교 실행·평가는 `202`와 상태를 돌려주고, `…/events`(SSE)로 진행을 흘립니다.",
        "- **실행 스트림:** 턴 실행 스트림은 AG-UI 이벤트입니다(`threadId`=세션, `runId`=턴). aops만의 정보는 `CUSTOM` 이벤트(`aops.handoff`, `aops.skill`, `aops.guardrail`, `aops.usage`)로 옵니다.",
        "- **값:** 상태·종류 같은 열거형은 영문 코드입니다. 한글 표시는 화면이 붙입니다.",
        "- **지금 상태:** 요청·응답 형식만 정해져 있고, 기능은 아직 `501`을 돌려줍니다.",
        "",
        "## 명령 ↔ API",
        "",
    ]
    rows = command_map(app)
    for group in dict.fromkeys(r["group"] for r in rows):
        lines += [f"### {group}", "", "| 명령 | API |", "| --- | --- |"]
        for r in (r for r in rows if r["group"] == group):
            api = "<br>".join(f"`{x}`" for x in r["routes"]) if r["routes"] else f"없음: {r['cli_only']}"
            name = r["command"].replace("|", "\\|")
            lines.append(f"| `{name}` | {api} |")
        lines.append("")
    screen_only = [r for r in routes(app) if not r["command"]]
    lines += ["## 화면·API 전용", "", "명령은 없고 화면에서 쓰는 API입니다.", "", "| API | 하는 일 | 화면 |", "| --- | --- | --- |"]
    for r in screen_only:
        lines.append(f"| `{r['method']} {r['path']}` | {r['summary']} | {r['screen'] or ''} |")
    lines.append("")
    return "\n".join(lines)
