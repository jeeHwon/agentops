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
