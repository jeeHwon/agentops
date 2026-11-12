"""The dbx lifecycle for skill agents: register, validate, deploy, promote, list and load.

The aops server calls these with the requester it identified; the functions act with the server's
own identity (the aops service principal in a Databricks App). validate and deploy do their work in
Lakeflow Jobs that run `aops-job`, as the same service principal.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import package
from ..init import SKILL_DESCRIPTION, SPEC_DESCRIPTION, TEMPLATE_SKILL
from ..hashing import skills_sha256
from .git import GitHub, GitSettings, commit_message, proxy_transport
from .sql import Sql, loads

DEFAULT_POLICY = {
    "validation_mode": "mandatory",
    "approval_mode": "required",
    "approval_channel": "console",
    "approver_group": "",
    "required_approvals": 1,
    "security_levels": ["general", "important", "secret"],
}
CRITICALITY = {"low", "medium", "high"}
SENSITIVITY = {"public", "internal", "confidential", "personal"}
# Agent Skills name rule: 1-64 lowercase letters, digits and hyphens; no hyphen at either end, no "--".
SKILL_NAME = re.compile(r"^(?!.*--)[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SECRET_PATTERNS = {
    "Databricks 토큰": re.compile(r"dapi[0-9a-f]{32}"),
    "GitHub 토큰": re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
    "AWS 키": re.compile(r"AKIA[0-9A-Z]{16}"),
    "개인 키": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|id_rsa)$")
# What each validate step checks, shown in the Job description and in every validate run's output.
CHECK_RULES = {
    "format": "SPEC.md에 agent_id·name·build_type이 있고, service_criticality ∈ {low, medium, high}, "
              "data_sensitivity ∈ {public, internal, confidential, personal}, description이 init 템플릿 문구가 아니다",
    "skills": "agent/skills/<이름>/SKILL.md가 하나 이상. 이름은 소문자·숫자·하이픈 64자 이하(하이픈으로 시작·끝·연속 불가), "
              "name = 폴더 이름, description 1~1024자 (Agent Skills 형식). init 템플릿(my-skill, 템플릿 description) 그대로면 반려",
    "secrets": "모든 파일에서 " + "·".join(SECRET_PATTERNS) + " 패턴, 금지 파일(.env, *.pem, id_rsa)",
    "record": "볼륨의 버전 zip이 등록 때 해시와 같다",
}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, command: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.command = status, code, message, command


@dataclass(frozen=True)
class Config:
    registry: str  # <catalog>.<schema>
    warehouse_id: str
    dist_wheel: str | None = None  # /Volumes/.../aops_cli-<version>-py3-none-any.whl for the Jobs
    profile: str | None = None  # only when the server runs outside Databricks Apps

    @classmethod
    def from_env(cls) -> Config | None:
        registry, warehouse = os.environ.get("AOPS_REGISTRY"), os.environ.get("AOPS_WAREHOUSE_ID")
        if not registry or not warehouse:
            return None
        return cls(registry, warehouse, os.environ.get("AOPS_DIST_WHEEL"), os.environ.get("AOPS_PROFILE"))

    @property
    def catalog(self) -> str:
        return self.registry.split(".")[0]

    @property
    def schema(self) -> str:
        return self.registry.split(".")[1]


@dataclass
class Caller:
    email: str
    token: str | None = None
    groups: list[str] = field(default_factory=list)


def now_id() -> str:
    return uuid.uuid4().hex


class Service:
    def __init__(self, config: Config, workspace: Any = None):
        from databricks.sdk import WorkspaceClient

        self.config = config
        self.w = workspace or (WorkspaceClient(profile=config.profile) if config.profile else WorkspaceClient())
        self.sql = Sql(self.w, config.warehouse_id, config.registry)
        self._jobs: dict[str, int] = {}

    # ── identity ────────────────────────────────────────────────────────────

    def identify(self, token: str | None, email: str | None) -> Caller:
        """The requester: the forwarded user from Databricks Apps, or the owner of a bearer token."""
        groups: list[str] = []
        if token:
            me = self._as_user(token, "GET", "/api/2.0/preview/scim/v2/Me")
            if me.get("status") == 200:
                body = me["body"]
                email = email or body.get("userName")
                groups = [g.get("display") for g in body.get("groups", []) if g.get("display")]
        if not email:
            raise ServiceError(401, "unauthenticated", "요청한 사람을 확인할 수 없습니다")
        return Caller(email=email, token=token, groups=groups)

    def _as_user(self, token: str, method: str, path: str) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        host = self.w.config.host.rstrip("/")
        req = urllib.request.Request(host + path, method=method, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                try:
                    return {"status": resp.status, "body": json.loads(data)}
                except ValueError:
                    return {"status": resp.status, "body": data}
        except urllib.error.HTTPError as error:
            return {"status": error.code, "body": None}

    def _is_owner(self, caller: Caller, agent: dict[str, Any]) -> bool:
        return caller.email == agent["owner"] or bool(agent.get("owner_group") and agent["owner_group"] in caller.groups)

    # ── registry reads ──────────────────────────────────────────────────────

    def policy(self) -> tuple[int, dict[str, Any]]:
        row = self.sql.one(f"SELECT policy_version, body FROM {self.sql.table('policies')} WHERE status = 'active' "
                           "ORDER BY policy_version DESC LIMIT 1")
        if not row:
            return 0, dict(DEFAULT_POLICY)
        return int(row["policy_version"]), {**DEFAULT_POLICY, **loads(row["body"])}

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        return self.sql.one(f"SELECT * FROM {self.sql.table('agents')} WHERE agent_id = :id", {"id": agent_id})

    def versions(self, agent_id: str) -> list[dict[str, Any]]:
        return self.sql.run(f"SELECT * FROM {self.sql.table('agent_versions')} WHERE agent_id = :id ORDER BY registered_at",
                            {"id": agent_id})

    def version(self, agent_id: str, version: str) -> dict[str, Any] | None:
        return self.sql.one(f"SELECT * FROM {self.sql.table('agent_versions')} WHERE agent_id = :id AND version = :v",
                            {"id": agent_id, "v": version})

    def version_skills(self, agent_id: str, version: str) -> list[dict[str, Any]]:
        return self.sql.run(f"SELECT * FROM {self.sql.table('agent_version_skills')} WHERE agent_id = :id AND version = :v",
                            {"id": agent_id, "v": version})

    def production(self, agent_id: str) -> dict[str, Any] | None:
        return self.sql.one(f"SELECT * FROM {self.sql.table('agent_production')} WHERE agent_id = :id", {"id": agent_id})

    def list_agents(self, build_type: str | None = None) -> list[dict[str, Any]]:
        where, params = ("WHERE build_type = :t", {"t": build_type}) if build_type else ("", {})
        return self.sql.run(f"SELECT * FROM {self.sql.table('agent_status')} {where} ORDER BY agent_id", params)

    def events(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.sql.run(f"SELECT * FROM {self.sql.table('agent_events')} WHERE agent_id = :id ORDER BY at DESC LIMIT {int(limit)}",
                            {"id": agent_id})
        for row in rows:
            row["detail"] = loads(row.get("detail")) or {}
        return rows

    def agent_status(self, agent_id: str) -> dict[str, Any]:
        row = self.sql.one(f"SELECT * FROM {self.sql.table('agent_status')} WHERE agent_id = :id", {"id": agent_id})
        if not row:
            raise ServiceError(404, "not_found", f"등록된 에이전트가 없습니다: {agent_id}", "aops list")
        return row

    def version_rows(self, agent_id: str) -> list[dict[str, Any]]:
        """Versions, newest first, with their Git copy when there is one (for the registry screen)."""
        self._agent_or_404(agent_id)
        try:
            git = self.git_records(agent_id)
        except Exception:  # no agent_version_git table yet
            git = {}
        rows = list(reversed(self.versions(agent_id)))
        for row in rows:
            record = git.get(row["version"])
            row["git_commit"] = (record or {}).get("commit_sha")
            row["git_url"] = (record or {}).get("url")
            row["git_status"] = (record or {}).get("status")
            row["created_at"] = row["registered_at"]
        return rows

    def version_detail(self, agent_id: str, version: str) -> dict[str, Any]:
        row = next((r for r in self.version_rows(agent_id) if r["version"] == version), None)
        if not row:
            raise ServiceError(404, "not_found", f"{agent_id}에 {version} 버전이 없습니다")
        record = None
        try:
            record = self.git_records(agent_id).get(version)
        except Exception:
            pass
        return {**row, "spec": loads(row.get("spec")) or {}, "skills": self.version_skills(agent_id, version), "git": record}

    def validation_runs(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.sql.run(f"SELECT * FROM {self.sql.table('validation_runs')} WHERE agent_id = :a "
                            f"ORDER BY started_at DESC LIMIT {int(limit)}", {"a": agent_id})
        for row in rows:
            row["steps"] = loads(row.get("steps")) or []
            row["policy_version"] = int(row["policy_version"])
        return rows

    def deployment_rows(self, agent_id: str | None = None, deployment_id: str | None = None) -> list[dict[str, Any]]:
        where, params = ("deployment_id = :d", {"d": deployment_id}) if deployment_id else ("agent_id = :a", {"a": agent_id})
        rows = self.sql.run(f"SELECT * FROM {self.sql.table('deployments')} WHERE {where} ORDER BY started_at DESC LIMIT 50", params)
        for row in rows:
            row["detail"] = loads(row.get("detail")) or {}
        return rows

    def policies(self) -> list[dict[str, Any]]:
        rows = self.sql.run(f"SELECT * FROM {self.sql.table('policies')} ORDER BY policy_version DESC")
        for row in rows:
            row["policy_version"] = int(row["policy_version"])
            row["body"] = {**DEFAULT_POLICY, **(loads(row.get("body")) or {})}
        return rows

    def me(self, caller: Caller) -> dict[str, Any]:
        _, policy = self.policy()
        group = policy.get("approver_group")
        roles = ["developer"] + (["approver"] if not group or group in caller.groups else []) + (["admin"] if is_admin(caller) else [])
        return {"user": caller.email, "groups": caller.groups, "roles": roles}

    def set_policy(self, caller: Caller, changes: dict[str, Any]) -> dict[str, Any]:
        """A new policy version from the console. Workspace admins only; keys that are not changed (Git) stay."""
        if not is_admin(caller):
            raise ServiceError(403, "not_admin", "정책은 워크스페이스 관리자(admins)만 바꿉니다", "aops admin setup --only policy")
        allowed = {"validation_mode": {"mandatory", "optional", "exempt"}, "approval_mode": {"required", "optional", "auto"}}
        for key, values in allowed.items():
            if key in changes and changes[key] not in values:
                raise ServiceError(400, "bad_policy", f"{key}는 {', '.join(sorted(values))} 중 하나입니다")
        version, current = self.policy()
        body = {**current, **{k: v for k, v in changes.items() if k in (*allowed, "approver_group")}}
        if body == current:
            return self.policies()[0] if version else {"policy_version": 0, "body": current}
        new_version = version + 1
        self.sql.atomic([
            (f"UPDATE {self.sql.table('policies')} SET status = 'retired' WHERE status = 'active'", {}),
            (f"INSERT INTO {self.sql.table('policies')} VALUES (:v, 'active', parse_json(:b), :by, current_timestamp())",
             {"v": new_version, "b": body, "by": caller.email}),
        ])
        return self.policies()[0]

    def event(self, agent_id: str, version: str | None, kind: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        self.sql.run(*self._event_statement(agent_id, version, kind, actor, detail))

    def _event_statement(self, agent_id: str, version: str | None, kind: str, actor: str,
                         detail: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        return (f"INSERT INTO {self.sql.table('agent_events')} VALUES (:e, :a, :v, :k, :actor, current_timestamp(), parse_json(:d))",
                {"e": now_id(), "a": agent_id, "v": version, "k": kind, "actor": actor, "d": detail or {}})

    # ── register ────────────────────────────────────────────────────────────

    def register(self, caller: Caller, agent_id: str, data: bytes, version: str | None = None,
                 bump: str | None = None) -> dict[str, Any]:
        files = package.read(data)
        if "aops.toml" not in files or "SPEC.md" not in files:
            raise ServiceError(400, "not_an_agent", "버전 zip에 aops.toml과 SPEC.md가 있어야 합니다", "aops register")
        spec = package.front_matter(files["SPEC.md"].decode("utf-8"))
        if spec.get("agent_id", agent_id) != agent_id:
            raise ServiceError(400, "agent_id_mismatch", f"SPEC.md의 agent_id({spec.get('agent_id')})가 {agent_id}와 다릅니다")
        build_type = spec.get("build_type", "skill").replace("-", "_")
        if build_type != "skill":
            raise ServiceError(501, "not_implemented", "지금은 스킬 기반 에이전트만 등록합니다", "aops register")
        sha = package.files_sha256(list(files.items()))

        agent = self.agent(agent_id)
        if agent and not self._is_owner(caller, agent):
            raise ServiceError(403, "not_owner", "담당자·담당 팀만 새 버전을 등록할 수 있습니다", "aops register")
        existing = self.versions(agent_id)
        if existing and existing[-1]["source_sha256"] == sha:
            raise ServiceError(409, "unchanged", f"마지막 버전({existing[-1]['version']})과 내용이 같습니다", "aops register")
        new_version = version or next_version([v["version"] for v in existing], bump)
        if any(v["version"] == new_version for v in existing):
            raise ServiceError(409, "version_exists", f"{new_version}은 이미 등록된 버전입니다", "aops register")

        # 1. the version zip, written once into the agent's own volume
        self.sql.run(f"CREATE VOLUME IF NOT EXISTS {self.config.registry}.`{agent_id}` "
                     f"COMMENT 'aops 에이전트 {agent_id}의 버전 zip. 쓰기는 aops 서비스 주체만'")
        path = f"/Volumes/{self.config.catalog}/{self.config.schema}/{agent_id}/{new_version}.zip"
        self.w.files.upload(path, io.BytesIO(data), overwrite=False)

        # 2. skills: hashes and the UC skill each will be. The deploy Job creates the UC skill on the first
        #    deploy, so names that never reach production (templates, renamed skills) never appear in UC.
        skills = skill_folders(files)
        hashes = {alias: package.files_sha256(list(content.items())) for alias, content in skills.items()}
        published = {alias: f"{self.config.registry}.{uc_skill_id(agent_id, alias)}" for alias in skills}

        # 3. the registry, in one transaction: an agent row whose columns follow this SPEC, the version,
        #    its skills and the audit line. If it fails, the zip written above is taken back.
        fields = {"name": spec.get("name") or agent_id, "desc": spec.get("description"),
                  "group": spec.get("owner_group") or None, "crit": pick(spec.get("service_criticality"), CRITICALITY, "medium"),
                  "sens": pick(spec.get("data_sensitivity"), SENSITIVITY, "internal"),
                  "sec": spec.get("security_level") or "general", "spec": spec}
        statements: list[tuple[str, dict[str, Any]]] = []
        if not agent:
            statements.append((
                f"INSERT INTO {self.sql.table('agents')} VALUES (:id, :name, :desc, 'skill', :owner, :group, :crit, :sens, :sec, "
                "'registered', parse_json(:spec), :by, current_timestamp(), current_timestamp())",
                {**fields, "id": agent_id, "owner": spec.get("owner") or caller.email, "by": caller.email}))
        else:
            # The owner changes only when the SPEC names one; an empty owner keeps the current owner.
            statements.append((
                f"UPDATE {self.sql.table('agents')} SET name = :name, description = :desc, owner = coalesce(:owner, owner), "
                "owner_group = :group, service_criticality = :crit, data_sensitivity = :sens, security_level = :sec, "
                "spec = parse_json(:spec), updated_at = current_timestamp() WHERE agent_id = :id",
                {**fields, "id": agent_id, "owner": spec.get("owner") or None}))
        harness = re.search(r'harness\s*=\s*"(\w+)"', files["aops.toml"].decode("utf-8"))
        statements.append((
            f"INSERT INTO {self.sql.table('agent_versions')} VALUES (:id, :v, 'registered', :sha, :path, :size, :skills, "
            ":harness, NULL, parse_json(:spec), :by, current_timestamp())",
            {"id": agent_id, "v": new_version, "sha": sha, "path": path, "size": len(data), "skills": skills_sha256(hashes),
             "harness": harness.group(1) if harness else None, "spec": spec, "by": caller.email}))
        for alias, uc_name in published.items():
            statements.append((f"INSERT INTO {self.sql.table('agent_version_skills')} VALUES (:id, :v, :alias, :uc, :sha, :src, 'agent')",
                               {"id": agent_id, "v": new_version, "alias": alias, "uc": uc_name, "sha": hashes[alias],
                                "src": f"agent/skills/{alias}"}))
        statements.append(self._event_statement(agent_id, new_version, "register", caller.email,
                                                {"artifact_path": path, "skills": published, "first": not existing}))
        try:
            self.sql.atomic(statements)
        except Exception:
            try:
                self.w.files.delete(path)  # the zip is written once; without its rows it would block this version
            except Exception:
                pass
            raise
        result = {**self.version(agent_id, new_version), "skills": self.version_skills(agent_id, new_version)}
        # 4. Git mirror (when the policy has one): never fails the registration
        synced = self.sync_git(agent_id, caller.email, known={new_version: files})
        if synced is not None:
            records, error = synced
            result["git"] = records.get(new_version) or {"status": "failed", "error": error}
        return result

    # ── Git mirror (optional) ───────────────────────────────────────────────

    def _github(self) -> GitHub | None:
        settings = GitSettings.from_policy(self.policy()[1])
        return GitHub(settings, proxy_transport(self.w, settings.connection)) if settings else None

    def git_records(self, agent_id: str) -> dict[str, dict[str, Any]]:
        rows = self.sql.run(f"SELECT * FROM {self.sql.table('agent_version_git')} WHERE agent_id = :a", {"a": agent_id})
        return {row["version"]: row for row in rows}

    def sync_git(self, agent_id: str, actor: str, known: dict[str, dict[str, bytes]] | None = None,
                 hub: GitHub | None = None) -> tuple[dict[str, dict[str, Any]], str | None] | None:
        """Put every version that is not in Git yet there, oldest first: (records by version, error).

        None when the policy has no Git. Never raises: Git is a copy, so a failure is recorded
        (`failed`) and the next register or deploy tries again from that version. A version with
        secrets is not sent (`skipped`), because Git history is hard to clean.
        """
        try:
            hub = hub or self._github()
        except Exception as error:
            return {}, f"정책을 읽지 못함: {error}"[:300]
        if hub is None:
            return None
        records: dict[str, dict[str, Any]] = {}
        current = None
        try:
            records = self.git_records(agent_id)
            repo = None
            for row in self.versions(agent_id):
                current = row["version"]
                if records.get(current, {}).get("status") in ("synced", "skipped"):
                    continue
                files = (known or {}).get(current) or package.read(self.w.files.download(row["artifact_path"]).contents.read())
                leaks = find_secrets(files)
                if leaks:
                    records[current] = self._git_record(agent_id, current, f"{hub.settings.owner}/{hub.settings.repo(agent_id)}",
                                                        None, None, "skipped", "비밀값이 있어 올리지 않음: " + ", ".join(leaks), actor)
                    continue
                repo = repo or hub.ensure_repo(agent_id, (self.agent(agent_id) or {}).get("description"))
                commit = hub.publish(repo, current, files, commit_message(agent_id, row), row.get("registered_by") or actor)
                records[current] = self._git_record(agent_id, current, repo["full_name"], f"{repo['html_url']}/tree/v{current}",
                                                    commit, "synced", None, actor)
            return records, None
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:300]
            if current:
                try:
                    records[current] = self._git_record(agent_id, current, f"{hub.settings.owner}/{hub.settings.repo(agent_id)}",
                                                        None, None, "failed", message, actor)
                except Exception:
                    pass  # e.g. no agent_version_git table yet (admin setup --only tables)
            return records, (f"{current}: {message}" if current else message)

    def _git_record(self, agent_id: str, version: str, repo: str, url: str | None, commit: str | None, status: str,
                    error: str | None, actor: str) -> dict[str, Any]:
        row = {"agent_id": agent_id, "version": version, "repo": repo, "url": url, "commit_sha": commit,
               "tag": f"v{version}" if commit else None, "status": status, "error": error}
        self.sql.atomic([
            (f"MERGE INTO {self.sql.table('agent_version_git')} t USING (SELECT :a AS agent_id, :v AS version) s "
             "ON t.agent_id = s.agent_id AND t.version = s.version "
             "WHEN MATCHED THEN UPDATE SET repo = :repo, url = :url, commit_sha = :c, tag = :tag, status = :st, error = :err, "
             "updated_at = current_timestamp() "
             "WHEN NOT MATCHED THEN INSERT (agent_id, version, repo, url, commit_sha, tag, status, error, updated_at) "
             "VALUES (:a, :v, :repo, :url, :c, :tag, :st, :err, current_timestamp())",
             {"a": agent_id, "v": version, "repo": repo, "url": url, "c": commit, "tag": row["tag"], "st": status, "err": error}),
            self._event_statement(agent_id, version, "git", actor,
                                  {"action": "publish", "status": status, "repo": repo, "commit": commit, "error": error}),
        ])
        return row

    def git_production(self, agent_id: str, version: str, actor: str) -> dict[str, Any] | None:
        """After a deploy: the `production` branch follows the production version. Never raises."""
        try:
            hub = self._github()
        except Exception as error:
            return {"status": "failed", "error": f"정책을 읽지 못함: {error}"[:300]}
        if hub is None:
            return None
        records, error = self.sync_git(agent_id, actor, hub=hub) or ({}, None)  # a version that missed Git goes there first
        record = records.get(version)
        if not record or record["status"] != "synced":
            reason = (record or {}).get("error") or error or "버전 커밋이 없습니다"
            return {"status": (record or {}).get("status") or "failed", "error": f"production을 옮기지 않음: {reason}"}
        try:
            hub.move_production(record["repo"], record["commit_sha"])
            result = {"status": "synced", "url": record["url"].rsplit("/tree/", 1)[0] + "/tree/production",
                      "commit_sha": record["commit_sha"]}
        except Exception as failure:
            result = {"status": "failed", "error": f"{type(failure).__name__}: {failure}"[:300]}
        try:
            self.event(agent_id, version, "git", actor, {"action": "production", **result})
        except Exception:
            pass
        return result

    # ── UC skills ───────────────────────────────────────────────────────────

    def _skill_exists(self, uc_name: str) -> bool:
        try:
            self.w.ai_gateway.get_skill(f"skills/{uc_name}")
            return True
        except Exception as error:
            if "NOT_FOUND" in str(error) or "does not exist" in str(error).lower() or "not found" in str(error).lower():
                return False
            raise

    def _create_skill(self, uc_name: str) -> None:
        from databricks.sdk.service.catalog import Skill

        catalog, schema, skill_id = uc_name.split(".", 2)
        self.w.ai_gateway.create_skill(Skill(comment="aops가 관리하는 운영 스킬"), f"schemas/{catalog}.{schema}", skill_id)

    def _skill_path(self, uc_name: str) -> str:
        catalog, schema, skill_id = uc_name.split(".", 2)
        return f"/Skills/{catalog}/{schema}/{skill_id}"

    def _sync_skill(self, uc_name: str, content: dict[str, bytes]) -> None:
        """Make the UC skill hold exactly `content`: changed files, SKILL.md last, stale files removed, finalize."""
        base = self._skill_path(uc_name)
        remote = set(self._remote_files(base))
        for rel in sorted(content, key=lambda r: (r == "SKILL.md", r)):
            self.w.files.upload(f"{base}/{rel}", io.BytesIO(content[rel]), overwrite=True)
        for rel in remote - set(content):
            self.w.files.delete(f"{base}/{rel}")
        self.w.ai_gateway.finalize_skill(f"skills/{uc_name}")

    def _remote_files(self, base: str, prefix: str = "") -> list[str]:
        out = []
        try:
            entries = list(self.w.files.list_directory_contents(f"{base}/{prefix}".rstrip("/")))
        except Exception:
            return out
        for entry in entries:
            rel = entry.path[len(base) + 1:]
            if entry.is_directory:
                out.extend(self._remote_files(base, rel))
            else:
                out.append(rel)
        return out

    # ── validate ────────────────────────────────────────────────────────────

    def start_validation(self, caller: Caller, agent_id: str, version: str | None = None) -> dict[str, Any]:
        agent = self._agent_or_404(agent_id)
        if not self._is_owner(caller, agent):
            raise ServiceError(403, "not_owner", "담당자·담당 팀만 검사를 요청할 수 있습니다", "aops validate")
        target = self._version_or_latest(agent_id, version)
        policy_version, _ = self.policy()
        run_id = now_id()
        self.sql.run(
            f"INSERT INTO {self.sql.table('validation_runs')} VALUES (:r, :a, :v, :sha, :p, 'job', 'queued', NULL, NULL, NULL, NULL, "
            ":by, current_timestamp(), NULL)",
            {"r": run_id, "a": agent_id, "v": target["version"], "sha": target["source_sha256"], "p": policy_version, "by": caller.email},
        )
        job_run = self._run_job("validate", run_id)
        self.sql.run(f"UPDATE {self.sql.table('validation_runs')} SET job_run_id = :j WHERE validation_run_id = :r",
                     {"j": job_run, "r": run_id})
        return self.validation(run_id)

    def validation(self, run_id: str) -> dict[str, Any]:
        row = self.sql.one(f"SELECT * FROM {self.sql.table('validation_runs')} WHERE validation_run_id = :r", {"r": run_id})
        if not row:
            raise ServiceError(404, "not_found", "검사 실행이 없습니다")
        row["steps"] = loads(row.get("steps")) or []
        row["checks"] = loads(row.get("checks")) or {}
        row["policy_version"] = int(row["policy_version"])
        return row

    def run_validation(self, run_id: str) -> dict[str, Any]:
        """Inside the validate Job: check the registered zip against the policy, record the result, return a report."""
        run = self.validation(run_id)
        self.sql.run(f"UPDATE {self.sql.table('validation_runs')} SET status = 'running' WHERE validation_run_id = :r", {"r": run_id})
        steps: list[dict[str, Any]] = []
        report: dict[str, Any] = {"agent_id": run["agent_id"], "version": run["version"], "artifact_path": None,
                                  "sha256": None, "files": []}
        try:
            version = self.version(run["agent_id"], run["version"])
            report.update(artifact_path=version["artifact_path"], sha256=version["source_sha256"])
            data = self.w.files.download(version["artifact_path"]).contents.read()
            files = package.read(data)
            report["files"] = sorted(files)
            steps = check_skill_agent(files)
            same = package.files_sha256(list(files.items())) == version["source_sha256"]
            steps.append({"name": "record", "status": "passed" if same else "failed",
                          "detail": "버전 zip이 등록 때 해시와 같습니다" if same else "버전 zip이 등록 때 해시와 다릅니다"})
            status = "passed" if all(s["status"] != "failed" for s in steps) else "failed"
            reason = None if status == "passed" else "; ".join(s["detail"] for s in steps if s["status"] == "failed")
        except Exception as error:
            status, reason = "error", f"{type(error).__name__}: {error}"
        self.sql.run(
            f"UPDATE {self.sql.table('validation_runs')} SET status = :s, steps = parse_json(:steps), failure_reason = :why, "
            "ended_at = current_timestamp() WHERE validation_run_id = :r",
            {"s": status, "steps": steps, "why": reason, "r": run_id},
        )
        self.event(run["agent_id"], run["version"], "validate", run["started_by"], {"status": status, "validation_run_id": run_id})
        return {**report, "status": status, "reason": reason, "steps": steps}

    # ── deploy ──────────────────────────────────────────────────────────────

    def request_deploy(self, caller: Caller, agent_id: str, version: str | None = None, reason: str | None = None) -> dict[str, Any]:
        agent = self._agent_or_404(agent_id)
        if not self._is_owner(caller, agent):
            raise ServiceError(403, "not_owner", "담당자·담당 팀만 배포를 요청할 수 있습니다", "aops deploy")
        target = self._version_or_latest(agent_id, version)
        open_gate = self.sql.one(
            f"SELECT g.gate_id, g.version FROM {self.sql.table('release_gates')} g LEFT JOIN {self.sql.table('deployments')} d "
            "ON d.deployment_id = g.deployment_id WHERE g.agent_id = :a AND (g.decision IN ('validating', 'pending_approval') "
            "OR (g.decision = 'approved' AND (d.status IS NULL OR d.status = 'running')))", {"a": agent_id})
        if open_gate and open_gate["version"] == target["version"]:
            return self.advance(open_gate["gate_id"])  # the same request again: keep following it
        if open_gate:
            raise ServiceError(409, "gate_open", f"진행 중인 배포 요청이 있습니다 ({open_gate['version']}, {open_gate['gate_id']})", "aops deploy")
        policy_version, policy = self.policy()
        production = self.production(agent_id)
        upgrade = upgrade_type(production["current_version"] if production else None, target["version"])
        gate_id = now_id()
        self.sql.run(
            f"INSERT INTO {self.sql.table('release_gates')} VALUES (:g, :a, :v, :by, :why, current_timestamp(), :up, :p, :vm, NULL, "
            ":am, :ch, :n, NULL, NULL, 'validating', NULL, NULL, current_timestamp())",
            {"g": gate_id, "a": agent_id, "v": target["version"], "by": caller.email, "why": reason, "up": upgrade,
             "p": policy_version, "vm": policy["validation_mode"], "am": policy["approval_mode"],
             "ch": policy["approval_channel"], "n": int(policy["required_approvals"])},
        )
        self.event(agent_id, target["version"], "gate_requested", caller.email, {"gate_id": gate_id, "upgrade_type": upgrade})

        passed = self._passed_validation(agent_id, target["version"], policy_version)
        if policy["validation_mode"] == "exempt" or passed:
            self._after_gate(gate_id, passed["validation_run_id"] if passed else None)
        else:
            run = self.start_validation(caller, agent_id, target["version"])
            self._set_gate(gate_id, validation_run_id=run["validation_run_id"])
            threading.Thread(target=self._follow, args=(gate_id,), daemon=True).start()
        return self.gate(gate_id)

    def _passed_validation(self, agent_id: str, version: str, policy_version: int) -> dict[str, Any] | None:
        return self.sql.one(f"SELECT validation_run_id FROM {self.sql.table('validation_runs')} WHERE agent_id = :a AND version = :v "
                            "AND policy_version = :p AND status = 'passed' AND mode = 'job' ORDER BY started_at DESC LIMIT 1",
                            {"a": agent_id, "v": version, "p": policy_version})

    def _follow(self, gate_id: str, timeout: int = 1800) -> None:
        """Background: move the gate on when its validation ends. advance() is also called on every read."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.advance(gate_id)["decision"] != "validating":
                return
            time.sleep(5)

    def advance(self, gate_id: str) -> dict[str, Any]:
        """Move a validating gate on once its validation has finished. Safe to call from anywhere, any time."""
        gate = self.gate(gate_id)
        if gate["decision"] != "validating" or not gate.get("validation_run_id"):
            return gate
        run = self.validation(gate["validation_run_id"])
        if run["status"] not in ("passed", "failed", "stale", "error"):
            return gate
        if run["status"] == "passed":
            self._after_gate(gate_id, run["validation_run_id"])
        elif run["status"] == "failed" and gate["validation_policy_mode"] == "optional":
            # optional: the failed checks stay on record and an approver decides
            self._after_gate(gate_id, run["validation_run_id"],
                             failed=f"검사 실패 (검사 방식 optional, 승인자 판단): {run.get('failure_reason') or ''}")
        elif self.sql.update(f"UPDATE {self.sql.table('release_gates')} SET decision = 'rejected', decision_reason = :why, "
                             "updated_at = current_timestamp() WHERE gate_id = :g AND decision = 'validating'",
                             {"g": gate_id, "why": f"검사 {run['status']}: {run.get('failure_reason') or ''}"}):
            self.event(gate["agent_id"], gate["version"], "gate_rejected", "policy:gate", {"gate_id": gate_id, "validation": run["status"]})
        return self.gate(gate_id)

    def _after_gate(self, gate_id: str, run_id: str | None, failed: str | None = None) -> None:
        gate = self.gate(gate_id)
        approve, why = (False, failed) if failed else self._auto_approval(gate)  # a failed check always waits for a person
        moved = self.sql.update(f"UPDATE {self.sql.table('release_gates')} SET decision = 'pending_approval', validation_run_id = :r, "
                                "decision_reason = :why, updated_at = current_timestamp() WHERE gate_id = :g AND decision = 'validating'",
                                {"g": gate_id, "r": run_id, "why": None if approve else why})
        if not moved or not approve:
            return
        self.sql.run(f"INSERT INTO {self.sql.table('approvals')} VALUES (:id, :g, 'policy:auto', 'approve', :c, "
                     "'policy', current_timestamp())", {"id": now_id(), "g": gate_id, "c": why})
        self._approved(gate, "policy:auto")

    def _auto_approval(self, gate: dict[str, Any]) -> tuple[bool, str | None]:
        """Whether the policy approves this request by itself, and why (or why a person has to)."""
        if gate["approval_mode"] == "auto":
            return True, "정책의 자동 승인"
        if gate["approval_mode"] != "optional":
            return False, None
        spec = loads((self.version(gate["agent_id"], gate["version"]) or {}).get("spec")) or {}
        crit = pick(spec.get("service_criticality"), CRITICALITY, "medium")
        sens = pick(spec.get("data_sensitivity"), SENSITIVITY, "internal")
        values = f"중요도 {crit}, 민감도 {sens}"
        if crit == "low" and sens in ("public", "internal"):
            return True, f"정책의 자동 승인 (approval optional: {values})"
        return False, f"승인자 필요 (approval optional: {values}. 자동 승인은 중요도 low이고 민감도 public·internal일 때)"

    def gates(self, caller: Caller, decision: str | None = None, mine: bool = False, to_approve: bool = False,
              agent_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Deploy requests, newest first. `to_approve`: waiting for this caller (not their own, in the approver group)."""
        clauses, params = [], {}
        if decision:
            clauses.append("g.decision = :d")
            params["d"] = decision
        if agent_id:
            clauses.append("g.agent_id = :a")
            params["a"] = agent_id
        if mine:
            clauses.append("g.requester = :me")
            params["me"] = caller.email
        if to_approve:
            _, policy = self.policy()
            if policy["approver_group"] and policy["approver_group"] not in caller.groups:
                return []
            clauses.append("g.decision = 'pending_approval' AND g.requester <> :me")
            params["me"] = caller.email
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.sql.run(
            f"SELECT g.*, d.status AS deployment_status, (SELECT count(*) FROM {self.sql.table('approvals')} a "
            f"WHERE a.gate_id = g.gate_id AND a.decision = 'approve') AS approvals FROM {self.sql.table('release_gates')} g "
            f"LEFT JOIN {self.sql.table('deployments')} d ON d.deployment_id = g.deployment_id {where} "
            f"ORDER BY g.requested_at DESC LIMIT {int(limit)}", params)

    def _check_approver(self, caller: Caller, gate: dict[str, Any], action: str) -> None:
        if gate["decision"] != "pending_approval":
            raise ServiceError(409, "not_pending", f"승인 대기 중인 요청이 아닙니다 ({gate['decision']})", f"aops {action}")
        if caller.email == gate["requester"]:
            hint = "요청자 본인은 승인할 수 없습니다" if action == "approve" else "요청자 본인은 반려 대신 aops deploy --cancel로 취소합니다"
            raise ServiceError(403, "self_approval", hint, f"aops {action}")
        _, policy = self.policy()
        if policy["approver_group"] and policy["approver_group"] not in caller.groups:
            raise ServiceError(403, "not_approver", f"승인 그룹({policy['approver_group']})만 할 수 있습니다", f"aops {action}")

    def reject(self, caller: Caller, gate_id: str, reason: str) -> dict[str, Any]:
        gate = self.gate(gate_id)
        self._check_approver(caller, gate, "reject")
        self.sql.run(f"INSERT INTO {self.sql.table('approvals')} VALUES (:id, :g, :by, 'reject', :c, 'console', current_timestamp())",
                     {"id": now_id(), "g": gate_id, "by": caller.email, "c": reason})
        if self.sql.update(f"UPDATE {self.sql.table('release_gates')} SET decision = 'rejected', decision_reason = :why, "
                           "updated_at = current_timestamp() WHERE gate_id = :g AND decision = 'pending_approval'",
                           {"g": gate_id, "why": f"반려 ({caller.email}): {reason}"}):
            self.event(gate["agent_id"], gate["version"], "gate_rejected", caller.email, {"gate_id": gate_id, "reason": reason})
        return self.gate(gate_id)

    def cancel(self, caller: Caller, gate_id: str) -> dict[str, Any]:
        gate = self.gate(gate_id)
        if caller.email != gate["requester"] and not self._is_owner(caller, self._agent_or_404(gate["agent_id"])):
            raise ServiceError(403, "not_owner", "요청자나 담당자·담당 팀만 취소할 수 있습니다", "aops deploy --cancel")
        if gate["decision"] not in ("validating", "pending_approval"):
            raise ServiceError(409, "not_open", f"취소할 수 없는 상태입니다 ({gate['decision']}). 운영 반영 뒤에는 이전 버전을 deploy해 되돌립니다",
                               "aops deploy --cancel")
        if self.sql.update(f"UPDATE {self.sql.table('release_gates')} SET decision = 'canceled', decision_reason = :why, "
                           "updated_at = current_timestamp() WHERE gate_id = :g AND decision IN ('validating', 'pending_approval')",
                           {"g": gate_id, "why": f"취소 ({caller.email})"}):
            self.event(gate["agent_id"], gate["version"], "gate_canceled", caller.email, {"gate_id": gate_id, "was": gate["decision"]})
        return self.gate(gate_id)

    def approve(self, caller: Caller, gate_id: str, comment: str | None = None) -> dict[str, Any]:
        gate = self.gate(gate_id)
        self._check_approver(caller, gate, "approve")
        self.sql.run(f"INSERT INTO {self.sql.table('approvals')} VALUES (:id, :g, :by, 'approve', :c, 'console', current_timestamp())",
                     {"id": now_id(), "g": gate_id, "by": caller.email, "c": comment})
        approved = self.sql.one(f"SELECT count(*) AS n FROM {self.sql.table('approvals')} WHERE gate_id = :g AND decision = 'approve'",
                                {"g": gate_id})
        if int(approved["n"]) >= int(gate["required_approvals"]):
            self._approved(gate, caller.email)
        return self.gate(gate_id)

    def _approved(self, gate: dict[str, Any], actor: str) -> None:
        deployment_id = now_id()
        if not self.sql.update(f"UPDATE {self.sql.table('release_gates')} SET decision = 'approved', deployment_id = :d, "
                               "updated_at = current_timestamp() WHERE gate_id = :g AND decision = 'pending_approval'",
                               {"g": gate["gate_id"], "d": deployment_id}):
            return
        self.sql.run(f"INSERT INTO {self.sql.table('deployments')} VALUES (:d, :g, :a, :v, 'production', NULL, NULL, 'running', NULL, "
                     "NULL, current_timestamp(), NULL)", {"d": deployment_id, "g": gate["gate_id"], "a": gate["agent_id"], "v": gate["version"]})
        self.event(gate["agent_id"], gate["version"], "gate_approved", actor,
                   {"gate_id": gate["gate_id"], "approval_mode": gate["approval_mode"]})
        job_run = self._run_job("deploy", gate["gate_id"])
        self.sql.run(f"UPDATE {self.sql.table('deployments')} SET job_run_id = :j WHERE deployment_id = :d",
                     {"j": job_run, "d": deployment_id})

    def run_deploy(self, gate_id: str) -> str:
        """Inside the deploy Job: check the approval again and roll the version out."""
        gate = self.gate(gate_id)
        deployment_id = gate["deployment_id"]
        try:
            if gate["decision"] != "approved":
                raise RuntimeError(f"승인되지 않은 요청입니다 ({gate['decision']})")
            agent_id, version = gate["agent_id"], gate["version"]
            target = self.version(agent_id, version)
            files = package.read(self.w.files.download(target["artifact_path"]).contents.read())
            if package.files_sha256(list(files.items())) != target["source_sha256"]:
                raise RuntimeError("버전 zip이 등록 때 해시와 다릅니다")
            production = self.production(agent_id)
            live = {s["skill_alias"]: s["skill_sha256"] for s in self.version_skills(agent_id, production["current_version"])} if production else {}
            names, skills = [], []
            for alias, content in skill_folders(files).items():
                row = next((s for s in self.version_skills(agent_id, version) if s["skill_alias"] == alias), None)
                if not row or not row["uc_name"]:
                    continue
                if not self._skill_exists(row["uc_name"]):
                    self._create_skill(row["uc_name"])
                    self._sync_skill(row["uc_name"], content)
                    action = "created"
                elif live.get(alias) != row["skill_sha256"] or not production:
                    self._sync_skill(row["uc_name"], content)
                    action = "updated"
                else:
                    action = "unchanged"  # the content is already this version
                names.append(row["uc_name"])
                skills.append({"uc_name": row["uc_name"], "action": action, "files": sorted(content)})
            # skills of the old production version that this version no longer has: kept, only reported
            kept = set(skill_folders(files))
            removed = [s["uc_name"] for s in (self.version_skills(agent_id, production["current_version"]) if production else [])
                       if s["skill_alias"] not in kept and s["uc_name"]]
            previous = production["current_version"] if production else None
            kind = "rollback" if gate["upgrade_type"] == "rollback" else "deploy"
            # Which version is in production lives in three places (versions, the pointer, the agent): they
            # change together with the deployment result and the audit line, or not at all.
            self.sql.atomic([
                (f"UPDATE {self.sql.table('agent_versions')} SET status = 'retired' WHERE agent_id = :a AND status = 'production'",
                 {"a": agent_id}),
                (f"UPDATE {self.sql.table('agent_versions')} SET status = 'production' WHERE agent_id = :a AND version = :v",
                 {"a": agent_id, "v": version}),
                (f"MERGE INTO {self.sql.table('agent_production')} t USING (SELECT :a AS agent_id) s ON t.agent_id = s.agent_id "
                 "WHEN MATCHED THEN UPDATE SET previous_version = t.current_version, current_version = :v, target = :t, updated_by = :by, "
                 "updated_at = current_timestamp() WHEN NOT MATCHED THEN INSERT (agent_id, current_version, previous_version, target, updated_by, updated_at) "
                 "VALUES (:a, :v, NULL, :t, :by, current_timestamp())",
                 {"a": agent_id, "v": version, "t": ", ".join(names), "by": gate["requester"]}),
                (f"UPDATE {self.sql.table('agents')} SET lifecycle = 'production', updated_at = current_timestamp() WHERE agent_id = :a",
                 {"a": agent_id}),
                (f"UPDATE {self.sql.table('deployments')} SET status = 'succeeded', target = :t, ended_at = current_timestamp() "
                 "WHERE deployment_id = :d", {"t": ", ".join(names), "d": deployment_id}),
                self._event_statement(agent_id, version, kind, gate["requester"],
                                      {"gate_id": gate_id, "previous": previous, "skills": names, "not_in_version": removed}),
            ])
            report = {"status": "succeeded", "agent_id": agent_id, "version": version, "previous": previous,
                      "approval_mode": gate.get("approval_mode"), "requester": gate["requester"], "skills": skills,
                      "not_in_version": removed, "artifact_path": target["artifact_path"]}
        except Exception as error:
            self.sql.run(f"UPDATE {self.sql.table('deployments')} SET status = 'failed', detail = parse_json(:d), ended_at = current_timestamp() "
                         "WHERE deployment_id = :id", {"d": {"error": f"{type(error).__name__}: {error}"}, "id": deployment_id})
            raise
        report["git"] = self.git_production(agent_id, version, gate["requester"])  # outside: Git never fails a deploy
        return report

    def gate(self, gate_id: str) -> dict[str, Any]:
        row = self.sql.one(f"SELECT * FROM {self.sql.table('release_gates')} WHERE gate_id = :g", {"g": gate_id})
        if not row:
            raise ServiceError(404, "not_found", "배포 요청이 없습니다")
        deployment = self.sql.one(f"SELECT status FROM {self.sql.table('deployments')} WHERE deployment_id = :d",
                                  {"d": row["deployment_id"]}) if row.get("deployment_id") else None
        row["deployment_status"] = deployment["status"] if deployment else None
        approvals = self.sql.one(f"SELECT count(*) AS n, max_by(approver, decided_at) AS last FROM {self.sql.table('approvals')} "
                                 "WHERE gate_id = :g AND decision = 'approve'", {"g": gate_id})
        row["approvals"] = int(approvals["n"]) if approvals else 0
        row["approved_by"] = approvals.get("last") if approvals else None
        return row

    def _set_gate(self, gate_id: str, **values: Any) -> None:
        sets = ", ".join(f"{k} = :{k}" for k in values)
        statement = f"UPDATE {self.sql.table('release_gates')} SET {sets}, updated_at = current_timestamp() WHERE gate_id = :g"
        for attempt in range(3):  # a plain write: on a concurrent-write conflict, write again
            if self.sql.update(statement, {**values, "g": gate_id}) or attempt == 2:
                return
            time.sleep(1)

    # ── promote, load ───────────────────────────────────────────────────────

    def promote(self, caller: Caller, agent_id: str, principals: list[str], revoke: bool = False) -> list[dict[str, Any]]:
        from databricks.sdk.service.catalog import PermissionsChange, Privilege

        agent = self._agent_or_404(agent_id)
        if not self._is_owner(caller, agent):
            raise ServiceError(403, "not_owner", "담당자·담당 팀만 권한을 줄 수 있습니다", "aops promote")
        production = self.production(agent_id)
        if not production:
            raise ServiceError(409, "no_production", "운영 버전이 없습니다. 먼저 aops deploy 하세요", "aops promote")
        names = [s["uc_name"] for s in self.version_skills(agent_id, production["current_version"]) if s["uc_name"]]
        grants = []
        for principal in principals:
            name = principal.split(":", 1)[1] if ":" in principal else principal
            for uc_name in names:
                read = [Privilege.READ_VOLUME]  # the SDK wants the enum, not the name
                change = PermissionsChange(principal=name, remove=read) if revoke else PermissionsChange(principal=name, add=read)
                self.w.grants.update("skill", uc_name, changes=[change])
                grants.append({"agent_id": agent_id, "securable": f"skill:{uc_name}", "principal": principal,
                               "permission": "READ_VOLUME", "granted": not revoke})
            self.event(agent_id, production["current_version"], "promote", caller.email,
                       {"principal": principal, "permission": "READ_VOLUME", "granted": not revoke, "skills": names})
        return grants

    def load(self, caller: Caller, agent_id: str, version: str | None = None) -> dict[str, Any]:
        agent = self._agent_or_404(agent_id)
        target, allowed = self._loadable(caller, agent, version)
        if allowed:
            self.event(agent_id, target["version"], "load", caller.email, {"version": target["version"]})
        return {"agent_id": agent_id, "build_type": agent["build_type"], "version": target["version"], "link": None,
                "allowed": allowed,
                "archive": {"url": f"/api/v1/agents/{agent_id}/versions/{target['version']}/archive",
                            "sha256": target["source_sha256"], "size_bytes": int(target["size_bytes"] or 0)} if allowed else None}

    def archive(self, caller: Caller, agent_id: str, version: str) -> bytes:
        agent = self._agent_or_404(agent_id)
        target, allowed = self._loadable(caller, agent, version)
        if not allowed:
            raise ServiceError(403, "no_access", "이 에이전트를 가져올 권한이 없습니다. 담당자에게 promote를 요청하세요", "aops load")
        return self.w.files.download(target["artifact_path"]).contents.read()

    def _loadable(self, caller: Caller, agent: dict[str, Any], version: str | None) -> tuple[dict[str, Any], bool]:
        owner = self._is_owner(caller, agent)
        production = self.production(agent["agent_id"])
        if version and version != (production or {}).get("current_version"):
            if not owner:
                raise ServiceError(403, "not_owner", "운영 버전이 아닌 버전은 담당자·담당 팀만 가져올 수 있습니다", "aops load")
            target = self.version(agent["agent_id"], version)
            if not target:
                raise ServiceError(404, "not_found", f"{version} 버전이 없습니다", "aops load")
            return target, True
        if not production:
            raise ServiceError(409, "no_production", "운영 버전이 없습니다", "aops load")
        target = self.version(agent["agent_id"], production["current_version"])
        if owner:
            return target, True
        skills = [s["uc_name"] for s in self.version_skills(agent["agent_id"], target["version"]) if s["uc_name"]]
        return target, bool(skills) and all(self._can_read_skill(caller, name) for name in skills)

    def _can_read_skill(self, caller: Caller, uc_name: str) -> bool:
        """Reading SKILL.md with the requester's own token: 200 means READ_VOLUME (directly or through a group)."""
        if caller.token:
            result = self._as_user(caller.token, "GET", f"/api/2.0/fs/files{self._skill_path(uc_name)}/SKILL.md")
            if result["status"] in (200, 404):
                return result["status"] == 200
        effective = self.w.grants.get_effective("skill", uc_name, principal=caller.email)
        privileges = {p.privilege.value if hasattr(p.privilege, "value") else str(p.privilege)
                      for a in (effective.privilege_assignments or []) for p in (a.privileges or [])}
        return bool(privileges & {"READ_VOLUME", "ALL_PRIVILEGES", "MANAGE"})

    # ── helpers ─────────────────────────────────────────────────────────────

    def _agent_or_404(self, agent_id: str) -> dict[str, Any]:
        agent = self.agent(agent_id)
        if not agent:
            raise ServiceError(404, "not_found", f"등록된 에이전트가 아닙니다: {agent_id}")
        return agent

    def _version_or_latest(self, agent_id: str, version: str | None) -> dict[str, Any]:
        target = self.version(agent_id, version) if version else (self.versions(agent_id) or [None])[-1]
        if not target:
            raise ServiceError(404, "not_found", f"등록된 버전이 없습니다: {agent_id} {version or ''}".strip(), "aops register")
        return target

    def _run_job(self, kind: str, target_id: str) -> int:
        job_id = self._job(kind)
        run = self.w.jobs.run_now(job_id, job_parameters={"id": target_id})
        return run.run_id

    def _job_settings(self, kind: str) -> dict[str, Any]:
        from databricks.sdk.service import compute, jobs

        registry, wheel = self.config.registry, self.config.dist_wheel
        code = f"실행 코드: {wheel} 의 aops-job → aops.dbx.service.Service.run_{'validation' if kind == 'validate' else 'deploy'}"
        if kind == "validate":
            description = ("aops 배포 전 검사(Gate). aops 서버가 배포 요청마다 실행합니다.\n"
                           + "\n".join(f"- {name}: {rule}" for name, rule in CHECK_RULES.items())
                           + f"\n{code} (check_skill_agent)\n결과: {registry}.validation_runs.steps, 실행 출력에 검사별 결과")
        else:
            description = ("aops 운영 반영. 승인된 배포 요청마다 aops 서버가 실행합니다.\n"
                           "승인 상태와 zip 해시를 다시 확인한 뒤, 스킬마다 UC Skill 내용을 이 버전으로 바꿉니다 "
                           "(바뀐 파일 올리기, SKILL.md는 마지막, 남은 파일 지우기).\n"
                           f"{code}\n결과: {registry}.agent_production · deployments · agent_events, 실행 출력에 스킬별 결과")
        params = ["--kind", kind, "--id", "{{job.parameters.id}}", "--registry", registry, "--warehouse", self.config.warehouse_id]
        return {
            "name": f"aops-{kind}",
            "description": description,
            "max_concurrent_runs": 10 if kind == "validate" else 1,
            "performance_target": jobs.PerformanceTarget.PERFORMANCE_OPTIMIZED,
            "parameters": [jobs.JobParameterDefinition(name="id", default="")],
            "environments": [jobs.JobEnvironment(environment_key="aops", spec=compute.Environment(
                environment_version="2", dependencies=[wheel, "databricks-sdk>=0.158"]))],
            "tasks": [jobs.Task(task_key=kind, environment_key="aops", max_retries=0, disable_auto_optimization=True,
                                description=description.split("\n")[0],
                                python_wheel_task=jobs.PythonWheelTask(package_name="aops_cli", entry_point="aops-job",
                                                                       parameters=params))],
        }

    def _job(self, kind: str) -> int:
        """The validate or deploy Job, created by this identity so it runs as itself, kept on this server's package."""
        if kind in self._jobs:
            return self._jobs[kind]
        from databricks.sdk.service import jobs

        if not self.config.dist_wheel:
            raise ServiceError(500, "no_wheel", "Job에 쓸 aops 패키지 경로(AOPS_DIST_WHEEL)가 없습니다. aops admin setup을 다시 실행하세요")
        settings = self._job_settings(kind)
        for found in self.w.jobs.list(name=settings["name"]):
            current = self.w.jobs.get(found.job_id).settings
            deps = [d for env in (current.environments or []) for d in ((env.spec.dependencies if env.spec else None) or [])]
            if self.config.dist_wheel not in deps or current.description != settings["description"]:
                self.w.jobs.reset(found.job_id, new_settings=jobs.JobSettings(**settings))  # new package or new description
            self._jobs[kind] = found.job_id
            return found.job_id
        created = self.w.jobs.create(**settings)
        self._jobs[kind] = created.job_id
        return created.job_id


# ── pure helpers ────────────────────────────────────────────────────────────

def uc_skill_id(agent_id: str, alias: str) -> str:
    return f"{agent_id}__{alias}".lower()


def skill_folders(files: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """agent/skills/<alias>/… → {alias: {relative path: content}} for folders that hold SKILL.md."""
    out: dict[str, dict[str, bytes]] = {}
    for rel, data in files.items():
        parts = rel.split("/")
        if len(parts) >= 4 and parts[0] == "agent" and parts[1] == "skills":
            out.setdefault(parts[2], {})["/".join(parts[3:])] = data
    return {alias: content for alias, content in out.items() if "SKILL.md" in content}


def next_version(existing: list[str], bump: str | None) -> str:
    if not existing:
        return "1.0.0"
    major, minor, patch = (int(x) for x in max(existing, key=semver).split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "fix":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor + 1}.0"


def semver(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.split("."))


def upgrade_type(current: str | None, new: str) -> str:
    if not current:
        return "initial"
    a, b = semver(current), semver(new)
    if b < a:
        return "rollback"
    return "major" if b[0] > a[0] else "minor" if b[1] > a[1] else "patch"


def pick(value: str | None, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def check_skill_agent(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """The validate steps for a skill agent: format, skills, secrets. An untouched init template fails."""
    steps: list[dict[str, Any]] = []
    spec = package.front_matter(files.get("SPEC.md", b"").decode("utf-8", "replace"))
    missing = [k for k in ("agent_id", "name", "build_type") if not spec.get(k)]
    bad = [k for k, allowed in (("service_criticality", CRITICALITY), ("data_sensitivity", SENSITIVITY)) if spec.get(k) not in allowed]
    problems = [f"빈 항목 {', '.join(missing)}"] if missing else []
    problems += [f"허용되지 않는 값 {', '.join(f'{k}={spec.get(k)!r}' for k in bad)}"] if bad else []
    if str(spec.get("description") or "").strip() == SPEC_DESCRIPTION:
        problems.append("description이 템플릿 그대로입니다 (이 에이전트가 하는 일을 적으세요)")
    steps.append({"name": "format", "status": "failed" if problems else "passed",
                  "detail": "SPEC.md: " + "; ".join(problems) if problems else "SPEC.md 필수 항목과 값이 맞습니다"})

    problems = []
    skills = skill_folders(files)
    if not skills:
        problems.append("agent/skills 아래에 SKILL.md가 있는 스킬이 없습니다")
    for alias, content in skills.items():
        front = package.front_matter(content["SKILL.md"].decode("utf-8", "replace"))
        if not SKILL_NAME.match(alias):
            problems.append(f"{alias}: 스킬 이름은 소문자·숫자·하이픈 64자 이하 (하이픈으로 시작·끝·연속 불가)")
        if front.get("name") != alias:
            problems.append(f"{alias}: SKILL.md의 name이 폴더 이름과 다릅니다")
        if not 1 <= len(str(front.get("description") or "")) <= 1024:
            problems.append(f"{alias}: description은 1~1024자")
        if alias == TEMPLATE_SKILL:
            problems.append(f"{alias}: init 템플릿입니다. 폴더 이름과 SKILL.md를 내 스킬로 바꾸세요")
        elif str(front.get("description") or "").strip() == SKILL_DESCRIPTION:
            problems.append(f"{alias}: description이 템플릿 그대로입니다 (언제 이 스킬을 쓰는지 적으세요)")
    steps.append({"name": "skills", "status": "failed" if problems else "passed",
                  "detail": "; ".join(problems) if problems else f"스킬 {len(skills)}개 형식이 맞습니다 ({', '.join(sorted(skills))})"})

    leaks = find_secrets(files)
    steps.append({"name": "secrets", "status": "failed" if leaks else "passed",
                  "detail": "비밀값·금지 파일: " + ", ".join(leaks) if leaks else "비밀값·금지 파일이 없습니다"})
    return steps


def is_admin(caller: Caller) -> bool:
    return "admins" in caller.groups


def find_secrets(files: dict[str, bytes]) -> list[str]:
    """Files with a secret-looking value or a forbidden name (the Gate's secrets check, also run before Git)."""
    leaks = [f"{rel} ({label})" for rel, data in files.items() for label, pattern in SECRET_PATTERNS.items()
             if pattern.search(data.decode("utf-8", "ignore"))]
    return leaks + [f"{rel} (금지 파일)" for rel in files if FORBIDDEN_FILES.search(rel)]
