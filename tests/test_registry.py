from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from codex_agentops.manifest import create_agent, validate_agent
from codex_agentops.registry import (
    AgentVersion,
    RegistryClient,
    RegistryError,
    build_agent_archive,
    extract_agent_archive,
    parse_registry,
    _row_to_agent_version,
)
from codex_agentops.release import (
    build_release,
    load_release,
    release_to_dict,
    write_release_metadata,
)


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def test_registry_type_is_not_silently_reinterpreted():
    row = ["current", "1.0.0", "Current", "Current", "skill-based-agent", "model", "artifact", "hash",
           1, "harness-hash", "skills-hash", "[]", "owner", "2026-10-15", json.dumps({"schema_version": 2})]
    assert _row_to_agent_version(row).agent_type == "skill-based-agent"
    with pytest.raises(RegistryError, match="unsupported Agent version or type"):
        _row_to_agent_version([*row[:4], "harness", *row[5:]])
    with pytest.raises(RegistryError, match="unsupported Agent version or type"):
        _row_to_agent_version([row[0], "1.0", *row[2:]])


def _release(root: Path, version: str = "1.0.0") -> None:
    write_release_metadata(build_release(root, version, "main.agentops"))


def test_registry_reference_requires_catalog_and_schema():
    location = parse_registry("main.agentops", volume="artifacts")
    assert location.agents_table_name == "main.agentops.agents"
    assert location.versions_table_name == "main.agentops.agent_versions"
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
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
    _release(root)
    archive = build_agent_archive(root)

    assert "SPEC.md" in archive.files
    assert "AGENTS.md" in archive.files
    assert "skills/release-summary/SKILL.md" in archive.files
    assert "skills/release-validation/SKILL.md" in archive.files
    assert "agents/validator/AGENTS.md" in archive.files
    assert ".aops/release.json" in archive.files
    assert "agent.yaml" not in archive.files
    assert not any("mcp" in name.lower() for name in archive.files)


def test_load_checks_uc_skill_access_before_artifact_download(tmp_path):
    release = build_release(SAMPLE, "1.0.0", "main.agentops")
    release_manifest = release_to_dict(release)

    class DeniedPublisher:
        called = False

        def verify_access(self, value):
            self.called = True
            raise RuntimeError("denied")

    class Files:
        downloaded = False

        def download(self, path):
            self.downloaded = True
            raise AssertionError("download must happen after authorization")

    class Workspace:
        files = Files()

    publisher = DeniedPublisher()
    client = RegistryClient(
        profile="test",
        warehouse_id="warehouse",
        location=parse_registry("main.agentops"),
        workspace=Workspace(),
        skill_publisher=publisher,
    )
    registered = AgentVersion(
        agent_id=release.agent_id,
        version=release.release_version,
        name="Sample",
        description="Sample",
        agent_type="skill-based-agent",
        model=release.model,
        artifact_path="/Volumes/main/agentops/artifacts/sample.tar.gz",
        artifact_sha256="0" * 64,
        artifact_size=1,
        harness_sha256=release.harness_sha256,
        skills_sha256=release.skills_sha256,
        skill_names=tuple(skill.alias for skill in release.skills),
        registered_by="owner@example.com",
        registered_at="2026-10-13",
        release_manifest=release_manifest,
    )
    client.get = lambda *args, **kwargs: registered  # type: ignore[method-assign]

    with pytest.raises(RegistryError, match="Cannot authorize"):
        client.load(release.agent_id, destination=tmp_path / "loaded")
    assert publisher.called is True
    assert Workspace.files.downloaded is False


def test_unsupported_release_cannot_be_loaded(tmp_path):
    release = build_release(SAMPLE, "1.0.0", "main.agentops")
    old_manifest = release_to_dict(release)
    old_manifest["schema_version"] = 1
    registered = AgentVersion(
        agent_id=release.agent_id, version="1.0.0", name="Old", description="Old",
        agent_type="skill-based-agent", model=release.model, artifact_path="unused",
        artifact_sha256="0" * 64, artifact_size=1, harness_sha256=release.harness_sha256,
        skills_sha256=release.skills_sha256, skill_names=("release-summary",),
        registered_by="test", registered_at="2026-01-01", release_manifest=old_manifest,
    )
    client = RegistryClient(
        profile="test", warehouse_id="warehouse", location=parse_registry("main.agentops"),
        workspace=object(), skill_publisher=object(),
    )
    client.get = lambda *args, **kwargs: registered
    with pytest.raises(RegistryError, match="Invalid Registry Release"):
        client.load("release-sample-agent", version="1.0.0", destination=tmp_path / "old")


