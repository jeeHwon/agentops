from __future__ import annotations

import pytest
import yaml

from codex_agentops.manifest import (
    ManifestError,
    create_agent,
    find_agent_root,
    load_manifest,
    validate_agent,
)


def test_create_agent_contains_only_standard_definition_files(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)
    assert manifest.agent_id == "claims-helper"
    assert (root / "README.md").is_file()
    assert (root / "spec.md").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "CLAUDE.md").is_file()
    skill_path = root / ".agents/skills/example-skill/SKILL.md"
    assert skill_path.is_file()
    assert not list(root.rglob("*.py"))
    assert find_agent_root(root / ".agents" / "skills") == root

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "## What to edit" in readme
    assert "1. **Define the operating contract in `spec.md`.**" in readme
    assert "## Validate and run" in readme
    assert "## Add a Skill" in readme
    assert "codex-agentops validate" in readme

    skill = skill_path.read_text(encoding="utf-8")
    _, frontmatter, body = skill.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "example-skill"
    assert metadata["description"]
    assert "## Procedure" in body
    assert validate_agent(root) == manifest


def test_created_spec_contains_editable_governance_defaults(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    spec = (root / "spec.md").read_text(encoding="utf-8")
    _, frontmatter, body = spec.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata == {
        "agent_type": "harness",
        "service_criticality": "low",
        "data_sensitivity": "internal",
        "security_access_level": "standard",
        "required_uc_permissions": [],
        "use_flagship_model": False,
    }
    assert "## Purpose" in body
    assert "## Users and inputs" in body
    assert "## Outputs" in body
    assert "## Scope" in body
    assert "## Success criteria" in body
    assert "## Flagship model justification" in body


def test_create_agent_rejects_non_kebab_id(tmp_path):
    with pytest.raises(ManifestError):
        create_agent("Claims_Helper", tmp_path / "bad")


def test_validate_agent_rejects_skill_name_that_does_not_match_folder(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    skill_path = root / ".agents/skills/example-skill/SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "name: example-skill", "name: renamed-skill", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="must match its folder"):
        validate_agent(root)
