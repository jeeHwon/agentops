from __future__ import annotations

from types import SimpleNamespace

import pytest

import codex_agentops.cli as cli
from codex_agentops.cli import build_parser
from codex_agentops.config import Settings, load_settings, save_settings
from codex_agentops.databricks import Profile


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_AGENTOPS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_AGENTOPS_DATA_DIR", str(tmp_path / "data"))


def test_supported_commands_match_harness_lifecycle():
    parser = build_parser()
    commands = {
        "configure": [],
        "init": ["test-agent"],
        "validate": [],
        "registry-init": [],
        "list": [],
        "catalog-export": [],
        "register": [".", "--version", "1.0.0"],
        "load": ["test-agent"],
        "doctor": [],
        "status": [],
        "collector-restart": [],
        "audit-calls": ["--session-id", "synthetic-session"],
        "flush": [],
        "deploy": [],
        "link": ["resource"],
        "run": [],
        "traces": ["test-agent", "--version", "1.0.0"],
        "trace": ["test-agent", "trace-id", "--version", "1.0.0"],
        "evaluate": ["test-agent", "--version", "1.0.0", "--data", "data.json",
                     "--judge-model", "databricks:/judge"],
        "review": ["test-agent", "--version", "1.0.0", "--run-id", "run", "--trace-id", "trace"],
        "production": ["test-agent"],
        "promote": ["test-agent", "--version", "1.0.0", "--expected-version", "none"],
        "rollback": ["test-agent", "--version", "1.0.0", "--expected-version", "2.0.0"],
    }
    for command, arguments in commands.items():
        assert parser.parse_args([command, *arguments]).command == command

    for removed in ("publish", "assemble", "use", "tracing-configure"):
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


def shared_environment(monkeypatch):
    host = "https://example.cloud.databricks.com"
    settings = Settings(
        "chosen", host, warehouse_id="warehouse", registry="main.aops", experiment_id="123",
    )
    save_settings(settings)
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("chosen", host)])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    return settings


def test_configure_is_shared_and_does_not_require_or_modify_codex(monkeypatch, capsys):
    import codex_agentops.tracing as tracing

    shared_environment(monkeypatch)
    monkeypatch.setattr(tracing, "configure_tracing", lambda settings: {**settings.tracing, "experiment_id": "321"})
    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=lambda: "test-trace"))
    for name in ("configure_otel", "ensure_collector", "validate_codex_config", "configure_turn_monitoring"):
        monkeypatch.setattr(cli, name, lambda *args: pytest.fail("Codex integration is opt-in"))
    assert cli.main(["configure"]) == 0
    saved = load_settings()
    assert saved.experiment_id == "321"
    assert saved.registry == "main.aops"
    assert saved.warehouse_id == "warehouse"
    assert saved.codex_enabled is False
    assert "test-trace" in capsys.readouterr().out


def test_configure_failure_keeps_previous_settings(monkeypatch):
    import codex_agentops.tracing as tracing

    original = shared_environment(monkeypatch)
    monkeypatch.setattr(tracing, "configure_tracing", lambda settings: {**settings.tracing, "experiment_id": "321"})

    def failed_test():
        raise RuntimeError("trace validation failed")

    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=failed_test))
    assert cli.main(["configure", "--experiment", "/Shared/new"]) == 1
    assert load_settings().experiment == original.experiment
    assert load_settings().experiment_id == original.experiment_id


def test_first_configure_never_chooses_default_profile_implicitly(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("DEFAULT", "https://example.com")])
    monkeypatch.setattr(cli, "validate_profile", lambda profile: pytest.fail("no profile was selected"))
    assert cli.main(["configure", "--registry", "main.aops"]) == 1


