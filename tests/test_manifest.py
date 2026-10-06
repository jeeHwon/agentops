from __future__ import annotations

import pytest
import yaml

from codex_agentops.manifest import (
    ManifestError,
    calculate_definition_checksums,
    create_agent,
    find_agent_root,
    load_manifest,
    validate_agent,
)


def test_create_agent_contains_only_harness_definition(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)

    assert manifest.agent_id == "claims-helper"
    assert manifest.description
    assert (root / "README.md").is_file()
    assert (root / "spec.md").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "CLAUDE.md").is_file()
    skill_path = root / ".agents/skills/example-skill/SKILL.md"
    assert skill_path.is_file()
    assert find_agent_root(root / ".agents" / "skills") == root
    assert not (root / "package.json").exists()
    assert not (root / "server").exists()
    assert not (root / "client").exists()

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "aops validate" in readme
    assert "aops register" in readme
    assert "aops load" in readme
    assert "AppKit 서버나 UI 코드를 포함하지 않습니다" in readme

    _, frontmatter, body = skill_path.read_text(encoding="utf-8").split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "example-skill"
    assert metadata["description"]
    assert "## 수행 절차" in body
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
    assert "## 목적" in body
    assert "## 대상 사용자와 입력" in body
    assert "## 출력" in body
    assert "## 업무 범위" in body
    assert "## 성공 기준" in body
    assert "## UC 필요 권한" in body


def test_checksums_change_when_harness_or_skill_changes(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    original = calculate_definition_checksums(root)

    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n- 새 지침\n", encoding="utf-8")
    harness_changed = calculate_definition_checksums(root)
    assert harness_changed.harness != original.harness
    assert harness_changed.combined_skills == original.combined_skills

    skill = root / ".agents/skills/example-skill/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\n- 추가 절차\n", encoding="utf-8")
    skill_changed = calculate_definition_checksums(root)
    assert skill_changed.combined_skills != harness_changed.combined_skills


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


def test_validate_agent_requires_all_harness_files(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    (root / "spec.md").unlink()
    with pytest.raises(ManifestError, match="Missing or empty"):
        validate_agent(root)
