"""More than one workspace: the policy's list, where a request came from, and where production goes."""

import base64
import io
import json
from types import SimpleNamespace

import pytest

from aops import package, validate
from aops.workspaces import Workspaces, normalize_host, outside, check_entries

HOME = "https://admin.example.com"
POLICY = {"workspaces": {
    "personal": {"host": "https://personal.example.com/", "roles": ["dev"]},
    "project": {"host": "project.example.com", "roles": ["dev", "target"], "catalogs": ["project"], "warehouse_id": "wh-p"}},
    "default_target": "project"}


# ── the policy's workspaces (no Databricks) ─────────────────────────────────

def test_one_workspace_is_dev_and_target():
    spaces = Workspaces.from_policy({}, HOME)
    assert not spaces.configured
    assert spaces.home.name == "home" and spaces.home.roles == ("dev", "target")
    assert spaces.target().home and spaces.get(None).home and spaces.get("home").home


def test_listed_workspaces_take_the_roles_from_home():
    spaces = Workspaces.from_policy(POLICY, HOME)
    assert spaces.home.roles == ()  # the admin workspace: server, registry, Jobs only
    assert [w.name for w in spaces.with_role("dev")] == ["personal", "project"]
    assert spaces.target().name == "project" and spaces.target().warehouse_id == "wh-p"
    with pytest.raises(KeyError):
        spaces.target("personal")  # not a target
    assert spaces.by_host("https://PERSONAL.example.com/x").name == "personal"
    assert spaces.by_host("https://other.example.com") is None


def test_the_servers_own_workspace_can_be_listed_by_name():
    spaces = Workspaces.from_policy({"workspaces": {"admin": {"host": HOME, "roles": ["target"]},
                                                    "personal": {"host": "https://personal.example.com", "roles": ["dev"]}}}, HOME)
    assert spaces.home.name == "admin" and spaces.home.roles == ("target",)
    assert spaces.target().home and spaces.get("home").name == "admin"


def test_hosts_and_entries_are_checked():
    assert normalize_host("adb-1.azuredatabricks.net/?o=1") == "https://adb-1.azuredatabricks.net"
    with pytest.raises(ValueError):
        normalize_host("http://x.example.com")
    problems = check_entries({"Bad Name": {"host": "https://a.example.com", "roles": ["dev"]},
                              "b": {"host": "https://a.example.com", "roles": ["prod"]}})
    assert any("Bad Name" in p for p in problems) and any("주소가 같습니다" in p for p in problems) and any("roles" in p for p in problems)


def test_outside_catalogs():
    assert outside({"project.sales.orders", "personal.u123.t"}, ("project",)) == ["personal.u123.t"]
    assert outside({"`Project`.s.t"}, ["project"]) == []
    assert outside({"anything.s.t"}, ()) == []


# ── the checklist ───────────────────────────────────────────────────────────

def nocode_files(tables, target=None):
    spec = "---\nagent_id: sales\nname: 매출\nbuild_type: no_code\nowner: dev@x.com\nservice_criticality: low\n" \
           "data_sensitivity: internal\nsecurity_level: general\nconsumer_group: analysts\napprovers: lead@x.com\n" \
           + (f"target_workspace: {target}\n" if target else "") + "description: 지역별 매출\n---\n\n# 매출\n\n지역별 매출을 답한다.\n"
    definition = {"version": 2, "data_sources": {"tables": [{"identifier": t} for t in tables]}}
    return {"SPEC.md": spec.encode(), "aops.toml": b'[agent]\nbuild_type = "no_code"\n',
            "genie/space.json": json.dumps(definition).encode()}


def rules(steps):
    return {r["id"]: r for s in steps for r in s.get("rules") or []}


def test_one_workspace_adds_no_rules():
    steps = validate.check(nocode_files(["personal.u1.t"]), "no_code", {})
    assert "target" not in [s["name"] for s in steps] and "spec.target_workspace" not in rules(steps)


