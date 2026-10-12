from __future__ import annotations

from types import SimpleNamespace

import pytest

import codex_agentops.cli as cli
from codex_agentops.cli import build_parser
from codex_agentops.config import Settings
from codex_agentops.databricks import Profile


def test_supported_commands_match_harness_lifecycle():
    parser = build_parser()
    commands = {
        "configure": [],
        "init": ["test-agent"],
        "validate": [],
        "registry-init": [],
        "list": [],
        "register": [".", "--version", "1.0.0"],
        "load": ["test-agent"],
        "doctor": [],
        "status": [],
        "flush": [],
    }
    for command, arguments in commands.items():
        assert parser.parse_args([command, *arguments]).command == command

    for removed in ("publish", "assemble", "deploy"):
        with pytest.raises(SystemExit):
            parser.parse_args([removed])


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
