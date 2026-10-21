from __future__ import annotations

from types import SimpleNamespace

import pytest

import codex_agentops.cli as cli
from codex_agentops.cli import build_parser
from codex_agentops.config import Settings
from codex_agentops.databricks import Profile
from codex_agentops.registry_context import RegistryContext, load_registry_context


def test_supported_commands_match_harness_lifecycle():
    parser = build_parser()
    commands = {
        "configure": [],
        "use": ["--profile", "test-profile", "--registry", "poc_catalog.agentops_test"],
        "init": ["test-agent"],
        "validate": [],
        "registry-init": [],
        "list": [],
        "catalog-export": [],
        "register": [".", "--version", "1.0.0"],
        "load": ["test-agent"],
        "doctor": [],
        "status": [],
        "audit-calls": ["--session-id", "synthetic-session"],
        "flush": [],
    }
    for command, arguments in commands.items():
        assert parser.parse_args([command, *arguments]).command == command

    for removed in ("publish", "assemble", "deploy"):
        with pytest.raises(SystemExit):
            parser.parse_args([removed])


def test_audit_calls_prints_only_safe_inventory(monkeypatch, tmp_path, capsys):
    import json

    from codex_agentops.manifest import create_agent
    from codex_agentops.outbox import Outbox
    from codex_agentops.events import from_hook_payload
    from codex_agentops.manifest import load_manifest

    root = create_agent("test-agent", tmp_path / "agent")
    outbox = Outbox(tmp_path / "outbox.db")
    for name, extra in (
        ("UserPromptSubmit", {"prompt": "secret"}),
        ("PostToolUse", {"tool_name": "Bash", "tool_use_id": "call-1",
                         "tool_input": {"command": "secret"}, "tool_response": {"output": "secret"}}),
        ("Stop", {"last_assistant_message": "secret"}),
    ):
        event = from_hook_payload(
            {"hook_event_name": name, "session_id": "s", "turn_id": "t",
             "cwd": str(root), **extra}, load_manifest(root),
            Settings("test-profile", "https://example.cloud.databricks.com", capture_content=True),
        )
        outbox.append(event)
    monkeypatch.setattr(cli, "outbox_path", lambda: tmp_path / "outbox.db")
    assert cli.main(["audit-calls", "--session-id", "s"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["call_id"] == "call-1"
    assert "secret" not in lines[0]


def test_init_warns_that_codex_needs_gateway_provider(monkeypatch, capsys, tmp_path):
    root = tmp_path / "sample-agent"
    monkeypatch.setattr(cli, "create_agent", lambda agent_id, path: root)
    monkeypatch.setattr(
        cli, "_codex_runtime_builder", lambda: SimpleNamespace(prepare=lambda path: None)
    )

    assert cli.main(["init", "sample-agent", "--path", str(root)]) == 0
    assert "Codex 모델 공급자와 인증을 먼저 확인하세요" in capsys.readouterr().out


def test_configure_accepts_explicit_profile_and_defaults():
    args = build_parser().parse_args(
        [
            "configure",
            "--profile",
            "test-profile",
            "--experiment",
            "/Users/example/agentops",
            "--warehouse-id",
            "warehouse-1",
            "--registry",
            "poc_catalog.agentops_test",
        ]
    )
    assert args.profile == "test-profile"
    assert args.experiment == "/Users/example/agentops"
    assert args.registry == "poc_catalog.agentops_test"


def test_register_and_load_use_configured_defaults():
    register = build_parser().parse_args(
        ["register", "./my-agent", "--version", "1.0.0"]
    )
    loaded = build_parser().parse_args(
        ["load", "claims-helper", "--version", "2.0.0", "--path", "./loaded"]
    )
    assert register.profile is None
    assert register.registry is None
    assert register.version == "1.0.0"
    assert loaded.agent_id == "claims-helper"
    assert loaded.version == "2.0.0"
    assert str(loaded.path) == "loaded"


def test_registry_client_uses_configured_defaults(monkeypatch):
    args = build_parser().parse_args(["list"])
    settings = Settings(
        profile="test-profile",
        host="https://example.cloud.databricks.com",
        warehouse_id="warehouse-1",
        registry="poc_catalog.agentops_test",
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_registry_context", lambda: None)
    monkeypatch.setattr(
        cli,
        "list_profiles",
        lambda: [Profile("test-profile", "https://example.cloud.databricks.com")],
    )
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))

    client = cli._registry_client(args)
    assert client.profile == "test-profile"
    assert client.warehouse_id == "warehouse-1"
    assert client.location.schema_name == "poc_catalog.agentops_test"


def test_use_selects_registry_without_changing_monitoring_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path))
    original = '{"profile": "old-profile", "experiment": "/Shared/old"}'
    (tmp_path / "config.json").write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "list_profiles", lambda: [
        Profile("test-profile", "https://example.cloud.databricks.com"),
    ])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    assert cli.main(["use", "--profile", "test-profile", "--registry", "poc_catalog.agentops_test",
                     "--warehouse-id", "warehouse-1"]) == 0
    assert load_registry_context() == RegistryContext(
        "test-profile", "https://example.cloud.databricks.com", "poc_catalog.agentops_test", "warehouse-1"
    )
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == original
    assert (tmp_path / "registry.json").stat().st_mode & 0o777 == 0o600
    assert "MLflow·Codex 설정은 변경하지 않았습니다" in capsys.readouterr().out