def test_production_data_must_be_in_the_targets_catalogs():
    steps = validate.check(nocode_files(["personal.u1.orders", "project.sales.orders"]), "no_code", POLICY)
    target = next(s for s in steps if s["name"] == "target")
    assert target["status"] == "failed" and "personal.u1.orders" in target["detail"]
    assert validate.check(nocode_files(["project.sales.orders"]), "no_code", POLICY)[-2]["status"] == "passed"


def test_target_workspace_must_be_a_target():
    steps = validate.check(nocode_files(["project.s.t"], target="personal"), "no_code", POLICY)
    rule = rules(steps)["spec.target_workspace"]
    assert not rule["passed"] and "personal" in rule["message"]
    assert rules(validate.check(nocode_files(["project.s.t"], target="project"), "no_code", POLICY))["spec.target_workspace"]["passed"]


# ── the server (fakes, no Databricks) ───────────────────────────────────────

pytest.importorskip("databricks.sdk")  # the dbx extra

from aops.dbx import clients, nocode  # noqa: E402
from aops.dbx.base import Caller, ServiceError  # noqa: E402
from aops.dbx.service import DEFAULT_POLICY, Config, Service  # noqa: E402


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


def service(policy=None, w=None):
    svc = object.__new__(Service)
    svc.config = Config("c.s", "wh-admin")
    svc.sql = Sql()
    svc.w = w or SimpleNamespace(config=SimpleNamespace(host=HOME))
    svc.policy = lambda: (1, {**DEFAULT_POLICY, **(POLICY if policy is None else policy)})
    svc.events = []
    svc.event = lambda *args: svc.events.append(args)
    svc._event_statement = lambda agent_id, version, kind, actor, detail=None: (f"EVENT {kind}", {"v": version, "d": detail})
    return svc


def test_a_token_from_a_listed_workspace_is_checked_there():
    svc = service()
    asked = []
    svc._as_user = lambda token, method, path, body=None, host=None: asked.append(host) or {
        "status": 200, "body": {"userName": "dev@x.com", "groups": [{"display": "team"}]}}
    caller = svc.identify("t1", None, "https://personal.example.com/")
    assert asked == ["https://personal.example.com"] and caller.workspace == "personal" and caller.host == "https://personal.example.com"
    assert caller.groups == ["team"]
    with pytest.raises(ServiceError) as error:
        svc.identify("t1", None, "https://elsewhere.example.com")
    assert error.value.code == "unknown_workspace" and asked == ["https://personal.example.com"]  # never sent there
    home = svc.identify("t2", None, HOME + "/")
    assert home.workspace is None and asked[-1] is None


def test_the_header_is_read_only_outside_apps(monkeypatch):
    from aops.server import dbx as server_dbx

    seen = []
    svc = SimpleNamespace(config=SimpleNamespace(profile=None),
                          identify=lambda token, email, workspace=None: seen.append((token, email, workspace)) or Caller("a@x.com"))
    headers = {"authorization": "Bearer tok", "x-aops-workspace": "https://personal.example.com"}
    request = SimpleNamespace(headers=headers, state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(oauth=None)), client=None)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    server_dbx.caller_of(request, svc)
    monkeypatch.setenv("DATABRICKS_APP_NAME", "aops-server")
    server_dbx.caller_of(request, svc)
    assert seen == [("tok", None, "https://personal.example.com"), ("tok", None, None)]


def test_clients_per_workspace(monkeypatch):
    made = []

    class Client:
        def __init__(self, **kwargs):
            made.append(kwargs)
            self.config = SimpleNamespace(host=kwargs.get("host"))

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", Client)
    secrets = SimpleNamespace(get_secret=lambda scope, key: SimpleNamespace(value=base64.b64encode(f"{scope}:{key}".encode()).decode()))
    svc = service({**POLICY, "workspace_credentials": {"secret_scope": "aops"}},
                  w=SimpleNamespace(config=SimpleNamespace(host=HOME), secrets=secrets))
    assert svc.client(None) is svc.w and svc.client("home") is svc.w
    project = svc.client("project")
    assert svc.client("project") is project  # kept
    assert made == [{"host": "https://project.example.com", "auth_type": "oauth-m2m", "client_id": "aops:client-id",
                     "client_secret": "aops:client-secret"}]
    profile, env = svc.cli_env(svc.workspace("project"))
    assert profile is None and env["DATABRICKS_HOST"] == "https://project.example.com" and env["DATABRICKS_CLIENT_SECRET"] == "aops:client-secret"
    monkeypatch.setenv("AOPS_WORKSPACE_PROFILES", "personal=personal-admin")
    svc.client("personal")
    assert made[-1] == {"profile": "personal-admin"}
    bare = service()
    bare.w.config.client_id = None
    with pytest.raises(ServiceError) as error:
        bare.client("project")
    assert error.value.code == "no_workspace_credentials"


