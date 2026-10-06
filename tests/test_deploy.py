from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from codex_agentops.deploy import (
    DeployError,
    McpAppBuilder,
    default_app_name,
    infer_trace_schema,
    parse_trace_schema,
    validate_app_name,
    DatabricksAppDeployer,
)
from codex_agentops.release import load_release


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def _runtime(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    (runtime / "skills" / "release-summary").mkdir(parents=True)
    (runtime / "skills" / "release-validation").mkdir(parents=True)
    (runtime / ".codex/agents").mkdir(parents=True)
    (runtime / "AGENTS.md").write_text("# Harness\n", encoding="utf-8")
    (runtime / "skills/release-summary/SKILL.md").write_text(
        "# Release Summary\n", encoding="utf-8"
    )
    (runtime / "skills/release-validation/SKILL.md").write_text(
        "# Release Validation\n", encoding="utf-8"
    )
    (runtime / ".codex/agents/validator.toml").write_text(
        'name = "validator"\n'
        'description = "Validates output."\n'
        'developer_instructions = "Validate the output."\n',
        encoding="utf-8",
    )
    release = load_release(SAMPLE)
    config = {
        "schema_version": 1,
        "agent_id": release.agent_id,
        "release_version": release.release_version,
        "profiles": {
            "model": release.model_profile,
            "harness": release.harness_profile,
            "mcp_tools": release.mcp_tool_profile,
        },
        "skills": [
            {
                "alias": skill.alias,
                "uc_name": skill.uc_name,
                "version": skill.version,
                "sha256": skill.sha256,
                "markdown": f"skills/{skill.alias}/SKILL.md",
            }
            for skill in release.skills
        ],
        "subagents": [
            {
                "id": "validator",
                "instructions": ".codex/agents/validator.toml",
                "skills": ["release-validation"],
            }
        ],
    }
    (runtime / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    return runtime


def test_app_name_and_trace_schema_are_explicit_and_portable():
    release = load_release(SAMPLE)

    assert default_app_name(release.agent_id) == "mcp-release-sample-agent"
    assert validate_app_name("mcp-release-sample-agent") == "mcp-release-sample-agent"
    assert infer_trace_schema(release) == ("poc_catalog", "agentops_test")
    assert parse_trace_schema("main.agentops") == ("main", "agentops")

    with pytest.raises(DeployError, match="mcp-"):
        validate_app_name("release-sample-agent")
    with pytest.raises(DeployError, match="26"):
        validate_app_name("mcp-this-agent-name-is-too-long")


def test_builder_preserves_runtime_files_and_wires_app_resources(tmp_path):
    release = load_release(SAMPLE)
    output = tmp_path / "mcp-app"
    build = McpAppBuilder().build(
        release=release,
        runtime_root=_runtime(tmp_path),
        output=output,
        app_name="mcp-release-sample-agent",
        model_endpoint="databricks-gpt-5-1",
        experiment_name="/Shared/agentops/release-sample-agent",
        experiment_id="12345",
        trace_catalog="poc_catalog",
        trace_schema="agentops_test",
        warehouse_id="warehouse-1",
    )

    assert build.root == output
    assert (output / ".aops-generated").is_file()
    assert (output / "runtime/AGENTS.md").is_file()
    assert not (output / "runtime/spec.md").exists()
    assert not (output / "runtime/CLAUDE.md").exists()
    assert (output / "runtime/skills/release-summary/SKILL.md").is_file()
    assert (output / "runtime/.codex/agents/validator.toml").is_file()
    assert not (output / "runtime/agent.md").exists()
    assert (output / "server/app.py").is_file()
    assert (output / "server/tracing.py").is_file()
    tracing = (output / "server/tracing.py").read_text(encoding="utf-8")
    assert '"agent.skill_versions"' not in tracing
    assert 'metadata[f"{prefix}.sha256"] = skill["sha256"]' in tracing

    app_yaml = yaml.safe_load((output / "app.yaml").read_text(encoding="utf-8"))
    assert app_yaml["command"] == ["python", "-m", "server.main"]
    assert {
        item["name"]: item.get("valueFrom", item.get("value"))
        for item in app_yaml["env"]
    }["MLFLOW_EXPERIMENT_ID"] == "experiment"

    bundle = yaml.safe_load((output / "databricks.yml").read_text(encoding="utf-8"))
    assert "server/**" in bundle["sync"]["include"]
    assert "runtime/**" in bundle["sync"]["include"]
    assert (output / ".gitignore").read_text(encoding="utf-8").startswith(".databricks/")
    app = bundle["resources"]["apps"]["agent_mcp"]
    assert app["name"] == "mcp-release-sample-agent"
    assert app["user_api_scopes"] == ["serving.serving-endpoints"]
    assert app["resources"] == [
        {
            "name": "serving-endpoint",
            "serving_endpoint": {
                "name": "databricks-gpt-5-1",
                "permission": "CAN_QUERY",
            },
        },
        {
            "name": "experiment",
            "experiment": {"experiment_id": "12345", "permission": "CAN_MANAGE"},
        },
    ]

    deployment = yaml.safe_load((output / "deployment.yaml").read_text(encoding="utf-8"))
    assert deployment["authentication"] == "oauth-obo"
    assert deployment["trace_location"] == {
        "catalog": "poc_catalog",
        "schema": "agentops_test",
    }


def test_builder_will_not_replace_unmanaged_directory(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "user-file.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DeployError, match="덮어쓰지"):
        McpAppBuilder().build(
            release=load_release(SAMPLE),
            runtime_root=_runtime(tmp_path),
            output=output,
            app_name="mcp-release-sample-agent",
            model_endpoint="databricks-gpt-5-1",
            experiment_name="/Shared/agentops/release-sample-agent",
            experiment_id="12345",
            trace_catalog="poc_catalog",
            trace_schema="agentops_test",
            warehouse_id="warehouse-1",
        )

    assert (output / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_deployer_grants_only_uc_trace_tables_to_app_service_principal():
    calls = []

    class FakeApps:
        def get(self, name):
            assert name == "mcp-release-sample-agent"
            return SimpleNamespace(service_principal_client_id="app-client-id")

    class FakeGrants:
        def update(self, securable_type, full_name, *, changes):
            calls.append((str(securable_type), full_name, changes[0]))

    workspace = SimpleNamespace(apps=FakeApps(), grants=FakeGrants())
    deployer = DatabricksAppDeployer(
        "test-profile",
        workspace=workspace,
        runner=lambda command, cwd: None,
        experiment_resolver=lambda *args: "12345",
    )
    deployer._grant_trace_permissions(
        "mcp-release-sample-agent",
        catalog="poc_catalog",
        schema="agentops_test",
        experiment_id="12345",
    )

    assert [name for _, name, _ in calls] == [
        "poc_catalog",
        "poc_catalog.agentops_test",
        "poc_catalog.agentops_test.12345_otel_annotations",
        "poc_catalog.agentops_test.12345_otel_logs",
        "poc_catalog.agentops_test.12345_otel_metrics",
        "poc_catalog.agentops_test.12345_otel_spans",
    ]
    assert all(change.principal == "app-client-id" for _, _, change in calls)
