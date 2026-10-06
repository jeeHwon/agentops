from __future__ import annotations

from codex_agentops.cli import build_parser


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
        ]
    )

    assert args.profile == "test-profile"
    assert args.experiment == "/Users/example/agentops"
    assert args.warehouse_id == "warehouse-1"
    assert parser.prog == "aops"


def test_register_requires_explicit_profile_registry_and_version():
    parser = build_parser()
    args = parser.parse_args(
        [
            "register",
            "./my-agent",
            "--profile",
            "test-profile",
            "--registry",
            "poc_catalog.agentops_test",
            "--version",
            "1.0.0",
        ]
    )

    assert args.profile == "test-profile"
    assert args.registry == "poc_catalog.agentops_test"
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


def test_publish_and_assemble_require_explicit_profile():
    parser = build_parser()
    publish = parser.parse_args(
        ["publish", "./release-agent", "--profile", "test-profile"]
    )
    assemble = parser.parse_args(
        [
            "assemble",
            "./release-agent",
            "--profile",
            "test-profile",
            "--output",
            "./runtime",
            "--force",
        ]
    )

    assert publish.command == "publish"
    assert publish.profile == "test-profile"
    assert assemble.command == "assemble"
    assert assemble.profile == "test-profile"
    assert str(assemble.output) == "runtime"
    assert assemble.force is True


def test_deploy_requires_explicit_profile_and_model_endpoint():
    parser = build_parser()
    args = parser.parse_args(
        [
            "deploy",
            "./release-agent",
            "--profile",
            "test-profile",
            "--model-endpoint",
            "databricks-gpt-5-1",
            "--trace-schema",
            "poc_catalog.agentops_test",
            "--build-only",
        ]
    )

    assert args.command == "deploy"
    assert args.profile == "test-profile"
    assert args.model_endpoint == "databricks-gpt-5-1"
    assert args.trace_schema == "poc_catalog.agentops_test"
    assert args.build_only is True