def test_interactive_configure_collects_common_values_once(monkeypatch):
    import codex_agentops.tracing as tracing

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    profile = Profile("selected-by-user", "https://example.com")
    monkeypatch.setattr(cli, "list_profiles", lambda: [profile])
    monkeypatch.setattr(cli, "choose_profile", lambda profiles: profile)
    monkeypatch.setattr(cli, "validate_profile", lambda profile: "user@example.com")
    answers = iter(["main.aops", "/Shared/common", "warehouse"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(tracing, "configure_tracing", lambda settings: {**settings.tracing, "experiment_id": "123"})
    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=lambda: "trace"))
    assert cli.main(["configure"]) == 0
    settings = load_settings()
    assert (settings.profile, settings.registry, settings.experiment, settings.warehouse_id) == (
        "selected-by-user", "main.aops", "/Shared/common", "warehouse",
    )
    assert settings.codex_enabled is False


def test_configure_codex_is_optional_and_preserves_common_values(monkeypatch):
    import codex_agentops.tracing as tracing

    original = shared_environment(monkeypatch)
    monkeypatch.setattr(tracing, "configure_tracing", lambda settings: original.tracing)
    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=lambda: "trace"))
    calls = []
    monkeypatch.setattr(cli, "configure_otel", lambda settings: calls.append("otel") or ("local", "/test/codex.toml"))
    monkeypatch.setattr(cli, "validate_codex_config", lambda: "codex-test")
    monkeypatch.setattr(cli, "ensure_collector", lambda settings: calls.append("collector"))
    monkeypatch.setattr(cli, "configure_turn_monitoring", lambda settings: ())
    assert cli.main(["configure", "--codex", "--capture-content"]) == 0
    assert load_settings().tracing == original.tracing
    assert load_settings().codex_enabled is True
    assert load_settings().content_capture_consent is True
    assert calls == ["otel", "collector"]
    assert cli.main(["configure", "--no-codex", "--no-content"]) == 0
    assert load_settings().codex_enabled is False
    assert load_settings().capture_content is False
    assert calls == ["otel", "collector"]


def test_configure_reuses_saved_profile_without_prompting_for_common_values(monkeypatch):
    import codex_agentops.tracing as tracing

    settings = shared_environment(monkeypatch)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "choose_profile", lambda profiles: pytest.fail("Profile was already selected"))
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Common values were already configured"))
    monkeypatch.setattr(tracing, "configure_tracing", lambda current: settings.tracing)
    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=lambda: "trace"))
    assert cli.main(["configure", "--no-content"]) == 0
    assert load_settings().tracing == settings.tracing


def test_configure_on_new_workspace_does_not_carry_over_content_consent_or_codex(monkeypatch):
    from dataclasses import replace
    import codex_agentops.tracing as tracing

    previous = shared_environment(monkeypatch)
    save_settings(replace(previous, capture_content=True, content_capture_consent=True, codex_enabled=True))
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("other", "https://other.test")])
    monkeypatch.setattr(tracing, "configure_tracing", lambda settings: {**settings.tracing, "experiment_id": "456"})
    monkeypatch.setattr(cli, "MlflowTurnExporter", lambda settings: SimpleNamespace(write_test_trace=lambda: "trace"))
    monkeypatch.setattr(cli, "configure_otel", lambda settings: pytest.fail("Codex consent is workspace-specific"))
    assert cli.main([
        "configure", "--profile", "other", "--registry", "other.registry", "--warehouse-id", "other-warehouse",
    ]) == 0
    settings = load_settings()
    assert settings.profile == "other"
    assert settings.capture_content is False
    assert settings.content_capture_consent is False
    assert settings.codex_enabled is False


def test_legacy_registry_selection_file_is_not_a_second_authority(monkeypatch):
    import json

    shared_environment(monkeypatch)
    (cli.config_path().parent / "registry.json").write_text(json.dumps({
        "profile": "other", "host": "https://other.test",
        "registry": "other.registry", "warehouse_id": "other-warehouse",
    }))
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))
    client = cli._registry_client(build_parser().parse_args(["list"]))
    assert (client.profile, client.location.schema_name) == ("chosen", "main.aops")


