from __future__ import annotations

from types import SimpleNamespace

import codex_agentops.cli as cli
from codex_agentops.cli import build_parser
from codex_agentops.config import Settings
from codex_agentops.databricks import Profile


def test_configure_accepts_portable_experiment_and_warehouse_options():
    parser = build_parser()
    args = parser.parse_args(
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
    assert args.warehouse_id == "warehouse-1"
    assert args.registry == "poc_catalog.agentops_test"
    assert parser.prog == "aops"


def test_register_accepts_configured_defaults_and_requires_version():
    parser = build_parser()
    args = parser.parse_args(
        [
            "register",
            "./my-agent",
            "--version",
            "1.0.0",
        ]
    )

    assert args.profile is None
    assert args.registry is None
    assert args.version == "1.0.0"


def test_load_accepts_optional_version_and_path():
    parser = build_parser()
    args = parser.parse_args(
        [
            "load",
            "claims-helper",
            "--version",
            "2.0.0",
            "--path",
            "./loaded",
            "--profile",
            "test-profile",
            "--registry",
            "poc_catalog.agentops_test",
        ]
    )

    assert args.agent_id == "claims-helper"
    assert args.version == "2.0.0"
    assert str(args.path) == "loaded"
    assert args.no_sync is False


def test_load_keeps_no_assemble_as_no_sync_compatibility_alias():
    args = build_parser().parse_args(["load", "claims-helper", "--no-assemble"])

    assert args.no_sync is True


def test_publish_and_assemble_accept_configured_profile():
    parser = build_parser()
    publish = parser.parse_args(["publish", "./release-agent"])
    assemble = parser.parse_args(
        [
            "assemble",
            "./release-agent",
            "--output",
            "./runtime",
            "--force",
        ]
    )

    assert publish.command == "publish"
    assert publish.profile is None
    assert assemble.command == "assemble"
    assert assemble.profile is None
    assert str(assemble.output) == "runtime"
    assert assemble.force is True


def test_deploy_accepts_configured_profile_and_requires_model_endpoint():
    parser = build_parser()
    args = parser.parse_args(
        [
            "deploy",
            "./release-agent",
            "--model-endpoint",
            "databricks-gpt-5-1",
            "--trace-schema",
            "poc_catalog.agentops_test",
            "--build-only",
        ]
    )

    assert args.command == "deploy"
    assert args.profile is None
    assert args.model_endpoint == "databricks-gpt-5-1"
    assert args.trace_schema == "poc_catalog.agentops_test"
    assert args.build_only is True


def test_list_accepts_all_configured_defaults():
    args = build_parser().parse_args(["list"])

    assert args.profile is None
    assert args.registry is None
    assert args.warehouse_id is None


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
