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
