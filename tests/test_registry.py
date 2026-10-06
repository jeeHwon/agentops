from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from codex_agentops.manifest import create_agent, validate_agent
from codex_agentops.registry import (
    RegistryError,
    build_agent_archive,
    extract_agent_archive,
    parse_registry,
)


def test_registry_reference_requires_catalog_and_schema():
    location = parse_registry("main.agentops", volume="artifacts", table="versions")
    assert location.table_name == "main.agentops.versions"
    assert location.volume_root == "/Volumes/main/agentops/artifacts"

    with pytest.raises(RegistryError):
        parse_registry("main")
    with pytest.raises(RegistryError):
        parse_registry("main.bad-name")


def test_agent_archive_is_deterministic_and_round_trips(tmp_path):
    root = create_agent("claims-helper", tmp_path / "source")
    first = build_agent_archive(root)
    second = build_agent_archive(root)

    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert ".agents/skills/example-skill/SKILL.md" in first.files
    assert ".codex/config.toml" in first.files
    assert ".codex/agents/validator.toml" in first.files

    destination = tmp_path / "loaded"
    extract_agent_archive(first.content, destination)
    manifest = validate_agent(destination)
    assert manifest.agent_id == "claims-helper"
    assert (destination / "AGENTS.md").read_bytes() == (root / "AGENTS.md").read_bytes()


def test_archive_rejects_path_traversal(tmp_path):
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            info = tarfile.TarInfo("../secret")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(RegistryError, match="Unsafe artifact path"):
        extract_agent_archive(raw.getvalue(), tmp_path / "loaded")


def test_archive_rejects_symbolic_links(tmp_path):
    root = create_agent("claims-helper", tmp_path / "source")
    (root / ".agents/skills/example-skill/reference.txt").symlink_to(root / "AGENTS.md")
    with pytest.raises(RegistryError, match="Symbolic links"):
        build_agent_archive(root)


def test_release_agent_archive_contains_release_contract_and_codex_subagent():
    sample = Path(__file__).parents[1] / "samples" / "release-agent"
    archive = build_agent_archive(sample)

    assert "agent.yaml" in archive.files
    assert ".codex/agents/validator.toml" in archive.files
    assert "release.yaml" not in archive.files
    assert "spec.md" not in archive.files
    assert "CLAUDE.md" not in archive.files
    assert ".runtime/config.yaml" not in archive.files
    assert ".aops/runtime/config.yaml" not in archive.files