def test_people_make_things_only_in_a_dev_workspace():
    svc = service()
    with pytest.raises(ServiceError) as error:
        svc.require_dev(Caller("dev@x.com"), "aops init")  # the admin workspace is not a dev one here
    assert error.value.code == "not_dev_workspace" and "personal" in error.value.message
    assert svc.require_dev(Caller("dev@x.com", "t", host="https://personal.example.com"), "aops init").name == "personal"
    with pytest.raises(ServiceError) as error:
        svc.same_workspace(Caller("dev@x.com", "t", host="https://personal.example.com"), "project", "이 에이전트 폴더", "aops register")
    assert error.value.code == "wrong_workspace" and error.value.data["workspace"] == "project"


SPEC = nocode_files(["project.sales.orders"])["SPEC.md"].decode()
DEFINITION = {"version": 2, "data_sources": {"tables": [{"identifier": "project.sales.orders"}]}}


def test_no_code_is_made_in_the_requesters_dev_workspace():
    calls, sent = [], []
    personal = SimpleNamespace(
        config=SimpleNamespace(host="https://personal.example.com"),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp-1")),
        workspace=SimpleNamespace(mkdirs=lambda path: calls.append(("mkdirs", path)), get_status=lambda path: SimpleNamespace(object_id=7),
                                  update_permissions=lambda *a, **k: calls.append(("share",)),
                                  upload=lambda path, data, format=None, overwrite=False: calls.append(("upload", path))),
        genie=SimpleNamespace(get_space=lambda *a, **k: SimpleNamespace(serialized_space="{}")))
    svc = service()
    svc.__dict__["_clients"] = {"https://personal.example.com": personal}
    svc.__dict__["_registry_ready"] = True
    svc.agent = lambda a: None
    svc.genie_agent = lambda a: {"agent_id": "sales", "dev_space_id": "n" * 32, "folder_path": "/Workspace/aops/no-code/sales",
                                 "dev_workspace": "personal", "prod_workspace": None, "prod_space_id": None}
    svc._as_user = lambda token, method, path, body=None, host=None: sent.append((host, path, body)) or {"status": 200, "body": {"space_id": "n" * 32}}
    caller = Caller("dev@x.com", "t", workspace="personal", host="https://personal.example.com")
    with pytest.raises(ServiceError) as error:  # personal has no warehouse in the policy: the requester picks one there
        svc.create_no_code(caller, {"name": "sales", "description": "매출", "tables": ["project.sales.orders"]})
    assert error.value.code == "no_warehouse"
    row = svc.create_no_code(caller, {"name": "sales", "description": "매출", "tables": ["project.sales.orders"], "warehouse_id": "wh-me"})
    assert ("mkdirs", "/Workspace/aops/no-code/sales") in calls and sent[0][0] == "https://personal.example.com"
    genie_insert = svc.sql.atomics[0][1]
    assert "dev_workspace" in genie_insert[0] and genie_insert[1]["ws"] == "personal"
    assert row["dev_url"].startswith("https://personal.example.com/genie/rooms/")