@pytest.mark.parametrize("arguments", [
    ["list"], ["load", "sample-agent"], ["registry-init"],
    ["register", "--version", "1.0.0"], ["review", "sample-agent", "--version", "1.0.0",
                                         "--run-id", "run", "--trace-id", "trace"],
])
def test_one_environment_drives_both_reads_and_writes(monkeypatch, arguments):
    settings = shared_environment(monkeypatch)
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))
    client = cli._registry_client(build_parser().parse_args(arguments))
    assert (client.profile, client.warehouse_id, client.location.schema_name) == (
        "chosen", "warehouse", "main.aops",
    )
    assert client.settings.tracing == settings.tracing


def test_alternate_identity_in_same_workspace_inherits_common_defaults(monkeypatch):
    settings = shared_environment(monkeypatch)
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("reviewer", settings.host)])
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))
    client = cli._registry_client(build_parser().parse_args(["list", "--profile", "reviewer"]))
    assert client.profile == "reviewer"
    assert client.location.schema_name == "main.aops"
    assert client.warehouse_id == "warehouse"


def test_different_workspace_does_not_inherit_registry_or_warehouse(monkeypatch):
    shared_environment(monkeypatch)
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("other", "https://other.test")])
    monkeypatch.setattr(cli, "RegistryClient", lambda **kwargs: SimpleNamespace(**kwargs))
    with pytest.raises(cli.RegistryError, match="aops configure"):
        cli._registry_client(build_parser().parse_args(["list", "--profile", "other"]))
    client = cli._registry_client(build_parser().parse_args([
        "list", "--profile", "other", "--registry", "other.registry", "--warehouse-id", "other-warehouse",
    ]))
    assert client.settings is None
    assert client.warehouse_id == "other-warehouse"


def test_configured_profile_rejects_changed_workspace_host(monkeypatch):
    shared_environment(monkeypatch)
    monkeypatch.setattr(cli, "list_profiles", lambda: [Profile("chosen", "https://other.test")])
    with pytest.raises(cli.ConfigError, match="aops configure"):
        cli._registry_client(build_parser().parse_args(["load", "sample-agent"]))


def test_code_init_inherits_selected_profile(monkeypatch, tmp_path):
    shared_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "create_project", lambda *args, **kwargs: calls.append(kwargs) or tmp_path)
    assert cli.main(["init", "test-agent", "--type", "code"]) == 0
    assert calls[0]["profile"] == "chosen"


@pytest.mark.parametrize("capture,flag,expected", [
    (True, [], True), (True, ["--no-content"], False),
    (False, [], False), (False, ["--capture-content"], True),
])
def test_genie_run_inherits_tracing_and_content_policy(monkeypatch, tmp_path, capture, flag, expected):
    from dataclasses import replace
    import codex_agentops.runtime as runtime
    import codex_agentops.tracing as tracing
    from codex_agentops.project import create_project

    settings = shared_environment(monkeypatch)
    save_settings(replace(settings, capture_content=capture, content_capture_consent=capture))
    root = create_project("test-agent", tmp_path / "genie", build_type="no-code")
    calls = []

    class StubStore:
        def __init__(self, profile, binding):
            assert profile == "chosen"
            assert binding == settings.tracing

        def run(self, root, invoke, **kwargs):
            calls.append(kwargs)
            return {"result": invoke()}

    monkeypatch.setattr(tracing, "TraceStore", StubStore)
    monkeypatch.setattr(runtime, "NativeRuntime", lambda profile, **kwargs: SimpleNamespace(
        check_host=lambda project: None, invoke=lambda *args, **kwargs: {"answer": "ok"},
    ))
    before = (root / "aops.yaml").read_bytes()
    assert cli.main(["run", str(root), "--prompt", "question", *flag]) == 0
    assert calls[0]["capture_content"] is expected
    assert (root / "aops.yaml").read_bytes() == before
