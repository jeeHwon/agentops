import io
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("databricks.sdk")  # the dbx extra

from aops import package  # noqa: E402
from aops.dbx import genie_rules, nocode  # noqa: E402
from aops.dbx.service import DEFAULT_POLICY, Caller, Config, Service, ServiceError  # noqa: E402
from aops.init import render_spec  # noqa: E402

SPEC = render_spec({"agent_id": "sales", "name": "매출", "build_type": "no_code", "owner": "dev@x.com", "service_criticality": "low",
                    "data_sensitivity": "internal", "security_level": "general", "consumer_group": "analysts",
                    "description": "지역별 매출에 답한다"})
DEFINITION = nocode.initial_space(["main.s.orders", "main.s.customers"], "매출 질문")


def test_render_spec_reads_back_and_drops_hints():
    spec = package.front_matter(SPEC)
    assert spec["build_type"] == "no_code" and spec["service_criticality"] == "low" and spec["consumer_group"] == "analysts"
    assert spec["owner_group"] == "" and spec["description"] == "지역별 매출에 답한다"
    skill = package.front_matter(render_spec({"agent_id": "a", "build_type": "skill", "description": "x # y"}))
    assert "consumer_group" not in skill and skill["description"] == "x ＃ y"  # " #" would cut the value


def test_initial_space_is_sorted_and_has_the_standard_sections():
    assert [t["identifier"] for t in DEFINITION["data_sources"]["tables"]] == ["main.s.customers", "main.s.orders"]
    text = next(r for r in genie_rules.lint(DEFINITION)["results"] if r["id"] == "text.sections")
    assert text["passed"]


def test_upgrade_is_major_only_when_data_sources_change():
    assert nocode.upgrade(None, DEFINITION) == "initial"
    more = json.loads(json.dumps(DEFINITION))
    more["instructions"]["example_question_sqls"] = [{"id": "1" * 32, "question": ["q"], "sql": ["select 1"]}]
    assert nocode.upgrade(DEFINITION, more) == "minor"
    other = nocode.initial_space(["main.s.orders"], "매출 질문")
    assert nocode.upgrade(DEFINITION, other) == "major"
    change = nocode.diff(DEFINITION, other)
    assert change["sources_removed"] == ["main.s.customers"] and change["sources_added"] == []


def test_bundle_hash_is_stable_and_steps_cover_the_snapshot():
    files = nocode.bundle("sales", SPEC, DEFINITION)
    assert package.files_sha256(list(files.items())) == package.files_sha256(list(nocode.bundle("sales", SPEC, DEFINITION).items()))
    from aops.dbx.service import spec_step

    steps = nocode.check_steps(files, DEFAULT_POLICY, spec_step(files, "no_code"))
    assert [s["name"] for s in steps] == ["format", "definition", "secrets"]
    assert steps[0]["status"] == "passed" and steps[1]["status"] == "failed"  # no examples, no benchmarks yet
    assert any(r["id"] == "examples.count" and not r["passed"] for r in steps[1]["rules"])
    wrong = spec_step({"SPEC.md": SPEC.replace("build_type: no_code", "build_type: skill").encode()}, "no_code")
    assert wrong["status"] == "failed" and "no_code" in wrong["detail"]


def test_principal_prefixes():
    assert nocode.principal_of("group:analysts") == ("group_name", "analysts")
    assert nocode.principal_of("user:kim@x.com") == ("user_name", "kim@x.com")
    assert nocode.principal_of("sp:1234") == ("service_principal_name", "1234")
    assert nocode.principal_of("analysts") == ("group_name", "analysts")


# ── service flows with fakes ────────────────────────────────────────────────

class Sql:
    def __init__(self, answers=None):
        self.statements, self.atomics, self.answers = [], [], answers or {}

    def table(self, name):
        return f"c.s.{name}"

    def run(self, statement, params=None):
        self.statements.append((statement, params or {}))
        return []

    def update(self, statement, params=None):
        self.statements.append((statement, params or {}))
        return 1

    def one(self, statement, params=None):
        for key, value in self.answers.items():
            if key in statement:
                return value
        return None

    def atomic(self, statements):
        self.atomics.append(list(statements))