def test_registry_initializes_current_lifecycle_tables():
    client = RegistryClient(
        profile="test",
        warehouse_id="warehouse",
        location=parse_registry("main.agentops"),
        workspace=object(),
        skill_publisher=object(),
    )
    statements: list[str] = []
    def execute(statement, parameters=None):
        statements.append(statement)
        if statement.startswith("DESCRIBE TABLE"):
            from codex_agentops.registry import AGENT_TABLE_COLUMNS, VERSION_TABLE_COLUMNS
            columns = VERSION_TABLE_COLUMNS if "agent_versions" in statement else AGENT_TABLE_COLUMNS
            return [[name] for name in columns]
        return []

    client._execute_sql = execute

    client._initialize_tables()

    sql = "\n".join(statements)
    for table in (
        "`main`.`agentops`.`agents`",
        "`main`.`agentops`.`agent_versions`",
        "`main`.`agentops`.`agent_version_skills`",
        "`main`.`agentops`.`release_gates`",
        "`main`.`agentops`.`agent_events`",
    ):
        assert table in sql
    assert "agent_releases" not in sql
    assert "uc_permissions ARRAY<STRUCT<object: STRING, privilege: STRING>>" in sql
    assert "agent_version STRING" in sql
    assert "source_system STRING" not in sql
    assert "ALTER TABLE" not in sql


def test_registry_rejects_incompatible_existing_columns():
    client = RegistryClient(
        profile="test", warehouse_id="warehouse", location=parse_registry("main.agentops"),
        workspace=object(), skill_publisher=object(),
    )
    statements: list[str] = []

    def execute(statement, parameters=None):
        statements.append(statement)
        if statement.startswith("DESCRIBE TABLE"):
            return [[name] for name in (
                "agent_version", "build_type", "serving_type", "origin", "security_level",
                "uc_permissions", "flagship_model", "model_service",
            )]
        return []

    client._execute_sql = execute
    with pytest.raises(RegistryError, match="Incompatible Registry table"):
        client._initialize_tables()
    assert not any("ALTER TABLE" in statement for statement in statements)


def test_registry_rejects_incompatible_uc_permissions_type():
    from codex_agentops.registry import AGENT_TABLE_COLUMNS, VERSION_TABLE_COLUMNS

    client = RegistryClient(
        profile="test", warehouse_id="warehouse", location=parse_registry("main.agentops"),
        workspace=object(), skill_publisher=object(),
    )

    def execute(statement, parameters=None):
        if statement.startswith("DESCRIBE TABLE"):
            columns = VERSION_TABLE_COLUMNS if "agent_versions" in statement else AGENT_TABLE_COLUMNS
            return [[name, "array<string>" if name == "uc_permissions" else "string"] for name in columns]
        return []

    client._execute_sql = execute
    with pytest.raises(RegistryError, match="uc_permissions_type=array<string>"):
        client._initialize_tables()


def test_registry_writes_structured_spec_columns(tmp_path):
    import shutil

    source = tmp_path / "agent"
    shutil.copytree(SAMPLE, source, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
    spec = source / "SPEC.md"
    spec.write_text(spec.read_text().replace(
        "uc_permissions: []",
        "uc_permissions:\n  - object: catalog.schema.table\n    privilege: SELECT",
    ))
    release = build_release(source, "1.0.0", "main.agentops")
    client = RegistryClient(
        profile="test", warehouse_id="warehouse", location=parse_registry("main.agentops"),
        workspace=object(), skill_publisher=object(),
    )
    statements = []
    client._execute_sql = lambda statement, parameters=None: statements.append((statement, parameters)) or []
    client._register_metadata(
        manifest=validate_agent(source), release=release, release_manifest=release_to_dict(release),
        version="1.0.0", artifact_path="/Volumes/main/agentops/a.tar.gz",
        artifact_sha256="0" * 64, artifact_size=100,
        harness_sha256=release.harness_sha256, skills_sha256=release.skills_sha256,
    )
    version_sql, version_params = statements[0]
    assert "from_json(:uc_permissions, 'array<struct<object:string,privilege:string>>')" in version_sql
    assert json.loads(version_params["uc_permissions"]) == [
        {"object": "catalog.schema.table", "privilege": "SELECT"}
    ]
    assert version_params["agent_version"] == "1.0.0"
    assert version_params["security_level"] == "일반"
    assert "source_system" not in version_params
    assert "required_uc_permissions" not in version_params
