from types import SimpleNamespace

import pytest

pytest.importorskip("databricks.sdk")  # the dbx extra

from aops.dbx.service import Caller, Service  # noqa: E402


def fake_service(calls):
    svc = object.__new__(Service)
    svc.w = SimpleNamespace(grants=SimpleNamespace(update=lambda kind, name, changes: calls.append((kind, name, changes))))
    svc._agent_or_404 = lambda agent_id: {"agent_id": agent_id, "owner": "me@x.com", "owner_group": None}
    svc._is_owner = lambda caller, agent: True
    svc.production = lambda agent_id: {"current_version": "1.0.1"}
    svc.version_skills = lambda agent_id, version: [{"uc_name": "c.s.a__k"}]
    svc.event = lambda *args: None
    return svc


def test_promote_sends_privileges_the_sdk_can_serialize():
    calls = []
    grants = fake_service(calls).promote(Caller("me@x.com", None, []), "a", ["group:team", "user:kim@x.com"])
    assert [g["principal"] for g in grants] == ["group:team", "user:kim@x.com"]
    kind, name, changes = calls[0]
    assert (kind, name) == ("skill", "c.s.a__k")
    assert changes[0].as_dict() == {"add": ["READ_VOLUME"], "principal": "team"}


def test_revoke_removes_the_same_privilege():
    calls = []
    fake_service(calls).promote(Caller("me@x.com", None, []), "a", ["group:team"], revoke=True)
    assert calls[0][2][0].as_dict() == {"remove": ["READ_VOLUME"], "principal": "team"}


class FakeSql:
    def __init__(self):
        self.statements = []

    def table(self, name):
        return f"c.s.{name}"

    def run(self, statement, params=None):
        self.statements.append((statement, params or {}))
        return []

    def update(self, statement, params=None):
        self.statements.append((statement, params or {}))
        return 1

    def one(self, statement, params=None):
        return None

    def atomic(self, statements):
        self.transactions = getattr(self, "transactions", 0) + 1
        self.statements.extend(statements)


def gate_service(gate, policy=None, spec=None):
    from aops.dbx.service import DEFAULT_POLICY

    svc = object.__new__(Service)
    svc.sql = FakeSql()
    svc.events = []
    svc.gate = lambda gate_id: dict(gate)
    svc.policy = lambda: (1, {**DEFAULT_POLICY, **(policy or {})})
    svc.version = lambda agent_id, version: {"spec": spec or {}}
    svc.event = lambda agent_id, version, kind, actor, detail: svc.events.append((kind, actor))
    svc._agent_or_404 = lambda agent_id: {"agent_id": agent_id, "owner": "owner@x.com", "owner_group": "team"}
    return svc


GATE = {"gate_id": "g1", "agent_id": "a", "version": "1.0.0", "requester": "dev@x.com", "decision": "pending_approval",
        "approval_mode": "optional", "validation_policy_mode": "mandatory", "validation_run_id": "r1"}


def test_optional_approval_follows_the_spec():
    low = gate_service(GATE, spec={"service_criticality": "low", "data_sensitivity": "internal"})
    assert low._auto_approval(GATE)[0] is True and "중요도 low" in low._auto_approval(GATE)[1]
    medium = gate_service(GATE, spec={"service_criticality": "medium", "data_sensitivity": "internal"})
    assert medium._auto_approval(GATE)[0] is False and "승인자 필요" in medium._auto_approval(GATE)[1]
    personal = gate_service(GATE, spec={"service_criticality": "low", "data_sensitivity": "personal"})
    assert personal._auto_approval(GATE)[0] is False
    assert gate_service(GATE)._auto_approval({**GATE, "approval_mode": "auto"}) == (True, "정책의 자동 승인")
    assert gate_service(GATE)._auto_approval({**GATE, "approval_mode": "required"}) == (False, None)


def test_requester_cannot_reject_and_is_told_to_cancel():
    from aops.dbx.service import ServiceError

    svc = gate_service(GATE)
    with pytest.raises(ServiceError) as error:
        svc.reject(Caller("dev@x.com"), "g1", "no")
    assert error.value.status == 403 and "--cancel" in str(error.value)
    svc.reject(Caller("boss@x.com"), "g1", "평가 부족")
    assert svc.events == [("gate_rejected", "boss@x.com")]


def test_cancel_only_while_the_request_is_open():
    from aops.dbx.service import ServiceError

    with pytest.raises(ServiceError) as error:
        gate_service({**GATE, "decision": "approved"}).cancel(Caller("dev@x.com"), "g1")
    assert error.value.status == 409
    with pytest.raises(ServiceError):
        gate_service(GATE).cancel(Caller("other@x.com"), "g1")  # not the requester, not the team
    team = gate_service({**GATE, "decision": "validating"})
    team.cancel(Caller("mate@x.com", groups=["team"]), "g1")
    assert team.events == [("gate_canceled", "mate@x.com")]


