"""No-code (Genie Agent) agents: an aops agent folder with SPEC.md and a dev Genie space, released as a
production Genie space that the aops service principal owns.

- create: the server makes the agent folder (owned by the aops service principal; the author gets CAN MANAGE),
  writes SPEC.md there, and the author's own token makes the dev Genie space in it (new from tables, a copy of
  a space, or an existing space moved in). The service principal reads the dev space through the folder.
- deploy request: SPEC.md and the dev definition are snapshotted into the agent volume (checks/<sha>.zip). The
  Gate checks that snapshot and the deploy Job puts the same snapshot into the production space, so what was
  checked is what goes out. The version number is given when the deploy succeeds: 1.0.0 first, then major when
  the data sources change and minor otherwise.
- promote: CAN RUN on the production space. load: its link (Genie checks CAN RUN itself).
"""

from __future__ import annotations

import io
import json
import re
from typing import Any

from .. import package
from . import genie_rules
from .base import AGENT_ID, CRITICALITY, SENSITIVITY, Caller, ServiceError, now_id

ROOT = "/Workspace/aops/no-code"  # admin setup makes it and gives the aops service principal CAN MANAGE
PRODUCTION = "_production"  # production spaces: <root>/_production/<agent_id> (an agent id never starts with "_")
SPACE = "genie/space.json"
SPACE_ID = re.compile(r"^[0-9a-f]{32}$")
TABLE = re.compile(r"^[^.\s`]+\.[^.\s`]+\.[^.\s`]+$")

NO_CODE_TOML = """\
config_version = 1

[project]
name = "{name}"
edition = "dbx"

[agent]
build_type = "no_code"     # 노코드(Genie Agent). 정의는 genie/space.json: 배포 요청 때 개발용 Genie에서 뜬 스냅샷
"""

TEXT_TEMPLATE = """\
## PURPOSE
{purpose}

## CONSTRAINTS
- 데이터에 있는 값으로만 답합니다.
- 개인정보 컬럼은 보여 주지 않습니다.

Instructions you must follow when providing summaries
- 숫자에는 단위와 기준 날짜를 붙입니다.
"""


# ── pure helpers ────────────────────────────────────────────────────────────

def snapshot_path(registry: str, agent_id: str, sha: str) -> str:
    catalog, schema = registry.split(".")
    return f"/Volumes/{catalog}/{schema}/{agent_id}/checks/{sha}.zip"


def production_folder(folder_path: str, agent_id: str) -> str:
    return f"{folder_path.rsplit('/', 1)[0]}/{PRODUCTION}/{agent_id}"


def genie_url(host: str, space_id: str | None) -> str | None:
    return f"{host.rstrip('/')}/genie/rooms/{space_id}" if space_id else None


def initial_space(tables: list[str], purpose: str | None) -> dict[str, Any]:
    """A first definition: the picked tables (Genie wants them sorted) and a text instruction with the standard sections."""
    text = TEXT_TEMPLATE.format(purpose=" ".join((purpose or "").split()) or "이 Genie가 답하는 질문을 적습니다.")
    return {"version": 2,
            "data_sources": {"tables": [{"identifier": t} for t in sorted(set(tables))]},
            "instructions": {"text_instructions": [{"id": now_id(), "content": [text]}]}}


def sources(definition: dict[str, Any]) -> set[str]:
    data = definition.get("data_sources") or {}
    return {x.get("identifier", "") for kind in ("tables", "metric_views") for x in (data.get(kind) or [])}


def upgrade(previous: dict[str, Any] | None, new: dict[str, Any]) -> str:
    """The change against production: initial (first release), major (data sources changed) or minor."""
    if previous is None:
        return "initial"
    return "major" if sources(previous) != sources(new) else "minor"


