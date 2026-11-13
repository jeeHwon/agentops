"""Work in the requester's own workspace folder, with their own token (Databricks Apps user authorization).

- create: a new skill or code agent's template goes into `/Workspace/Users/<me>/aops/<name>` (or a folder the
  requester names), so they edit it in the workspace with Genie Code. The agent row is made at once, so the
  name is taken and the agent page can link the folder before the first version.
- register: the server reads that folder with the requester's token and packs it with the same rules as
  `aops register` (harness settings and run records left out).

The server never reads or writes a person's folder as itself: what the requester cannot see, aops cannot take.
"""

from __future__ import annotations

import base64
import urllib.parse
from typing import Any

from .. import package
from .base import AGENT_ID, Caller, ServiceError

WORKSPACE_SCOPE = "workspace.workspace"
MAX_FILES = 2000
MAX_BYTES = 100 * 2**20
FOLDERS = ("DIRECTORY", "REPO")


def entry_file(kind: str, files: dict[str, bytes]) -> str:
    """The file to open first: the agent code for a code agent, the first skill for a skill agent."""
    if kind == "code" and "agent/agent.py" in files:
        return "agent/agent.py"
    skills = sorted(rel for rel in files if rel.startswith("agent/skills/") and rel.endswith("/SKILL.md"))
    return skills[0] if skills else "SPEC.md"


def check_location(path: str) -> str:
    path = path.rstrip("/")
    parts = path.split("/")
    if not path.startswith("/Workspace/") or any(p in ("", ".", "..") for p in parts[1:]):
        raise ServiceError(400, "bad_location", "위치는 /Workspace/로 시작하는 워크스페이스 폴더입니다 (예: /Workspace/Users/<나>/aops/<이름>)", "aops init")
    return path