class Files:
    def __init__(self):
        self.store = {}

    def upload(self, path, data, overwrite=False):
        if path in self.store and not overwrite:
            raise RuntimeError("file already exists")
        self.store[path] = data.read()

    def download(self, path):
        return SimpleNamespace(contents=io.BytesIO(self.store[path]))


def service(sql=None, genie=None, workspace=None, files=None, permissions=None):
    svc = object.__new__(Service)
    svc.config = Config("c.s", "wh1")
    svc.sql = sql or Sql()
    svc.w = SimpleNamespace(config=SimpleNamespace(host="https://ws.example.com"), genie=genie, workspace=workspace,
                            files=files or Files(), permissions=permissions,
                            current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp-1")))
    svc.policy = lambda: (3, dict(DEFAULT_POLICY))
    svc.events = []
    svc.event = lambda *args: svc.events.append(args)
    svc._event_statement = lambda agent_id, version, kind, actor, detail=None: (f"EVENT {kind}", {"v": version, "d": detail})
    return svc


AGENT = {"agent_id": "sales", "owner": "dev@x.com", "owner_group": None, "build_type": "no_code", "spec": None,
         "service_criticality": "low", "data_sensitivity": "internal", "security_level": "general"}
GENIE = {"agent_id": "sales", "dev_space_id": "d" * 32, "prod_space_id": None, "folder_path": "/Workspace/aops/no-code/sales",
         "consumer_group": "analysts", "warehouse_id": "wh1"}


def genie_api(definition, calls):
    return SimpleNamespace(
        get_space=lambda space_id, include_serialized_space=False: SimpleNamespace(serialized_space=json.dumps(definition)),
        create_space=lambda wh, ser, title=None, description=None, parent_path=None: (
            calls.append(("create", wh, title, parent_path)), SimpleNamespace(space_id="p" * 32))[1],
        update_space=lambda space_id, **kw: calls.append(("update", space_id, kw.get("title"))))


def test_first_request_snapshots_proposes_1_0_0_and_starts_the_check():
    workspace = SimpleNamespace(download=lambda path: io.BytesIO(SPEC.encode()))
    svc = service(sql=Sql({"genie_agents": GENIE}), genie=genie_api(DEFINITION, []), workspace=workspace)
    svc.versions, svc.production, svc._open_gate = (lambda a: []), (lambda a: None), (lambda a: None)
    started = []
    svc._start_run = lambda caller, agent_id, version, sha: started.append((version, sha)) or {"validation_run_id": "r1"}
    svc._set_gate = lambda gate_id, **values: None
    svc._follow = lambda gate_id: None
    svc.gate = lambda gate_id: {"gate_id": gate_id}
    svc.request_no_code_deploy(Caller("dev@x.com"), AGENT, "첫 공개")
    gate_sql, event = svc.sql.atomics[0]
    assert gate_sql[1]["v"] == "1.0.0" and gate_sql[1]["up"] == "initial"
    sha = event[1]["d"]["snapshot_sha256"]
    assert started == [("1.0.0", sha)]
    path = nocode.snapshot_path("c.s", "sales", sha)
    assert event[1]["d"]["snapshot_path"] == path and path in svc.w.files.store
    assert package.files_sha256(list(package.read(svc.w.files.store[path]).items())) == sha


def test_request_equal_to_production_is_refused_and_an_older_version_is_redeployed():
    files = nocode.bundle("sales", SPEC, DEFINITION)
    sha = package.files_sha256(list(files.items()))
    workspace = SimpleNamespace(download=lambda path: io.BytesIO(SPEC.encode()))
    svc = service(sql=Sql({"genie_agents": GENIE}), genie=genie_api(DEFINITION, []), workspace=workspace)
    svc.versions = lambda a: [{"version": "1.0.0", "source_sha256": sha}, {"version": "1.1.0", "source_sha256": "other"}]
    svc.production = lambda a: {"current_version": "1.0.0"}
    with pytest.raises(ServiceError) as error:
        svc.request_no_code_deploy(Caller("dev@x.com"), AGENT, None)
    assert error.value.code == "unchanged"
    svc.production = lambda a: {"current_version": "1.1.0"}
    asked = []
    svc.request_deploy = lambda caller, agent_id, version, reason: asked.append(version)
    svc.request_no_code_deploy(Caller("dev@x.com"), AGENT, None)
    assert asked == ["1.0.0"]  # the definition went back to 1.0.0: that version again (a rollback)


def test_deploy_makes_the_production_space_registers_the_version_and_grants_run():
    files = nocode.bundle("sales", SPEC, DEFINITION)
    sha = package.files_sha256(list(files.items()))
    data = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    store = Files()
    snap = nocode.snapshot_path("c.s", "sales", sha)
    store.store[snap] = data
    calls, grants = [], []
    permissions = SimpleNamespace(update=lambda kind, space_id, access_control_list: grants.append(
        (kind, space_id, access_control_list[0].as_dict())))
    svc = service(sql=Sql({"genie_agents": GENIE}), genie=genie_api(DEFINITION, calls),
                  workspace=SimpleNamespace(mkdirs=lambda path: calls.append(("mkdirs", path))), files=store, permissions=permissions)
    svc._agent_or_404 = lambda a: AGENT
    svc.version = lambda a, v: None
    svc.production = lambda a: None
    svc.gate_snapshot = lambda gate_id: {"snapshot_path": snap, "snapshot_sha256": sha}
    svc.git_production = lambda *args: None
    gate = {"gate_id": "g1", "deployment_id": "d1", "agent_id": "sales", "version": "1.0.0", "decision": "approved",
            "upgrade_type": "initial", "requester": "dev@x.com", "approval_mode": "auto"}
    report = svc.run_no_code_deploy(gate)
    assert ("mkdirs", "/Workspace/aops/no-code/_production/sales") in calls
    assert ("create", "wh1", "매출", "/Workspace/aops/no-code/_production/sales") in calls
    assert report["space_id"] == "p" * 32 and report["definition_match"] and report["space_action"] == "created"
    assert [g[2] for g in grants] == [{"permission_level": "CAN_RUN", "user_name": "dev@x.com"},
                                      {"permission_level": "CAN_RUN", "group_name": "analysts"}]
    assert "/Volumes/c/s/sales/1.0.0.zip" in store.store
    statements = [s for s, _ in svc.sql.atomics[0]]
    assert any(s.startswith("INSERT INTO c.s.agent_versions") for s in statements)
    assert any("UPDATE c.s.genie_agents SET prod_space_id" in s for s in statements)
    assert "EVENT register" in statements and "EVENT deploy" in statements
    assert any("UPDATE c.s.genie_agents SET prod_space_id" in s for s, _ in svc.sql.statements)  # kept right after creation


def test_rollback_updates_the_same_space_without_a_new_version():
    files = nocode.bundle("sales", SPEC, DEFINITION)
    sha = package.files_sha256(list(files.items()))
    store = Files()
    store.store["/Volumes/c/s/sales/1.0.0.zip"] = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    calls = []
    svc = service(sql=Sql({"genie_agents": {**GENIE, "prod_space_id": "p" * 32}}), genie=genie_api(DEFINITION, calls), files=store,
                  permissions=SimpleNamespace(update=lambda *a, **k: None))
    svc._agent_or_404 = lambda a: AGENT
    svc.version = lambda a, v: {"version": "1.0.0", "artifact_path": "/Volumes/c/s/sales/1.0.0.zip", "source_sha256": sha}
    svc.production = lambda a: {"current_version": "1.1.0"}
    svc.git_production = lambda *args: None
    gate = {"gate_id": "g2", "deployment_id": "d2", "agent_id": "sales", "version": "1.0.0", "decision": "approved",
            "upgrade_type": "rollback", "requester": "dev@x.com"}
    report = svc.run_no_code_deploy(gate)
    assert calls == [("update", "p" * 32, "매출")] and report["space_action"] == "updated"
    statements = [s for s, _ in svc.sql.atomics[0]]
    assert not any(s.startswith("INSERT INTO c.s.agent_versions") for s in statements) and "EVENT rollback" in statements


def test_dev_space_is_made_with_the_authors_token_only():
    svc = service()
    svc._as_user = lambda token, method, path, body=None: {"status": 403, "body": None,
                                                           "error": "Provided OAuth token does not have required scopes: genie"}
    with pytest.raises(ServiceError) as error:
        svc._user_genie(Caller("dev@x.com", "t"), "POST", "/api/2.0/genie/spaces", {}, "만들기")
    assert error.value.code == "missing_scope" and "admin setup --only app" in error.value.message
    svc._as_user = lambda token, method, path, body=None: {"status": 403, "body": None, "error": "no SELECT on main.s.orders"}
    with pytest.raises(ServiceError) as error:
        svc._user_genie(Caller("dev@x.com", "t"), "POST", "/api/2.0/genie/spaces", {}, "만들기")
    assert error.value.code == "genie_failed" and error.value.status == 403  # the author's own rights: no fallback


def test_create_writes_folder_spec_space_and_rows():
    calls = []
    workspace = SimpleNamespace(
        mkdirs=lambda path: calls.append(("mkdirs", path)),
        get_status=lambda path: SimpleNamespace(object_id=42),
        update_permissions=lambda kind, object_id, access_control_list: calls.append(("share", [a.as_dict() for a in access_control_list])),
        upload=lambda path, data, format=None, overwrite=False: calls.append(("upload", path, data.read().decode())))
    svc = service(genie=SimpleNamespace(get_space=lambda *a, **k: SimpleNamespace(serialized_space="{}")), workspace=workspace)
    svc.agent = lambda a: None
    sent = []
    svc._as_user = lambda token, method, path, body=None: sent.append((method, path, body)) or {"status": 200, "body": {"space_id": "n" * 32}}
    svc.genie_agent = lambda a: {**GENIE, "dev_space_id": "n" * 32}
    with pytest.raises(ServiceError):
        svc.create_no_code(Caller("dev@x.com", "t"), {"name": "sales", "description": "매출", "tables": ["orders"]})
    row = svc.create_no_code(Caller("dev@x.com", "t"), {"name": "sales", "display_name": "매출", "description": "지역별 매출",
                                                         "tables": ["main.s.orders"], "owner_group": "team", "consumer_group": "analysts"})
    assert ("mkdirs", "/Workspace/aops/no-code/sales") in calls
    share = next(c for c in calls if c[0] == "share")[1]
    assert {"user_name": "dev@x.com", "permission_level": "CAN_MANAGE"} in share and {"group_name": "team", "permission_level": "CAN_MANAGE"} in share
    spec = package.front_matter(next(c for c in calls if c[0] == "upload")[2])
    assert spec["build_type"] == "no_code" and spec["consumer_group"] == "analysts" and spec["owner"] == "dev@x.com"
    method, path, body = sent[0]
    assert (method, path) == ("POST", "/api/2.0/genie/spaces") and body["parent_path"] == "/Workspace/aops/no-code/sales"
    assert body["title"] == "매출 (개발)" and body["warehouse_id"] == "wh1"
    assert json.loads(body["serialized_space"])["data_sources"]["tables"] == [{"identifier": "main.s.orders"}]
    agents, genie_row, event = svc.sql.atomics[0]
    assert "'no_code'" in agents[0] and genie_row[1]["dev"] == "n" * 32 and event[0] == "EVENT create"
    assert row["dev_url"] == "https://ws.example.com/genie/rooms/" + "n" * 32


def test_revoke_keeps_everyone_else():
    from databricks.sdk.service.iam import AccessControlResponse, Permission, PermissionLevel

    acl = [AccessControlResponse(group_name="analysts", all_permissions=[Permission(permission_level=PermissionLevel.CAN_RUN, inherited=False)]),
           AccessControlResponse(user_name="dev@x.com", all_permissions=[Permission(permission_level=PermissionLevel.CAN_RUN, inherited=False)]),
           AccessControlResponse(group_name="admins", all_permissions=[Permission(permission_level=PermissionLevel.CAN_MANAGE, inherited=True)])]
    sets = []
    permissions = SimpleNamespace(get=lambda kind, space_id: SimpleNamespace(access_control_list=acl),
                                  set=lambda kind, space_id, access_control_list: sets.append([a.as_dict() for a in access_control_list]))
    svc = service(permissions=permissions)
    grants = svc.promote_genie(Caller("dev@x.com"), "sales", {"target": "p" * 32, "current_version": "1.0.0"}, ["group:analysts"], True)
    assert sets == [[{"user_name": "dev@x.com", "permission_level": "CAN_RUN"}]]
    assert grants[0]["securable"] == "genie:" + "p" * 32 and grants[0]["granted"] is False


# ── skill and code agents in the requester's workspace folder ──────────────

def user_workspace(tree):
    """A fake of the workspace REST API as the requester sees it: {path: "DIRECTORY" | "NOTEBOOK" | bytes}."""
    import base64
    import urllib.parse

    calls = []

    def as_user(token, method, path, body=None):
        calls.append((method, path.split("?")[0], body))
        query = dict(urllib.parse.parse_qsl(path.split("?", 1)[1])) if "?" in path else {}
        if path.startswith("/api/2.0/workspace/get-status"):
            p = query["path"]
            if p not in tree:
                return {"status": 404, "body": None, "error": "RESOURCE_DOES_NOT_EXIST"}
            return {"status": 200, "body": {"object_id": abs(hash(p)) % 1000, "object_type": "FILE"}, "error": None}
        if path.startswith("/api/2.0/workspace/mkdirs"):
            tree[body["path"]] = "DIRECTORY"
        elif path.startswith("/api/2.0/workspace/import"):
            tree[body["path"]] = base64.b64decode(body["content"])
        elif path.startswith("/api/2.0/workspace/list"):
            p = query["path"]
            objects = [{"path": k, "object_type": v if isinstance(v, str) else "FILE"} for k, v in tree.items()
                       if k.rsplit("/", 1)[0] == p]
            return {"status": 200, "body": {"objects": objects}, "error": None}
        elif path.startswith("/api/2.0/workspace/export"):
            return {"status": 200, "body": {"content": base64.b64encode(tree[query["path"]]).decode()}, "error": None}
        return {"status": 200, "body": {}, "error": None}

    return as_user, calls


def test_template_goes_into_my_folder_and_never_over_an_existing_one():
    tree = {}
    svc = service()
    svc._as_user, calls = user_workspace(tree)
    svc.agent = lambda a: None
    files = {"SPEC.md": b"---\nbuild_type: code\n---\n", "aops.toml": b"", "agent/agent.py": b"print(1)\n", ".claude/settings.json": b"{}",
             "CLAUDE.md": b"x"}
    row = svc.create_in_workspace(Caller("dev@x.com", "t"), "code", "helper", files, {"description": "돕는다"})
    root = "/Workspace/Users/dev@x.com/aops/helper"
    assert tree[f"{root}/agent/agent.py"] == b"print(1)\n" and f"{root}/.claude/settings.json" not in tree and f"{root}/CLAUDE.md" not in tree
    assert row["open_file"] == "agent/agent.py" and row["open_url"].startswith("https://ws.example.com/editor/files/")
    assert all(body["overwrite"] is False for method, path, body in calls if path.endswith("/import"))
    agents, event = svc.sql.atomics[0]
    assert ":type" in agents[0] and agents[1]["type"] == "code" and event[0] == "EVENT create"
    assert event[1]["d"]["workspace_path"] == root
    with pytest.raises(ServiceError) as error:
        svc.create_in_workspace(Caller("dev@x.com", "t"), "code", "helper", files, {})
    assert error.value.code == "folder_exists"
    with pytest.raises(ServiceError):
        svc.create_in_workspace(Caller("dev@x.com", "t"), "code", "other", files, {}, "/Workspace/../etc")


def test_register_reads_my_folder_with_the_register_rules():
    root = "/Workspace/Users/dev@x.com/aops/helper"
    tree = {root: "DIRECTORY", f"{root}/SPEC.md": b"---\nbuild_type: skill\n---\n", f"{root}/aops.toml": b"x",
            f"{root}/agent": "DIRECTORY", f"{root}/agent/skills": "DIRECTORY", f"{root}/agent/skills/k": "DIRECTORY",
            f"{root}/agent/skills/k/SKILL.md": b"---\nname: k\n---\n", f"{root}/runs": "DIRECTORY", f"{root}/runs/s.db": b"db",
            f"{root}/scratch": "NOTEBOOK", f"{root}/AGENTS.md": b"generated"}
    svc = service()
    svc._as_user, _ = user_workspace(tree)
    files, skipped = svc.read_workspace_folder(Caller("dev@x.com", "t"), root + "/")
    assert [rel for rel, _ in files] == ["SPEC.md", "agent/skills/k/SKILL.md", "aops.toml"]
    assert skipped == ["scratch (notebook)"]
    links = svc.workspace_links(Caller("dev@x.com", "t"), root, dict(files))
    assert links["open_file"] == "agent/skills/k/SKILL.md" and links["workspace_path"] == root