def test_no_code_production_goes_to_the_target_workspace():
    files = nocode.bundle("sales", SPEC, DEFINITION)
    sha = package.files_sha256(list(files.items()))
    data = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    snap = nocode.snapshot_path("c.s", "sales", sha)
    store = {snap: data}
    home_files = SimpleNamespace(download=lambda path: SimpleNamespace(contents=io.BytesIO(store[path])),
                                 upload=lambda path, data, overwrite=False: store.__setitem__(path, data.read()))
    calls, grants = [], []
    project = SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp-1")),
        workspace=SimpleNamespace(mkdirs=lambda path: calls.append(("mkdirs", path))),
        genie=SimpleNamespace(get_space=lambda space_id, include_serialized_space=False: SimpleNamespace(serialized_space=json.dumps(DEFINITION)),
                              create_space=lambda wh, ser, title=None, description=None, parent_path=None: (
                                  calls.append(("create", wh, parent_path)), SimpleNamespace(space_id="p" * 32))[1]),
        permissions=SimpleNamespace(update=lambda kind, space_id, access_control_list: grants.append(space_id)))
    svc = service(w=SimpleNamespace(config=SimpleNamespace(host=HOME), files=home_files))
    svc.__dict__["_clients"] = {"https://project.example.com": project}
    genie = {"agent_id": "sales", "dev_space_id": "d" * 32, "prod_space_id": None, "folder_path": "/Workspace/aops/no-code/sales",
             "consumer_group": "analysts", "warehouse_id": "wh-me", "dev_workspace": "personal", "prod_workspace": None}
    svc.sql = Sql({"genie_agents": genie})
    svc._agent_or_404 = lambda a: {"agent_id": "sales", "owner": "dev@x.com", "owner_group": None, "build_type": "no_code",
                                   "service_criticality": "low", "data_sensitivity": "internal", "security_level": "general"}
    svc.version = lambda a, v: None
    svc.production = lambda a: None
    svc.gate_snapshot = lambda gate_id: {"snapshot_path": snap, "snapshot_sha256": sha}
    svc.git_production = lambda *args: None
    svc.set_version_note = lambda *args: None
    gate = {"gate_id": "g1", "deployment_id": "d1", "agent_id": "sales", "version": "1.0.0", "decision": "approved",
            "upgrade_type": "initial", "requester": "dev@x.com", "approval_mode": "auto", "target_workspace": "project"}
    report = svc.run_no_code_deploy(gate)
    assert ("create", "wh-p", "/Workspace/aops/no-code/_production/sales") in calls  # the target's warehouse and root
    assert report["space_url"] == "https://project.example.com/genie/rooms/" + "p" * 32 and grants == ["p" * 32, "p" * 32]
    statements = svc.sql.atomics[0]
    pointer = next(p for s, p in statements if "agent_production" in s)
    assert "workspace = :w" in next(s for s, _ in statements if "agent_production" in s) and pointer["w"] == "project"
    assert any("prod_workspace = :w" in s and p.get("w") == "project" for s, p in statements)


def test_a_code_deploy_runs_agentbricks_against_the_target(monkeypatch, tmp_path):
    from aops.dbx import code

    files = {"aops.toml": b'[project]\nname = "bot"\ntype = "code"\n', "SPEC.md": b"---\nagent_id: bot\nbuild_type: code\n---\n",
             "agent.toml": b'[agent]\nserver = "agentbricks"\nframework = "langgraph"\n', "app.yaml": b"command: []\n", "pyproject.toml": b""}
    sha = package.files_sha256(list(files.items()))
    data = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    seen = {}
    monkeypatch.setattr(code, "deploy", lambda source, base, path, profile=None, env=None: seen.update(profile=profile, env=env, path=path) or (0, "ok"))
    project = SimpleNamespace(current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="sp-1")),
                              workspace=SimpleNamespace(mkdirs=lambda path: None),
                              apps=SimpleNamespace(get=lambda name: SimpleNamespace(url="https://agent-bricks-bot.project.apps")))
    secrets = SimpleNamespace(get_secret=lambda scope, key: SimpleNamespace(value=base64.b64encode(b"x").decode()))
    svc = service({**POLICY, "workspace_credentials": {"secret_scope": "aops"}},
                  w=SimpleNamespace(config=SimpleNamespace(host=HOME), secrets=secrets,
                                    files=SimpleNamespace(download=lambda path: SimpleNamespace(contents=io.BytesIO(data)))))
    svc.__dict__["_clients"] = {"https://project.example.com": project}
    svc.gate = lambda gate_id: {"gate_id": "g1", "deployment_id": "d1", "agent_id": "bot", "version": "1.0.0", "decision": "approved",
                                "upgrade_type": "initial", "requester": "dev@x.com", "target_workspace": "project"}
    svc.version = lambda a, v: {"artifact_path": "/Volumes/c/s/bot/1.0.0.zip", "source_sha256": sha}
    svc.production = lambda a: None
    svc.git_production = lambda *args: None
    report = svc.run_code_deploy("g1")
    assert report["status"] == "succeeded" and report["app_url"] == "https://agent-bricks-bot.project.apps"
    assert seen["profile"] is None and seen["env"]["DATABRICKS_HOST"] == "https://project.example.com"
    pointer = next(p for s, p in svc.sql.atomics[0] if "agent_production" in s)
    assert pointer["w"] == "project"


