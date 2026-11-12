"""Code-based agents (Agent Bricks): checks, the deploy copy, names, init."""

import stat
import tomllib

from aops.dbx import code

AGENT_TOML = b'schema_version = 1\n\n[agent]\nframework = "openai"\nserver = "agentbricks"\n\n[tracing]\nexperiment_name = "/Shared/agentbricks_traces/x"\n'
SPEC = b"---\nagent_id: price-bot\nname: price-bot\nbuild_type: code\nservice_criticality: medium\ndata_sensitivity: internal\ndescription: \xea\xb0\x80\xea\xb2\xa9 \xec\x95\x88\xeb\x82\xb4\n---\n"


def project(**extra):
    return {"SPEC.md": SPEC, "aops.toml": b'[project]\nname = "price-bot"\n', "agent.toml": AGENT_TOML,
            "app.yaml": b'command: ["uv", "run", "start-server"]\n', "pyproject.toml": b"[project]\nname = \"x\"\n", **extra}


def test_a_code_agent_is_checked_as_an_agent_bricks_project():
    from aops.dbx.service import check_agent

    steps = {s["name"]: s for s in check_agent(project())}
    assert set(steps) == {"format", "project", "secrets"}  # no skills step for code
    assert steps["project"]["status"] == "passed" and "uv.lock" in steps["project"]["detail"]
    custom = project(**{"agent.toml": b'[agent]\nframework = "openai"\nserver = "custom"\n'})
    assert {s["name"]: s for s in check_agent(custom)}["project"]["status"] == "failed"
    missing = {k: v for k, v in project().items() if k != "app.yaml"}
    assert "app.yaml" in {s["name"]: s for s in check_agent(missing)}["project"]["detail"]


def test_the_deploy_copy_points_tracing_at_the_production_experiment(tmp_path):
    files = project()
    changed = code.prepare(files, tmp_path / "src", "/Users/sp/aops/agents/price-bot/traces")
    toml = tomllib.loads((tmp_path / "src" / "agent.toml").read_text())
    assert changed == ["agent.toml"] and toml["tracing"]["experiment_name"] == "/Users/sp/aops/agents/price-bot/traces"
    assert files["agent.toml"] == AGENT_TOML  # the registered files are not touched
    bare = project(**{"agent.toml": b'[agent]\nframework = "openai"\n'})
    code.prepare(bare, tmp_path / "b", "/x/traces")
    assert tomllib.loads((tmp_path / "b" / "agent.toml").read_text())["tracing"]["experiment_name"] == "/x/traces"


def test_app_names_fit_the_agentbricks_limit():
    assert code.app_name("price-bot") == "agent-bricks-price-bot"
    long = code.app_base("customer-service-escalation")
    assert len(long) <= 17 and long != code.app_base("customer-service-escalations")
    assert len(code.app_name("customer-service-escalation")) <= 30


def test_init_code_adds_aops_files_to_the_agent_bricks_template(tmp_path):
    from aops import init

    fake = tmp_path / "agentbricks"  # stands in for `agentbricks init <dir> --framework openai`
    fake.write_text('#!/bin/sh\nmkdir -p "$2"\nprintf \'[agent]\\nframework = "openai"\\n\' > "$2/agent.toml"\ntouch "$2/app.yaml"\necho "# t" > "$2/README.md"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    project_ = init.create_code(tmp_path, "price-bot", agentbricks=str(fake))
    spec = (project_.root / "SPEC.md").read_text()
    assert "build_type: code" in spec and "[project]" in (project_.root / "aops.toml").read_text()
    assert "aops deploy" in (project_.root / "README.md").read_text()


def test_a_code_deploy_cut_by_a_restart_is_marked_failed():
    from aops.dbx.service import CODE_DEPLOYS, Service

    updates = []
    svc = object.__new__(Service)
    svc.agent = lambda agent_id: {"build_type": "code"}
    svc.sql = type("Sql", (), {"table": staticmethod(lambda n: n), "update": staticmethod(lambda s, p: updates.append(p) or 1)})()
    svc.gate = lambda gate_id: {"gate_id": gate_id, "deployment_status": "failed"}
    gate = {"gate_id": "g1", "agent_id": "a", "deployment_id": "d1", "decision": "approved", "deployment_status": "running"}
    CODE_DEPLOYS.add("g1")
    assert svc._reconcile_code_deploy(gate) is gate and updates == []  # still running here
    CODE_DEPLOYS.discard("g1")
    assert svc._reconcile_code_deploy(gate)["deployment_status"] == "failed" and updates[0]["id"] == "d1"


def test_zip_names_that_leave_the_folder_are_refused(tmp_path):
    import io
    import zipfile

    import pytest

    from aops import package

    for name in ("../../evil.py", "/etc/passwd", "a/../../b", "a\\b", "C:/x"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(name, "x")
        with pytest.raises(ValueError):
            package.read(buf.getvalue())
        assert not package.included(name)
    with pytest.raises(ValueError):
        code.prepare({"../x": b"x", **project()}, tmp_path / "src", "/e")


def test_register_refuses_bad_names_and_a_changed_kind():
    import pytest

    from aops.dbx.service import Caller, Service, ServiceError

    svc = object.__new__(Service)
    with pytest.raises(ServiceError) as bad:
        svc.register(Caller("dev@x.com"), "../x", b"")
    assert bad.value.code == "bad_agent_id"
    svc.agent = lambda agent_id: {"agent_id": agent_id, "owner": "dev@x.com", "build_type": "skill"}
    svc._is_owner = lambda caller, agent: True
    buf = __import__("io").BytesIO()
    with __import__("zipfile").ZipFile(buf, "w") as zf:
        for k, v in project().items():
            zf.writestr(k, v)
    with pytest.raises(ServiceError) as changed:
        svc.register(Caller("dev@x.com"), "price-bot", buf.getvalue())
    assert changed.value.code == "build_type_changed"


def test_one_approver_counts_once():
    import pytest

    from aops.dbx.service import Caller, Service, ServiceError

    svc = object.__new__(Service)
    gate = {"gate_id": "g", "agent_id": "a", "version": "1.0.0", "requester": "dev@x.com", "decision": "pending_approval",
            "required_approvals": 2}
    svc.gate = lambda gate_id: dict(gate)
    svc._check_approver = lambda caller, g, action: None
    svc.sql = type("Sql", (), {"table": staticmethod(lambda n: n),
                               "one": staticmethod(lambda s, p: {"x": 1} if "SELECT 1" in s else {"n": 1}),
                               "run": staticmethod(lambda s, p: pytest.fail("a second approval was recorded"))})()
    with pytest.raises(ServiceError) as again:
        svc.approve(Caller("boss@x.com"), "g")
    assert again.value.status == 409
