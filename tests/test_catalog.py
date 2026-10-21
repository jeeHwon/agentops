from __future__ import annotations

import pytest

from codex_agentops.catalog import catalog_record
from codex_agentops.registry import AgentVersion, RegistryLocation


def sample_release() -> AgentVersion:
    return AgentVersion(
        agent_id="sample-agent", version="1.0.0", name="Sample Agent",
        description="Generic sample", agent_type="skill-based-agent", model="model",
        artifact_path="/Volumes/catalog/schema/volume/sample.tar.gz",
        artifact_sha256="a" * 64, artifact_size=100, harness_sha256="b" * 64,
        skills_sha256="c" * 64, skill_names=("sample-skill",),
        registered_by="developer@example.com", registered_at="2026-01-01",
        release_manifest={
            "spec": {"agent_id": "sample-agent", "build_type": "skill", "owner": "developer@example.com"},
            "skills": [{"uc_name": "catalog.schema.sample-skill", "alias": "sample-skill"}],
            "files": {"skills/sample-skill/SKILL.md": "secret"},
        },
    )


def test_catalog_record_exports_metadata_without_archive_or_content():
    result = catalog_record(sample_release(), RegistryLocation("catalog", "schema"))
    assert result["source_registry"] == "catalog.schema"
    assert result["uc_skills"] == ["catalog.schema.sample-skill"]
    assert result["spec"]["build_type"] == "skill"
    assert "artifact_path" not in result
    assert "files" not in result
    assert "release_manifest" not in result


def test_catalog_record_rejects_missing_skill_reference():
    release = sample_release()
    release.release_manifest["skills"] = []
    with pytest.raises(ValueError, match="UC Skill"):
        catalog_record(release, RegistryLocation("catalog", "schema"))