def test_the_request_record_keeps_the_target():
    svc = service()
    svc.__dict__["_registry_ready"] = True
    assert svc.target_detail(svc.workspace("project")) == {"target_workspace": "project", "target_host": "https://project.example.com"}
    assert service({}).target_detail(service({}).workspace(None)) == {}  # one workspace: records as before
    assert svc.target_of({}, "skill") is None and svc.target_of({}, "code").name == "project"
    with pytest.raises(ServiceError) as error:
        svc.target_of({"target_workspace": "personal"}, "no_code")
    assert error.value.code == "unknown_target"


def test_the_registry_must_have_the_columns_first():
    svc = service()
    svc.sql.run = lambda statement, params=None: (_ for _ in ()).throw(RuntimeError("UNRESOLVED_COLUMN dev_workspace"))
    with pytest.raises(ServiceError) as error:
        svc.registry_ready()
    assert error.value.code == "registry_outdated"
    assert clients.workspace_profiles() == {} or True


# ── admin setup and the CLI ─────────────────────────────────────────────────

def test_admin_workspace_options_carry_over_and_are_checked():
    from argparse import Namespace

    from aops.admin import parse_workspace, workspace_settings

    assert parse_workspace("project=https://p.example.com;roles=dev,target;catalogs=project;warehouse=w1") == (
        "project", {"host": "https://p.example.com", "roles": ["dev", "target"], "catalogs": ["project"], "warehouse_id": "w1"})
    args = Namespace(workspace=["personal=personal.example.com;roles=dev"], default_target=None, workspace_secret_scope="aops", no_workspaces=False)
    out = workspace_settings(args, {"workspaces": {"project": {"host": "https://p.example.com", "roles": ["dev", "target"]}},
                                    "default_target": "project"})
    assert set(out["workspaces"]) == {"project", "personal"} and out["workspaces"]["personal"]["host"] == "https://personal.example.com"
    assert out["default_target"] == "project" and out["workspace_credentials"]["secret_scope"] == "aops"
    kept = workspace_settings(Namespace(workspace=None, default_target=None, workspace_secret_scope=None, no_workspaces=False), out)
    assert kept == out
    assert workspace_settings(Namespace(workspace=None, default_target=None, workspace_secret_scope=None, no_workspaces=True), out) == {}
    with pytest.raises(SystemExit):
        workspace_settings(Namespace(workspace=None, default_target="personal", workspace_secret_scope=None, no_workspaces=False), out)


def test_the_cli_names_its_workspace_for_a_server_outside_apps(monkeypatch):
    from aops import client as client_mod

    class Fake:
        def __init__(self, profile=None):
            self.config = SimpleNamespace(host="https://personal.example.com/", authenticate=lambda: {"Authorization": "Bearer t"})

    monkeypatch.setattr("databricks.sdk.WorkspaceClient", Fake)
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    outside_apps = client_mod.Server(url="https://aops.example.com", profile="p")
    assert outside_apps._headers() == {"Authorization": "Bearer t", "X-Aops-Workspace": "https://personal.example.com"}
    app = client_mod.Server(url="https://aops-server-1.aws.databricksapps.com", profile="p")
    assert app._headers() == {"Authorization": "Bearer t"}