class UserSpaceMixin:
    """Service methods that use the requester's token on the workspace (scope `workspace.workspace`)."""

    w: Any
    sql: Any

    def _user_result(self, result: dict[str, Any], what: str, scope: str, command: str, code: str = "workspace_failed") -> dict[str, Any]:
        if result["status"] == 200:
            return result["body"] or {}
        message = result.get("error") or ""
        if result["status"] in (401, 403) and "scope" in message.lower():
            raise ServiceError(403, "missing_scope", f"내 토큰에 {scope} 범위가 없어 내 권한으로 {what}를 할 수 없습니다. "
                               "앱에 그 범위가 없으면 관리자가 aops admin setup --only app을 다시 실행합니다. 앱에는 있는데 이 메시지가 나오면 "
                               "범위를 더하기 전에 열어 둔 화면입니다: 시크릿 창으로 열거나 이 앱 주소의 쿠키를 지운 뒤 다시 열어 동의하세요. "
                               f"응답: {message[:200]}", "aops admin setup --only app")
        raise ServiceError(result["status"] if 400 <= result["status"] < 500 else 502, code, f"{what}에 실패했습니다: {message}", command)

    def _user_api(self, caller: Caller, method: str, path: str, body: dict[str, Any] | None, what: str,
                  scope: str = WORKSPACE_SCOPE, command: str = "aops init", code: str = "workspace_failed") -> dict[str, Any]:
        if not caller.token:
            raise ServiceError(401, "no_user_token", "내 권한으로 워크스페이스를 다룰 토큰이 없습니다 (Databricks App으로 열거나 CLI 로그인)", command)
        return self._user_result(self._as_user(caller.token, method, path, body), what, scope, command, code)

    def _status(self, caller: Caller, path: str) -> dict[str, Any] | None:
        result = self._as_user(caller.token, "GET", "/api/2.0/workspace/get-status?" + urllib.parse.urlencode({"path": path}))
        if result["status"] == 404 or "RESOURCE_DOES_NOT_EXIST" in (result.get("error") or "") or "does not exist" in (result.get("error") or ""):
            return None
        return self._user_result(result, "폴더 확인", WORKSPACE_SCOPE, "aops init")

    # ── create ──────────────────────────────────────────────────────────────

    def create_in_workspace(self, caller: Caller, kind: str, name: str, files: dict[str, bytes], spec: dict[str, Any],
                            location: str | None = None) -> dict[str, Any]:
        """The template into the requester's folder (never over an existing one), then the agent row."""
        if not AGENT_ID.match(name):
            raise ServiceError(400, "bad_agent_id", "에이전트 이름은 영문 소문자·숫자·하이픈 63자 이하입니다 (첫 글자는 영문 소문자·숫자)", "aops init")
        if self.agent(name):
            raise ServiceError(409, "exists", f"이미 있는 에이전트입니다: {name}", "aops init")
        if not caller.token:
            raise ServiceError(401, "no_user_token", "내 권한으로 워크스페이스를 다룰 토큰이 없습니다", "aops init")
        target = check_location(location or f"/Workspace/Users/{caller.email}/aops/{name}")
        if self._status(caller, target):
            raise ServiceError(409, "folder_exists", f"이미 있는 폴더입니다: {target}. 다른 위치를 적거나 그 폴더를 지운 뒤 다시 하세요", "aops init")
        kept = {rel: data for rel, data in files.items() if package.included(rel)}
        dirs = sorted({rel.rsplit("/", 1)[0] for rel in kept if "/" in rel})
        self._user_api(caller, "POST", "/api/2.0/workspace/mkdirs", {"path": target}, "폴더 만들기")
        for rel in dirs:
            self._user_api(caller, "POST", "/api/2.0/workspace/mkdirs", {"path": f"{target}/{rel}"}, "폴더 만들기")
        for rel, data in sorted(kept.items()):
            self._user_api(caller, "POST", "/api/2.0/workspace/import",
                           {"path": f"{target}/{rel}", "format": "AUTO", "overwrite": False, "content": base64.b64encode(data).decode()},
                           f"{rel} 올리기")
        links = self.workspace_links(caller, target, kept)
        self.sql.atomic([
            (f"INSERT INTO {self.sql.table('agents')} VALUES (:id, :name, :desc, :type, :owner, :group, :crit, :sens, :sec, "
             "'registered', parse_json(:spec), :by, current_timestamp(), current_timestamp())",
             {"id": name, "name": spec.get("name") or name, "desc": spec.get("description"), "type": kind, "owner": caller.email,
              "group": spec.get("owner_group") or None, "crit": spec.get("service_criticality") or "medium",
              "sens": spec.get("data_sensitivity") or "internal", "sec": spec.get("security_level") or "general",
              "spec": spec, "by": caller.email}),
            self._event_statement(name, None, "create", caller.email, {**links, "build_type": kind, "files": len(kept)}),
        ])
        return {"agent_id": name, "type": kind, "files": sorted(kept), **links}

    def workspace_links(self, caller: Caller, root: str, files: dict[str, bytes]) -> dict[str, Any]:
        """The folder and its first file in the workspace UI (the editor opens with the Genie Code panel)."""
        root = check_location(root)
        spec = package.front_matter(files.get("SPEC.md", b"").decode("utf-8", "replace"))
        entry = entry_file(str(spec.get("build_type") or "skill").replace("-", "_"), files)
        host = self.w.config.host.rstrip("/")
        folder = (self._status(caller, root) or {}).get("object_id")
        opened = (self._status(caller, f"{root}/{entry}") or {}).get("object_id") if entry in files else None
        return {"workspace_path": root, "folder_url": f"{host}/browse/folders/{folder}" if folder else None,
                "open_url": f"{host}/editor/files/{opened}" if opened else None, "open_file": entry if opened else None}

    # ── register from a workspace folder ────────────────────────────────────

    def read_workspace_folder(self, caller: Caller, path: str) -> tuple[list[tuple[str, bytes]], list[str]]:
        """(relative path, content) of the files `aops register` would take, and what was left out (notebooks)."""
        root = check_location(path)
        if not self._status(caller, root):
            raise ServiceError(404, "no_folder", f"폴더가 없거나 내게 읽을 권한이 없습니다: {root}", "aops register")
        bare = lambda p: p[len("/Workspace"):] if p.startswith("/Workspace/") else p  # noqa: E731
        files: list[tuple[str, bytes]] = []
        skipped: list[str] = []
        total = 0
        pending = [root]
        while pending:
            current = pending.pop()
            listing = self._user_api(caller, "GET", "/api/2.0/workspace/list?" + urllib.parse.urlencode({"path": current}), None,
                                     "폴더 읽기", command="aops register")
            for item in listing.get("objects") or []:
                rel = bare(item["path"])[len(bare(root)) + 1:]
                kind = item.get("object_type")
                if kind in FOLDERS:
                    if package.included(f"{rel}/x"):
                        pending.append(item["path"])
                    continue
                if kind != "FILE":
                    skipped.append(f"{rel} ({kind.lower() if kind else '?'})")
                    continue
                if not package.included(rel):
                    continue
                exported = self._user_api(caller, "GET", "/api/2.0/workspace/export?" + urllib.parse.urlencode({"path": item["path"], "format": "AUTO"}),
                                          None, f"{rel} 읽기", command="aops register")
                data = base64.b64decode(exported.get("content") or "")
                total += len(data)
                files.append((rel, data))
                if len(files) > MAX_FILES or total > MAX_BYTES:
                    raise ServiceError(413, "too_large", f"파일 {MAX_FILES}개·{MAX_BYTES // 2**20}MB까지입니다 (데이터는 UC 테이블·볼륨에 두세요)", "aops register")
        return sorted(files), sorted(skipped)

    def agent_workspace(self, agent_id: str) -> dict[str, Any] | None:
        """Where the agent is edited in the workspace: the last create or register that came from a workspace folder."""
        from .sql import loads

        row = self.sql.one(f"SELECT detail FROM {self.sql.table('agent_events')} WHERE agent_id = :a AND event_type IN ('create', 'register') "
                           "AND detail:workspace_path IS NOT NULL ORDER BY at DESC LIMIT 1", {"a": agent_id})
        detail = loads(row.get("detail")) if row else None
        return {k: detail.get(k) for k in ("workspace_path", "folder_url", "open_url", "open_file")} if detail else None
