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
from codex_agentops.release import build_release, load_release, write_release_metadata


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def _release(root: Path, version: str = "1.0.0") -> None:
    write_release_metadata(build_release(root, version, "main.agentops"))


def test_registry_reference_requires_catalog_and_schema():
    location = parse_registry("main.agentops", volume="artifacts", table="releases")
    assert location.table_name == "main.agentops.releases"
    assert location.volume_root == "/Volumes/main/agentops/artifacts"
    with pytest.raises(RegistryError):
        parse_registry("main")
    with pytest.raises(RegistryError):
        parse_registry("main.bad-name")


def test_agent_archive_is_deterministic_and_round_trips(tmp_path):
    root = create_agent("claims-helper", tmp_path / "source")
    _release(root)
    first = build_agent_archive(root)
    second = build_agent_archive(root)

    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert "skills/request-structurer/SKILL.md" in first.files
    assert "agents/validator/AGENTS.md" in first.files
    assert ".aops/release.json" in first.files
    assert not any(name.startswith(".codex/") for name in first.files)
    assert not any(name.startswith(".agents/") for name in first.files)

    destination = tmp_path / "loaded"
    extract_agent_archive(first.content, destination)
    assert validate_agent(destination).agent_id == "claims-helper"
    assert load_release(destination).release_version == "1.0.0"


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
    (root / "skills/request-structurer/references/escape.md").symlink_to(root / "AGENTS.md")
    with pytest.raises((RegistryError, RuntimeError), match="Symbolic links"):
        _release(root)


def test_sample_archive_contains_only_source_and_release_contract(tmp_path):
    import shutil

    root = tmp_path / "release-agent"
    shutil.copytree(SAMPLE, root)
    _release(root)
    archive = build_agent_archive(root)

    assert "spec.md" in archive.files
    assert "AGENTS.md" in archive.files
    assert "skills/release-summary/SKILL.md" in archive.files
    assert "skills/release-validation/SKILL.md" in archive.files
    assert "agents/validator/AGENTS.md" in archive.files
    assert ".aops/release.json" in archive.files
    assert "agent.yaml" not in archive.files
    assert not any("mcp" in name.lower() for name in archive.files)