def test_optional_validation_failure_waits_for_a_person():
    svc = gate_service({**GATE, "decision": "validating", "validation_policy_mode": "optional", "approval_mode": "auto"})
    svc.evaluation = lambda run_id: {"validation_run_id": run_id, "status": "failed", "failure_reason": "skills: 템플릿"}
    moved = []
    svc._after_gate = lambda gate_id, run_id, failed=None: moved.append(failed)
    svc.advance("g1")
    assert moved and "optional" in moved[0]
    strict = gate_service({**GATE, "decision": "validating"})
    strict.evaluation = svc.evaluation
    strict.advance("g1")
    assert strict.events == [("gate_rejected", "policy:gate")]


def test_register_does_not_create_uc_skills(tmp_path):
    from aops import init, package

    project = init.create(tmp_path, "agent-x", edition="dbx")
    data = package.build(project).data
    svc = object.__new__(Service)
    svc.config = SimpleNamespace(registry="c.s", catalog="c", schema="s")
    svc.sql = FakeSql()
    svc.w = SimpleNamespace(files=SimpleNamespace(upload=lambda *a, **k: None),
                            ai_gateway=SimpleNamespace(create_skill=lambda *a, **k: pytest.fail("created at register")))
    svc.agent = lambda agent_id: None
    svc.versions = lambda agent_id: []
    svc.version = lambda agent_id, v: {"version": v}
    svc.version_skills = lambda agent_id, v: [{"skill_alias": "my-skill", "uc_name": "c.s.agent-x__my-skill"}]
    svc.event = lambda *a: None
    row = svc.register(Caller("dev@x.com"), "agent-x", data)
    assert row["version"] == "1.0.0"
    inserted = [p for stmt, p in svc.sql.statements if "agent_version_skills" in stmt]
    assert inserted and inserted[0]["uc"] == "c.s.agent-x__my-skill"
    assert svc.sql.transactions == 1  # agent, version, skills and the audit line commit together
    assert [stmt.split()[0] for stmt, _ in svc.sql.statements[1:]] == ["INSERT"] * 4


def test_register_keeps_the_note_on_the_version_and_the_record(tmp_path):
    svc, data, _ = registered(tmp_path, None)
    events = []
    svc._event_statement = lambda agent_id, version, kind, actor, detail: events.append(detail) or ("SELECT 1", {})
    svc.register(Caller("dev@x.com"), "agent-x", data, note="  사내 주소·고객 정보 제거  ")
    insert = [stmt for stmt, _ in svc.sql.statements if "INSERT INTO c.s.agent_versions" in stmt][0]
    assert "registered_at)" in insert and "note" not in insert  # named columns; the note is set apart
    update = [p for stmt, p in svc.sql.statements if stmt.startswith("UPDATE c.s.agent_versions SET note")][0]
    assert update["n"] == "사내 주소·고객 정보 제거" and events[-1]["note"] == "사내 주소·고객 정보 제거"


def test_a_registry_without_the_note_column_still_registers(tmp_path):
    svc, data, deleted = registered(tmp_path, None)
    recorded = svc.sql.run

    def run(statement, params=None):
        if "SET note" in statement:
            raise RuntimeError("[UNRESOLVED_COLUMN.WITH_SUGGESTION] note cannot be resolved")
        return recorded(statement, params)

    svc.sql.run = run
    assert svc.register(Caller("dev@x.com"), "agent-x", data, note="x")["version"] == "1.0.0" and not deleted


def test_admin_ddl_skips_a_column_that_is_there_already():
    from aops.admin import run_ddl

    ran = []

    class Sql:
        def run(self, stmt):
            ran.append(stmt)
            if "ADD COLUMNS" in stmt:
                raise RuntimeError("[FIELD_ALREADY_EXISTS] Cannot add column, because `note` already exists")

    run_ddl(Sql(), ["CREATE TABLE IF NOT EXISTS t (a INT)", "ALTER TABLE t ADD COLUMNS (note STRING)", "CREATE VIEW v AS SELECT 1"])
    assert len(ran) == 3

    class Broken:
        def run(self, stmt):
            raise RuntimeError("[TABLE_OR_VIEW_NOT_FOUND] t")

    with pytest.raises(RuntimeError):
        run_ddl(Broken(), ["ALTER TABLE t ADD COLUMNS (note STRING)"])


