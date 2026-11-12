"""The console (web UI) and the API it adds: folder upload, template, policy change, reads."""

import io
import zipfile
from types import SimpleNamespace

import pytest

pytest.importorskip("databricks.sdk")  # the dbx extra
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aops import init, package  # noqa: E402
from aops.dbx.service import Caller, Service, ServiceError  # noqa: E402
from aops.server.api.lifecycle import folder_zip  # noqa: E402
from aops.server.app import create_app  # noqa: E402


def test_root_serves_the_console_with_versioned_assets():
    client = TestClient(create_app(edition="dbx"))
    page = client.get("/")
    assert page.status_code == 200 and "aops console" in page.text
    assert "/web/app.js?v=" in page.text and "/web/style.css?v=" in page.text
    assert client.get("/web/app.js").status_code == 200 and client.get("/web/style.css").status_code == 200


def test_folder_upload_makes_the_same_zip_as_the_cli(tmp_path):
    project = init.create(tmp_path, "agent-x", edition="dbx")
    cli = package.build(project)
    # what a browser sends for a picked folder: every file, paths under the folder name (harness settings too)
    files = [(f"agent-x/{p.relative_to(project.root).as_posix()}", p.read_bytes()) for p in sorted(project.root.rglob("*")) if p.is_file()]
    assert any("/.claude/" in name for name, _ in files)
    assert folder_zip(files) == cli.data


def test_template_is_the_init_folder_with_this_server(tmp_path):
    client = TestClient(create_app(edition="dbx"))
    resp = client.post("/api/v1/projects/template", json={"name": "price-helper", "type": "skill"},
                       headers={"x-forwarded-host": "aops-server-1.aws.databricksapps.com"})
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        toml = zf.read("price-helper/aops.toml").decode()
    assert "price-helper/SPEC.md" in names and "price-helper/agent/skills/my-skill/SKILL.md" in names
    assert 'server = "https://aops-server-1.aws.databricksapps.com"' in toml
    assert client.post("/api/v1/projects/template", json={"name": "Bad Name"}).status_code == 400
    assert client.post("/api/v1/projects/template", json={"name": "x", "type": "no_code"}).status_code == 501


class PolicySql:
    def __init__(self):
        self.written = []

    def table(self, name):
        return f"c.s.{name}"

    def run(self, statement, params=None):
        return [{"policy_version": 3, "status": "active", "body": '{"validation_mode": "mandatory", "approval_mode": "auto", '
                 '"git": {"connection": "c", "owner": "o"}}', "created_by": "admin@x.com", "created_at": "2026-11-13T00:00:00Z"}]

    def one(self, statement, params=None):
        return {"policy_version": 3, "body": '{"validation_mode": "mandatory", "approval_mode": "auto", "git": {"connection": "c", "owner": "o"}}'}

    def atomic(self, statements):
        self.written.append(statements)


def policy_service():
    svc = object.__new__(Service)
    svc.sql = PolicySql()
    return svc


def test_only_workspace_admins_change_the_policy():
    svc = policy_service()
    with pytest.raises(ServiceError) as denied:
        svc.set_policy(Caller("dev@x.com", groups=["users"]), {"approval_mode": "required"})
    assert denied.value.status == 403 and svc.sql.written == []
    with pytest.raises(ServiceError):
        svc.set_policy(Caller("admin@x.com", groups=["admins"]), {"approval_mode": "sometimes"})
    svc.set_policy(Caller("admin@x.com", groups=["admins"]), {"approval_mode": "required"})
    (retire, insert), = svc.sql.written
    assert "status = 'retired'" in retire[0]
    body = insert[1]["b"]
    assert insert[1]["v"] == 4 and body["approval_mode"] == "required"
    assert body["git"] == {"connection": "c", "owner": "o"}  # what the console does not show stays


def test_me_tells_the_roles_the_screens_use():
    svc = policy_service()
    assert svc.me(Caller("a@x.com", groups=["admins"]))["roles"] == ["developer", "approver", "admin"]
    svc.policy = lambda: (1, {"approver_group": "approvers"})
    assert svc.me(Caller("d@x.com", groups=["users"]))["roles"] == ["developer"]
    assert svc.me(Caller("p@x.com", groups=["approvers"]))["roles"] == ["developer", "approver"]


def test_versions_carry_their_git_copy_newest_first():
    svc = object.__new__(Service)
    svc._agent_or_404 = lambda agent_id: {}
    svc.versions = lambda agent_id: [{"version": "1.0.0", "registered_at": "t0"}, {"version": "1.0.1", "registered_at": "t1"}]
    svc.git_records = lambda agent_id: {"1.0.1": {"commit_sha": "c1", "url": "https://g/tree/v1.0.1", "status": "synced"}}
    rows = svc.version_rows("a")
    assert [r["version"] for r in rows] == ["1.0.1", "1.0.0"]
    assert rows[0]["git_url"] == "https://g/tree/v1.0.1" and rows[1]["git_url"] is None and rows[0]["created_at"] == "t1"


def test_a_local_console_is_the_profile_user(monkeypatch):
    from aops.server import dbx

    seen = []
    svc = SimpleNamespace(config=SimpleNamespace(profile="my-profile"),
                          w=SimpleNamespace(config=SimpleNamespace(authenticate=lambda: {"Authorization": "Bearer pc-token"})),
                          identify=lambda token, email: seen.append((token, email)) or Caller(email or "me@x.com"))
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    dbx.caller_of(request, svc)
    assert seen == [("pc-token", None)]
    seen.clear()  # from another machine, or inside Databricks Apps: never the server's own login
    dbx.caller_of(SimpleNamespace(headers={}, client=SimpleNamespace(host="10.0.0.5")), svc)
    monkeypatch.setenv("DATABRICKS_APP_NAME", "aops-server")
    dbx.caller_of(request, svc)
    assert seen == [(None, None), (None, None)]
    monkeypatch.delenv("DATABRICKS_APP_NAME")
    svc.config.profile = None  # in Databricks Apps: never borrow the server's own identity
    seen.clear()
    dbx.caller_of(SimpleNamespace(headers={"x-forwarded-email": "u@x.com", "x-forwarded-access-token": "t"}), svc)
    assert seen == [("t", "u@x.com")]