def diff(previous: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """What changed against production, for the request record and the approver."""
    def count(d: dict[str, Any] | None) -> dict[str, int]:
        d = d or {}
        ins = d.get("instructions") or {}
        return {"examples": len(ins.get("example_question_sqls") or []), "benchmarks": len((d.get("benchmarks") or {}).get("questions") or []),
                "joins": len(ins.get("join_specs") or []), "sample_questions": len((d.get("config") or {}).get("sample_questions") or [])}

    def text(d: dict[str, Any] | None) -> str:
        return "".join("".join(t.get("content") or []) for t in ((d or {}).get("instructions") or {}).get("text_instructions") or [])

    before, after = sources(previous or {}), sources(new)
    return {"sources_added": sorted(after - before), "sources_removed": sorted(before - after),
            "counts": {"before": count(previous) if previous is not None else None, "after": count(new)},
            "text_changed": text(previous) != text(new), "text_chars": len(text(new))}


def bundle(agent_id: str, spec_md: str, definition: dict[str, Any]) -> dict[str, bytes]:
    """The files of a snapshot (and of the version it becomes): SPEC.md, aops.toml and the definition."""
    return {"SPEC.md": spec_md.encode("utf-8"), "aops.toml": NO_CODE_TOML.format(name=agent_id).encode("utf-8"),
            SPACE: (json.dumps(definition, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")}


def definition_of(files: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(files[SPACE].decode("utf-8"))


def check_steps(files: dict[str, bytes], policy: dict[str, Any], format_step: dict[str, Any]) -> list[dict[str, Any]]:
    """The Gate on a snapshot: SPEC.md format, the definition rules and secrets (integrity and record come after)."""
    from .service import find_secrets

    no_code = policy.get("no_code") or {}
    try:
        definition = definition_of(files)
        step = genie_rules.format_step(definition, lint_min=float(no_code.get("lint_min", 0.9)), rules=no_code.get("rules"))
        rules = {**step, "name": "definition", "metrics": {"score": step["score"]}}
    except (KeyError, ValueError) as error:
        rules = {"name": "definition", "status": "failed", "detail": f"{SPACE}를 읽지 못했습니다 ({error})", "rules": []}
    leaks = find_secrets(files)
    return [format_step, rules, {"name": "secrets", "status": "failed" if leaks else "passed",
                                 "detail": "비밀값·금지 파일: " + ", ".join(leaks) if leaks else "비밀값·금지 파일이 없습니다"}]


def principal_of(principal: str) -> tuple[str, str]:
    """`group:<name>` · `user:<email>` · `sp:<application id>` → (ACL key, name). Without a prefix: a group."""
    kind, _, who = principal.partition(":") if ":" in principal else ("group", "", principal)
    return {"group": "group_name", "user": "user_name", "sp": "service_principal_name"}.get(kind, "group_name"), who.strip()


# ── service methods (mixed into Service) ────────────────────────────────────

class NoCodeMixin:
    """Service methods for no-code agents. They use the Service's workspace client, SQL and registry helpers."""

    w: Any
    sql: Any
    config: Any

    def genie_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self.sql.one(f"SELECT * FROM {self.sql.table('genie_agents')} WHERE agent_id = :a", {"a": agent_id})

    def _host(self) -> str:
        return self.w.config.host.rstrip("/")

    def no_code_links(self, agent_id: str) -> dict[str, Any]:
        """The places of a no-code agent: its folder, the dev space the author edits, the production space."""
        row = self.genie_agent(agent_id)
        if not row:
            raise ServiceError(404, "not_no_code", f"노코드 에이전트가 아닙니다: {agent_id}")
        folder_url = None
        try:
            folder_url = f"{self._host()}/browse/folders/{self.w.workspace.get_status(row['folder_path']).object_id}"
        except Exception:
            pass
        return {**row, "dev_url": genie_url(self._host(), row.get("dev_space_id")),
                "prod_url": genie_url(self._host(), row.get("prod_space_id")), "folder_url": folder_url}

    # ── create ──────────────────────────────────────────────────────────────

    def create_no_code(self, caller: Caller, body: dict[str, Any]) -> dict[str, Any]:
        """A new no-code agent: folder, SPEC.md, dev Genie space (with the author's own token), registry rows."""
        from databricks.sdk.service.workspace import ImportFormat

        from ..init import render_spec

        agent_id = (body.get("name") or "").strip()
        if not AGENT_ID.match(agent_id):
            raise ServiceError(400, "bad_agent_id", "에이전트 이름은 영문 소문자·숫자·하이픈 63자 이하입니다 (첫 글자는 영문 소문자·숫자)", "aops init")
        if self.agent(agent_id):
            raise ServiceError(409, "exists", f"이미 있는 에이전트입니다: {agent_id}", "aops init")
        description = " ".join((body.get("description") or "").split())
        if not description:
            raise ServiceError(400, "no_description", "설명(이 에이전트가 하는 일)을 적으세요", "aops init")
        crit, sens = body.get("service_criticality") or "medium", body.get("data_sensitivity") or "internal"
        if crit not in CRITICALITY or sens not in SENSITIVITY:
            raise ServiceError(400, "bad_spec", "중요도는 low·medium·high, 민감도는 public·internal·confidential·personal입니다", "aops init")
        source = (body.get("genie_space_id") or "").strip()
        mode = body.get("genie_mode") or ("copy" if source else "new")
        tables = [t.strip() for t in body.get("tables") or [] if t and t.strip()]
        if mode == "new":
            bad = [t for t in tables if not TABLE.match(t)]
            if not tables or bad or len(tables) > 30:
                raise ServiceError(400, "bad_tables", "테이블을 <catalog>.<schema>.<table> 형태로 1~30개 적으세요"
                                   + (f" (형태가 다른 것: {', '.join(bad[:3])})" if bad else ""), "aops init")
        elif mode in ("copy", "move"):
            if not SPACE_ID.match(source):
                raise ServiceError(400, "bad_space_id", "Genie 스페이스 ID는 32자리 16진수입니다 (주소의 /genie/rooms/<ID>)", "aops init")
        else:
            raise ServiceError(400, "bad_mode", "genie_mode는 new·copy·move 중 하나입니다", "aops init")
        if not caller.token:
            raise ServiceError(401, "no_user_token", "내 권한으로 Genie를 만들 토큰이 없습니다 (Databricks App으로 열거나 CLI 로그인)", "aops init")

        warehouse = (body.get("warehouse_id") or "").strip()
        definition = None
        if mode == "copy":
            source_space = self._user_genie(caller, "GET", f"/api/2.0/genie/spaces/{source}?include_serialized_space=true", None, "읽기")
            if not source_space.get("serialized_space"):
                raise ServiceError(409, "no_definition", "그 스페이스의 정의를 읽지 못했습니다. 내게 편집(CAN EDIT) 이상 권한이 있어야 복사할 수 있습니다", "aops init")
            definition = json.loads(source_space["serialized_space"])
            warehouse = warehouse or source_space.get("warehouse_id") or ""
        warehouse = warehouse or self.config.warehouse_id

        folder = self._agent_folder(agent_id)
        self._share_folder(folder, caller.email, body.get("owner_group"))
        spec = {"agent_id": agent_id, "name": (body.get("display_name") or "").strip() or agent_id, "build_type": "no_code",
                "owner": caller.email, "owner_group": (body.get("owner_group") or "").strip(), "service_criticality": crit,
                "data_sensitivity": sens, "security_level": (body.get("security_level") or "general").strip(),
                "consumer_group": (body.get("consumer_group") or "").strip(), "description": description}
        notes = ("이 폴더의 Genie(개발용)를 고친 뒤 aops console에서 배포를 요청합니다. 배포 요청 때 이 파일과 개발용 정의를 "
                 "함께 스냅샷으로 떠서 검사하고, 승인되면 같은 내용이 운영용 Genie로 나갑니다. 위 항목은 여기서 고칠 수 있습니다.")
        self.w.workspace.upload(f"{folder}/SPEC.md", io.BytesIO(render_spec(spec, notes).encode("utf-8")),
                                format=ImportFormat.AUTO, overwrite=True)

        title = f"{spec['name']} (개발)"
        if mode == "move":
            self._user_genie(caller, "PATCH", f"/api/2.0/genie/spaces/{source}", {"parent_path": folder}, "옮기기")
            space_id, made = source, False
        else:
            payload = {"warehouse_id": warehouse, "title": title, "description": description, "parent_path": folder,
                       "serialized_space": json.dumps(definition or initial_space(tables, description), ensure_ascii=False)}
            space_id, made = self._user_genie(caller, "POST", "/api/2.0/genie/spaces", payload, "만들기")["space_id"], True

        warnings = []
        if not self._sp_reads(space_id):
            warnings.append("aops 서비스 주체가 개발용 정의를 아직 읽지 못합니다. 폴더 권한이 전해지는 데 시간이 걸릴 수 있습니다. "
                            "배포 요청 전에 [점검]으로 확인하세요")
        try:
            self.sql.atomic([
                (f"INSERT INTO {self.sql.table('agents')} VALUES (:id, :name, :desc, 'no_code', :owner, :group, :crit, :sens, :sec, "
                 "'registered', parse_json(:spec), :by, current_timestamp(), current_timestamp())",
                 {"id": agent_id, "name": spec["name"], "desc": description, "owner": caller.email, "group": spec["owner_group"] or None,
                  "crit": crit, "sens": sens, "sec": spec["security_level"], "spec": spec, "by": caller.email}),
                (f"INSERT INTO {self.sql.table('genie_agents')} VALUES (:id, :dev, NULL, :folder, :consumer, :wh, current_timestamp())",
                 {"id": agent_id, "dev": space_id, "folder": folder, "consumer": spec["consumer_group"] or None, "wh": warehouse}),
                self._event_statement(agent_id, None, "create", caller.email,
                                      {"folder": folder, "dev_space_id": space_id, "genie_mode": mode, "source_space_id": source or None,
                                       "tables": tables}),
            ])
        except Exception:
            if made:
                try:
                    self.w.genie.trash_space(space_id)  # the server can: the space is in its folder
                except Exception:
                    pass
            raise
        return {"agent_id": agent_id, "type": "no_code", "folder_path": folder, "dev_space_id": space_id, "genie_mode": mode,
                "warehouse_id": warehouse, "warnings": warnings, **{k: v for k, v in self.no_code_links(agent_id).items()
                                                                    if k in ("dev_url", "folder_url")}}

    def _user_genie(self, caller: Caller, method: str, path: str, body: dict[str, Any] | None, what: str) -> dict[str, Any]:
        """A Genie call with the author's own token: their rights on the tables, warehouse and spaces apply.

        There is no fallback to the service principal: a space it made would carry sample values of tables the
        author may not be allowed to read.
        """
        result = self._as_user(caller.token, method, path, body)
        if result["status"] == 200:
            return result["body"] or {}
        message = result.get("error") or ""
        if result["status"] in (401, 403) and "scope" in message.lower():
            raise ServiceError(403, "missing_scope", "aops 앱에 genie 사용자 권한 범위가 없어 내 권한으로 Genie를 다룰 수 없습니다. "
                               f"관리자가 aops admin setup --only app을 다시 실행하면 더해집니다 (그 뒤 화면을 새로 열어 동의). 응답: {message[:200]}",
                               "aops admin setup --only app")
        raise ServiceError(result["status"] if 400 <= result["status"] < 500 else 502, "genie_failed",
                           f"Genie {what}에 실패했습니다: {message}", "aops init")

    def _service_user(self) -> str:
        if not getattr(self, "_me", None):
            self._me = self.w.current_user.me().user_name
        return self._me

    def _agent_folder(self, agent_id: str) -> str:
        """<root>/<agent_id>, made by the service principal. Without rights on the shared root: its home folder."""
        for root in (ROOT, f"/Workspace/Users/{self._service_user()}/aops/no-code"):
            path = f"{root}/{agent_id}"
            try:
                self.w.workspace.mkdirs(path)
                return path
            except Exception as error:
                if "permission" not in str(error).lower() and "denied" not in str(error).lower():
                    raise
        raise ServiceError(500, "no_folder", "에이전트 폴더를 만들 곳이 없습니다", "aops admin setup --only grants")

    def _share_folder(self, folder: str, email: str, group: str | None) -> None:
        from databricks.sdk.service.workspace import WorkspaceObjectAccessControlRequest, WorkspaceObjectPermissionLevel

        acl = [WorkspaceObjectAccessControlRequest(user_name=email, permission_level=WorkspaceObjectPermissionLevel.CAN_MANAGE)]
        if group and group.strip():
            acl.append(WorkspaceObjectAccessControlRequest(group_name=group.strip(), permission_level=WorkspaceObjectPermissionLevel.CAN_MANAGE))
        status = self.w.workspace.get_status(folder)
        self.w.workspace.update_permissions("directories", str(status.object_id), access_control_list=acl)

    def _sp_reads(self, space_id: str) -> bool:
        try:
            return bool(self.w.genie.get_space(space_id, include_serialized_space=True).serialized_space)
        except Exception:
            return False

    # ── deploy request ──────────────────────────────────────────────────────

    def _dev_definition(self, genie: dict[str, Any]) -> dict[str, Any]:
        try:
            space = self.w.genie.get_space(genie["dev_space_id"], include_serialized_space=True)
        except Exception as error:
            raise ServiceError(409, "space_not_readable", f"개발용 Genie를 읽지 못했습니다 ({type(error).__name__}). "
                               "aops 에이전트 폴더 안에 있어야 서비스 주체가 읽습니다", "aops deploy") from error
        if not space.serialized_space:
            raise ServiceError(409, "no_definition", "개발용 Genie 정의가 빈 응답입니다. aops 서비스 주체에 편집(CAN EDIT) 권한이 필요합니다", "aops deploy")
        return json.loads(space.serialized_space)

    def _folder_spec(self, genie: dict[str, Any], agent: dict[str, Any]) -> str:
        """SPEC.md from the agent folder (the author edits it there); the registry's SPEC if it is gone."""
        from ..init import render_spec
        from .sql import loads

        try:
            return self.w.workspace.download(f"{genie['folder_path']}/SPEC.md").read().decode("utf-8")
        except Exception:
            return render_spec(loads(agent.get("spec")) or {"agent_id": agent["agent_id"], "build_type": "no_code"})

    def request_no_code_deploy(self, caller: Caller, agent: dict[str, Any], reason: str | None) -> dict[str, Any]:
        """Snapshot SPEC.md and the dev definition, then the usual Gate: check → approval → deploy Job."""
        import threading

        from .service import next_version

        agent_id = agent["agent_id"]
        genie = self.genie_agent(agent_id)
        if not genie or not genie.get("dev_space_id"):
            raise ServiceError(409, "not_linked", "개발용 Genie가 연결되지 않은 노코드 에이전트입니다", "aops init")
        definition = self._dev_definition(genie)
        files = bundle(agent_id, self._folder_spec(genie, agent), definition)
        sha = package.files_sha256(list(files.items()))
        versions = self.versions(agent_id)
        production = self.production(agent_id)
        same = next((v for v in versions if v["source_sha256"] == sha), None)
        if same:
            if production and production["current_version"] == same["version"]:
                raise ServiceError(409, "unchanged", f"운영 버전({same['version']})과 정의·SPEC.md가 같습니다. 개발용 Genie를 고친 뒤 요청하세요", "aops deploy")
            return self.request_deploy(caller, agent_id, same["version"], reason)  # an earlier version again
        open_gate = self._open_gate(agent_id)
        if open_gate:
            if (self.gate_snapshot(open_gate["gate_id"]) or {}).get("snapshot_sha256") == sha:
                return self.advance(open_gate["gate_id"])  # the same request again: keep following it
            raise ServiceError(409, "gate_open", f"진행 중인 배포 요청이 있습니다 ({open_gate['version']}, {open_gate['gate_id']})", "aops deploy")

        previous = None
        if production:
            row = self.version(agent_id, production["current_version"])
            previous = definition_of(package.read(self.w.files.download(row["artifact_path"]).contents.read()))
        change = upgrade(previous, definition)
        proposed = next_version([v["version"] for v in versions], "major" if change == "major" else None)
        path = snapshot_path(self.config.registry, agent_id, sha)
        self.sql.run(f"CREATE VOLUME IF NOT EXISTS {self.config.registry}.`{agent_id}` "
                     f"COMMENT 'aops 에이전트 {agent_id}의 버전 zip·검사 스냅샷. 쓰기는 aops 서비스 주체만'")
        data = package.build_entries([(rel, content, 0o644) for rel, content in sorted(files.items())]).data
        try:
            self.w.files.upload(path, io.BytesIO(data), overwrite=False)
        except Exception as error:
            if "exist" not in str(error).lower():
                raise  # the same snapshot was taken before: same name, same content

        policy_version, policy = self.policy()
        gate_id = now_id()
        detail = {"gate_id": gate_id, "upgrade_type": change, "snapshot_sha256": sha, "snapshot_path": path,
                  "definition_sha256": genie_rules.definition_hash(definition), "dev_space_id": genie["dev_space_id"],
                  "diff": diff(previous, definition)}
        self.sql.atomic([
            (f"INSERT INTO {self.sql.table('release_gates')} VALUES (:g, :a, :v, :by, :why, current_timestamp(), :up, :p, :vm, NULL, "
             ":am, :ch, :n, NULL, NULL, 'validating', NULL, NULL, current_timestamp())",
             {"g": gate_id, "a": agent_id, "v": proposed, "by": caller.email, "why": reason, "up": change,
              "p": policy_version, "vm": policy["validation_mode"], "am": policy["approval_mode"],
              "ch": policy["approval_channel"], "n": int(policy["required_approvals"])}),
            self._event_statement(agent_id, proposed, "gate_requested", caller.email, detail),
        ])
        passed = self.sql.one(f"SELECT validation_run_id FROM {self.sql.table('validation_runs')} WHERE agent_id = :a AND source_sha256 = :sha "
                              "AND policy_version = :p AND status = 'passed' AND mode = 'job' ORDER BY started_at DESC LIMIT 1",
                              {"a": agent_id, "sha": sha, "p": policy_version})
        if policy["validation_mode"] == "exempt" or passed:
            self._after_gate(gate_id, passed["validation_run_id"] if passed else None)
        else:
            run = self._start_run(caller, agent_id, proposed, sha)
            self._set_gate(gate_id, validation_run_id=run["validation_run_id"])
            threading.Thread(target=self._follow, args=(gate_id,), daemon=True).start()
        return self.gate(gate_id)

    def gate_snapshot(self, gate_id: str) -> dict[str, Any] | None:
        """The snapshot a no-code request was made with (kept in its gate_requested record)."""
        from .sql import loads

        row = self.sql.one(f"SELECT detail FROM {self.sql.table('agent_events')} WHERE event_type = 'gate_requested' "
                           "AND detail:gate_id::string = :g LIMIT 1", {"g": gate_id})
        detail = loads(row.get("detail")) if row else None
        return detail if detail and detail.get("snapshot_sha256") else None

    def integrity_step(self, agent_id: str, files: dict[str, bytes]) -> dict[str, Any]:
        """At the end of the Gate: the dev definition still equals the snapshot (otherwise the run is stale)."""
        genie = self.genie_agent(agent_id) or {}
        try:
            now = genie_rules.definition_hash(self._dev_definition(genie))
        except ServiceError as error:
            return {"name": "integrity", "status": "failed", "detail": f"개발용 정의를 다시 읽지 못했습니다: {error.message}"}
        same = now == genie_rules.definition_hash(definition_of(files))
        return {"name": "integrity", "status": "passed" if same else "failed",
                "detail": "검사하는 동안 개발용 정의가 바뀌지 않았습니다" if same
                else "검사하는 동안 개발용 정의가 바뀌었습니다 (stale). 다 고친 뒤 다시 배포를 요청하세요"}

    # ── deploy (in the deploy Job, as the service principal) ────────────────

    def run_no_code_deploy(self, gate: dict[str, Any]) -> dict[str, Any]:
        """The approved snapshot (or an earlier version) into the production space, then the version and the pointer."""
        from ..package import front_matter

        gate_id, deployment_id = gate["gate_id"], gate["deployment_id"]
        agent_id, version = gate["agent_id"], gate["version"]
        try:
            if gate["decision"] != "approved":
                raise RuntimeError(f"승인되지 않은 요청입니다 ({gate['decision']})")
            agent = self._agent_or_404(agent_id)
            genie = self.genie_agent(agent_id)
            if not genie:
                raise RuntimeError("genie_agents 행이 없습니다")
            row = self.version(agent_id, version)
            if row:  # an earlier version again (rollback)
                path, sha, new = row["artifact_path"], row["source_sha256"], False
            else:
                snap = self.gate_snapshot(gate_id)
                if not snap:
                    raise RuntimeError("이 요청의 스냅샷 기록이 없습니다")
                path, sha, new = snap["snapshot_path"], snap["snapshot_sha256"], True
            data = self.w.files.download(path).contents.read()
            files = package.read(data)
            if package.files_sha256(list(files.items())) != sha:
                raise RuntimeError("스냅샷이 배포 요청 때 해시와 다릅니다" if new else "버전 zip이 등록 때 해시와 다릅니다")
            definition = definition_of(files)
            spec = front_matter(files["SPEC.md"].decode("utf-8"))
            prod_id, action = self._put_production_space(agent_id, genie, definition, spec)
            live = self.w.genie.get_space(prod_id, include_serialized_space=True).serialized_space
            match = bool(live) and genie_rules.definition_hash(json.loads(live)) == genie_rules.definition_hash(definition)
            consumer = (spec.get("consumer_group") or genie.get("consumer_group") or "").strip()
            principals = [f"user:{agent['owner']}"] + [f"group:{g}" for g in dict.fromkeys(
                x for x in (agent.get("owner_group"), consumer) if x)]
            grants = self._grant_run(prod_id, principals)
            artifact = path
            if new:  # the version number is given now, when the snapshot reaches production
                artifact = f"/Volumes/{self.config.catalog}/{self.config.schema}/{agent_id}/{version}.zip"
                self._write_once(artifact, data, sha)
            production = self.production(agent_id)
            previous = production["current_version"] if production else None
            kind = "rollback" if gate["upgrade_type"] == "rollback" else "deploy"
            url = genie_url(self._host(), prod_id)
            statements: list[tuple[str, dict[str, Any]]] = [
                (f"UPDATE {self.sql.table('agent_versions')} SET status = 'retired' WHERE agent_id = :a AND status = 'production'", {"a": agent_id})]
            if new:
                statements += [
                    (f"INSERT INTO {self.sql.table('agent_versions')} VALUES (:id, :v, 'production', :sha, :path, :size, NULL, NULL, NULL, "
                     "parse_json(:spec), :by, current_timestamp())",
                     {"id": agent_id, "v": version, "sha": sha, "path": artifact, "size": len(data), "spec": spec, "by": gate["requester"]}),
                    (f"UPDATE {self.sql.table('agents')} SET name = :name, description = :desc, owner_group = :group, "
                     "service_criticality = :crit, data_sensitivity = :sens, security_level = :sec, spec = parse_json(:spec), "
                     "updated_at = current_timestamp() WHERE agent_id = :a",
                     {"a": agent_id, "name": spec.get("name") or agent_id, "desc": spec.get("description"), "group": spec.get("owner_group") or None,
                      "crit": spec.get("service_criticality") if spec.get("service_criticality") in CRITICALITY else agent["service_criticality"],
                      "sens": spec.get("data_sensitivity") if spec.get("data_sensitivity") in SENSITIVITY else agent["data_sensitivity"],
                      "sec": spec.get("security_level") or agent["security_level"], "spec": spec}),
                    self._event_statement(agent_id, version, "register", gate["requester"],
                                          {"artifact_path": artifact, "from_snapshot": path, "gate_id": gate_id, "first": not previous}),
                ]
            else:
                statements.append((f"UPDATE {self.sql.table('agent_versions')} SET status = 'production' WHERE agent_id = :a AND version = :v",
                                   {"a": agent_id, "v": version}))
            statements += [
                (f"MERGE INTO {self.sql.table('agent_production')} t USING (SELECT :a AS agent_id) s ON t.agent_id = s.agent_id "
                 "WHEN MATCHED THEN UPDATE SET previous_version = t.current_version, current_version = :v, target = :t, updated_by = :by, "
                 "updated_at = current_timestamp() WHEN NOT MATCHED THEN INSERT (agent_id, current_version, previous_version, target, updated_by, updated_at) "
                 "VALUES (:a, :v, NULL, :t, :by, current_timestamp())",
                 {"a": agent_id, "v": version, "t": prod_id, "by": gate["requester"]}),
                (f"UPDATE {self.sql.table('agents')} SET lifecycle = 'production', updated_at = current_timestamp() WHERE agent_id = :a", {"a": agent_id}),
                (f"UPDATE {self.sql.table('genie_agents')} SET prod_space_id = :p, consumer_group = :c, updated_at = current_timestamp() WHERE agent_id = :a",
                 {"a": agent_id, "p": prod_id, "c": consumer or None}),
                (f"UPDATE {self.sql.table('deployments')} SET status = 'succeeded', target = :t, app_url = :u, detail = parse_json(:d), "
                 "ended_at = current_timestamp() WHERE deployment_id = :id",
                 {"t": prod_id, "u": url, "d": {"space_action": action, "definition_match": match, "grants": grants}, "id": deployment_id}),
                self._event_statement(agent_id, version, kind, gate["requester"],
                                      {"gate_id": gate_id, "previous": previous, "space_id": prod_id, "space_action": action}),
            ]
            statements += [self._event_statement(agent_id, version, "promote", "policy:deploy",
                                                 {"principal": g["principal"], "permission": "CAN_RUN", "granted": True, "space_id": prod_id})
                           for g in grants if g["granted"]]
            self.sql.atomic(statements)
            report = {"status": "succeeded", "agent_id": agent_id, "version": version, "previous": previous, "space_id": prod_id,
                      "space_url": url, "space_action": action, "definition_match": match, "grants": grants, "artifact_path": artifact,
                      "approval_mode": gate.get("approval_mode"), "requester": gate["requester"], "skills": [], "not_in_version": []}
        except Exception as error:
            self.sql.run(f"UPDATE {self.sql.table('deployments')} SET status = 'failed', detail = parse_json(:d), ended_at = current_timestamp() "
                         "WHERE deployment_id = :id", {"d": {"error": f"{type(error).__name__}: {error}"}, "id": deployment_id})
            raise
        report["git"] = self.git_production(agent_id, version, gate["requester"])
        return report

    def _put_production_space(self, agent_id: str, genie: dict[str, Any], definition: dict[str, Any],
                              spec: dict[str, str]) -> tuple[str, str]:
        """Update the production space (its id and history stay), or make it in the production folder."""
        serialized = json.dumps(definition, ensure_ascii=False)
        title, description = spec.get("name") or agent_id, spec.get("description") or None
        warehouse = genie.get("warehouse_id") or self.config.warehouse_id
        if genie.get("prod_space_id"):
            try:
                self.w.genie.update_space(genie["prod_space_id"], serialized_space=serialized, title=title,
                                          description=description, warehouse_id=warehouse)
                return genie["prod_space_id"], "updated"
            except Exception as error:
                if not any(word in str(error).lower() for word in ("not found", "does not exist", "not_found")):
                    raise
        folder = production_folder(genie["folder_path"], agent_id)
        self.w.workspace.mkdirs(folder)
        space = self.w.genie.create_space(warehouse, serialized, title=title, description=description, parent_path=folder)
        # kept right away: if a later step fails, the next deploy updates this space instead of making another
        self.sql.run(f"UPDATE {self.sql.table('genie_agents')} SET prod_space_id = :p, updated_at = current_timestamp() WHERE agent_id = :a",
                     {"a": agent_id, "p": space.space_id})
        return space.space_id, "created"

    def _write_once(self, path: str, data: bytes, sha: str) -> None:
        try:
            self.w.files.upload(path, io.BytesIO(data), overwrite=False)
        except Exception as error:
            if "exist" not in str(error).lower():
                raise
            # written by an earlier attempt of this deploy: fine only if it is the same content
            if package.files_sha256(list(package.read(self.w.files.download(path).contents.read()).items())) != sha:
                raise RuntimeError(f"{path}가 이미 있고 내용이 다릅니다") from error

    def _grant_run(self, space_id: str, principals: list[str]) -> list[dict[str, Any]]:
        """CAN RUN on the production space for each principal; a failure is reported, not raised (aops promote retries)."""
        from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

        results = []
        for principal in principals:
            key, who = principal_of(principal)
            try:
                self.w.permissions.update("genie", space_id, access_control_list=[
                    AccessControlRequest(**{key: who}, permission_level=PermissionLevel.CAN_RUN)])
                results.append({"principal": principal, "granted": True})
            except Exception as error:
                results.append({"principal": principal, "granted": False, "error": f"{type(error).__name__}: {error}"[:200]})
        return results

    # ── promote, load ───────────────────────────────────────────────────────

    def promote_genie(self, caller: Caller, agent_id: str, production: dict[str, Any], principals: list[str],
                      revoke: bool) -> list[dict[str, Any]]:
        """CAN RUN on the production Genie space (Genie checks it on every question)."""
        from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

        space_id = production["target"]
        grants = []
        for principal in principals:
            key, who = principal_of(principal)
            if revoke:
                current = self.w.permissions.get("genie", space_id).access_control_list or []
                keep = [AccessControlRequest(**{k: getattr(a, k) for k in ("group_name", "user_name", "service_principal_name") if getattr(a, k)},
                                             permission_level=PermissionLevel(p.permission_level.value))
                        for a in current if getattr(a, key) != who for p in (a.all_permissions or [])[:1] if not p.inherited]
                self.w.permissions.set("genie", space_id, access_control_list=keep)
            else:
                self.w.permissions.update("genie", space_id, access_control_list=[
                    AccessControlRequest(**{key: who}, permission_level=PermissionLevel.CAN_RUN)])
            grants.append({"agent_id": agent_id, "securable": f"genie:{space_id}", "principal": principal, "permission": "CAN_RUN",
                           "granted": not revoke})
            self.event(agent_id, production["current_version"], "promote", caller.email,
                       {"principal": principal, "permission": "CAN_RUN", "granted": not revoke, "space_id": space_id})
        return grants

    def load_genie(self, caller: Caller, agent_id: str) -> dict[str, Any]:
        production = self.production(agent_id)
        if not production:
            raise ServiceError(409, "no_production", "운영 버전이 없습니다. 먼저 배포를 요청하세요", "aops load")
        self.event(agent_id, production["current_version"], "load", caller.email, {"space_id": production["target"]})
        return {"agent_id": agent_id, "build_type": "no_code", "version": production["current_version"],
                "link": genie_url(self._host(), production["target"]), "allowed": True, "archive": None}