def test_a_second_request_says_which_one_is_open():
    import json

    from aops.dbx.base import gate_open_error
    from aops.server.app import problem

    error = gate_open_error({"gate_id": "g1", "version": "1.0.0", "decision": "pending_approval", "requester": "dev@x.com"})
    assert (error.status, error.code) == (409, "gate_open") and error.data["gate_id"] == "g1"
    body = json.loads(problem(409, error.code, error.message, data=error.data).body)
    assert body["data"] == {"gate_id": "g1", "version": "1.0.0", "decision": "pending_approval", "requester": "dev@x.com",
                            "deployment_status": None}


def registered(tmp_path, agent, spec_edit=None, upload_fails=False):
    from aops import init, package

    project = init.create(tmp_path, "agent-x", edition="dbx")
    if spec_edit:
        spec = project.root / "SPEC.md"
        spec.write_text(spec_edit(spec.read_text(encoding="utf-8")), encoding="utf-8")
    data = package.build(project).data
    deleted = []
    svc = object.__new__(Service)
    svc.config = SimpleNamespace(registry="c.s", catalog="c", schema="s")
    svc.sql = FakeSql()
    if upload_fails:
        def fail(statements):
            raise RuntimeError("transaction failed")
        svc.sql.atomic = fail
    svc.w = SimpleNamespace(files=SimpleNamespace(upload=lambda *a, **k: None, delete=deleted.append))
    svc.agent = lambda agent_id: agent
    svc._is_owner = lambda caller, row: True
    svc.versions = lambda agent_id: [{"version": "1.0.0", "source_sha256": "old"}] if agent else []
    svc.version = lambda agent_id, v: {"version": v}
    svc.version_skills = lambda agent_id, v: []
    return svc, data, deleted


def test_register_again_refreshes_the_agent_columns(tmp_path):
    def edit(text):
        return (text.replace("service_criticality: medium", "service_criticality: low")
                .replace("\nowner_group:\n", "\nowner_group: team-b\n"))

    svc, data, _ = registered(tmp_path, {"agent_id": "agent-x", "owner": "dev@x.com"}, edit)
    svc.register(Caller("dev@x.com"), "agent-x", data)
    stmt, params = next((s, p) for s, p in svc.sql.statements if s.startswith("UPDATE c.s.agents"))
    for column in ("name", "description", "owner_group", "service_criticality", "data_sensitivity", "security_level", "spec"):
        assert f"{column} = " in stmt
    assert "owner = coalesce(:owner, owner)" in stmt  # no owner in the SPEC keeps the current owner
    assert params["crit"] == "low" and params["group"] == "team-b" and params["owner"] is None


def test_register_takes_the_zip_back_when_the_registry_write_fails(tmp_path):
    svc, data, deleted = registered(tmp_path, None, upload_fails=True)
    with pytest.raises(RuntimeError):
        svc.register(Caller("dev@x.com"), "agent-x", data)
    assert deleted == ["/Volumes/c/s/agent-x/1.0.0.zip"]


def test_atomic_block_keeps_each_statement_parameters_apart():
    from aops.dbx.sql import atomic_block

    body, params = atomic_block([
        ("UPDATE t SET v = :v WHERE a = :a", {"a": "x", "v": "1"}),
        ("INSERT INTO e VALUES (:a, '10:30', CAST(:v AS STRING), x::int)", {"a": "y", "v": "2"}),
    ])
    assert body.startswith("BEGIN ATOMIC\n") and body.endswith("\nEND")
    assert "UPDATE t SET v = :s0_v WHERE a = :s0_a;" in body
    assert "INSERT INTO e VALUES (:s1_a, '10:30', CAST(:s1_v AS STRING), x::int);" in body
    assert params == {"s0_a": "x", "s0_v": "1", "s1_a": "y", "s1_v": "2"}


def test_a_tokens_account_is_kept_for_a_while():
    svc = object.__new__(Service)
    asked = []
    svc._as_user = lambda token, method, path, body=None, host=None: asked.append(token) or {
        "status": 200, "body": {"userName": "dev@x.com", "groups": [{"display": "admins"}]}}
    first = svc.identify("tok", None)
    again = svc.identify("tok", None)
    assert (first.email, first.groups) == (again.email, again.groups) == ("dev@x.com", ["admins"]) and asked == ["tok"]
    svc.identify("other", None)
    assert asked == ["tok", "other"]
    svc._as_user = lambda token, method, path, body=None, host=None: {"status": 401, "body": {}}
    with pytest.raises(Exception):
        svc.identify("bad", None)  # a token SCIM does not take is not kept, and names nobody