def test_selected_registry_lists_and_loads_without_monitoring_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    host = "https://sandbox.cloud.databricks.com"
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("sandbox", host)])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    release = SimpleNamespace(
        agent_id="sample-agent", version="1.0.0", name="Sample Agent",
        skill_names=["sample-skill"], registered_by="example",
    )
    calls = []

    class StubRegistry:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def list(self, **kwargs):
            return [release]

        def load(self, agent_id, *, version, destination):
            assert (agent_id, version, destination) == (
                "sample-agent", "1.0.0", tmp_path / "sample-agent",
            )
            return destination, release

    monkeypatch.setattr(cli, "RegistryClient", StubRegistry)
    assert cli.main(["use", "--profile", "sandbox", "--registry", "test_catalog.test_schema",
                     "--warehouse-id", "warehouse-2"]) == 0
    assert cli.main(["list"]) == 0
    assert cli.main(["load", "sample-agent", "--version", "1.0.0"]) == 0
    assert len(calls) == 2
    assert all(call["profile"] == "sandbox" and call["warehouse_id"] == "warehouse-2" for call in calls)
    output = capsys.readouterr().out
    assert "sample-agent@1.0.0" in output
    assert "aops validate" not in output


def test_selected_registry_drives_list_without_extra_flags(monkeypatch):
    args = build_parser().parse_args(["list"])
    selected = RegistryContext("sandbox", "https://sandbox.cloud.databricks.com", "test_catalog.test_schema", "warehouse-2")
    monkeypatch.setattr(cli, "load_registry_context", lambda: selected)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(
        "old-profile", "https://old.cloud.databricks.com", warehouse_id="warehouse-1",
        registry="old_catalog.old_schema",
    ))
    monkeypatch.setattr(cli, "list_profiles", lambda: [
        Profile("sandbox", selected.host), Profile("old-profile", "https://old.cloud.databricks.com"),
    ])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))
    current = cli._registry_client(args)
    assert (current.profile, current.warehouse_id, current.location.schema_name) == (
        "sandbox", "warehouse-2", "test_catalog.test_schema",
    )
    legacy = cli._registry_client(build_parser().parse_args(["list", "--profile", "old-profile"]))
    assert (legacy.profile, legacy.warehouse_id, legacy.location.schema_name) == (
        "old-profile", "warehouse-1", "old_catalog.old_schema",
    )
    with pytest.raises(cli.ConfigError, match="게시 대상이 다릅니다"):
        cli._registry_client(build_parser().parse_args(["register", ".", "--version", "2.0.0"]))
    with pytest.raises(cli.ConfigError, match="게시 대상이 다릅니다"):
        cli._registry_client(build_parser().parse_args(["registry-init"]))
    writable = cli._registry_client(build_parser().parse_args([
        "register", ".", "--version", "2.0.0", "--profile", "sandbox",
        "--registry", "test_catalog.test_schema", "--warehouse-id", "warehouse-2",
    ]))
    assert (writable.profile, writable.warehouse_id, writable.location.schema_name) == (
        "sandbox", "warehouse-2", "test_catalog.test_schema",
    )


def test_selected_registry_rejects_changed_workspace_host(monkeypatch):
    monkeypatch.setattr(cli, "load_registry_context", lambda: RegistryContext(
        "sandbox", "https://original.cloud.databricks.com", "test_catalog.test_schema", "warehouse-2"
    ))
    monkeypatch.setattr(cli, "load_settings", lambda: Settings("sandbox", "https://original.cloud.databricks.com"))
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("sandbox", "https://other.cloud.databricks.com")])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    with pytest.raises(cli.ConfigError, match="aops use"):
        cli._registry_client(build_parser().parse_args(["load", "sample-agent"]))
