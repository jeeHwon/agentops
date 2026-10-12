from __future__ import annotations

import pytest

from codex_agentops.manifest import (
    ManifestError,
    calculate_definition_checksums,
    create_agent,
    find_agent_root,
    load_manifest,
    load_skills,
    load_subagents,
    validate_agent,
)
from codex_agentops.release import CodexRuntimeBuilder


def test_create_agent_uses_markdown_source_and_generated_codex_runtime(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)

    assert manifest.agent_id == "claims-helper"
    assert manifest.schema_version == 1
    assert manifest.agent_type == "harness"
    assert (root / "spec.md").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "README.md").is_file()
    assert (root / "skills/request-structurer/SKILL.md").is_file()
    assert (root / "agents/validator/AGENTS.md").is_file()
    assert not (root / "agent.yaml").exists()
    assert not (root / "CLAUDE.md").exists()
    assert find_agent_root(root / "skills/request-structurer") == root

    runtime = CodexRuntimeBuilder().prepare(root)
    assert runtime.runtime_root == root
    assert (root / ".agents/skills/request-structurer/SKILL.md").is_file()
    assert (root / ".codex/config.toml").is_file()
    assert "http://127.0.0.1:4319/v1/logs" in (
        root / ".codex/config.toml"
    ).read_text(encoding="utf-8")
    assert (root / ".codex/agents/validator.toml").is_file()
    assert (root / ".aops/runtime.json").is_file()
    assert validate_agent(root) == manifest


def test_spec_contains_governance_defaults(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)

    assert manifest.service_criticality == "low"
    assert manifest.data_sensitivity == "internal"
    assert manifest.security_access_level == "standard"
    assert manifest.required_uc_permissions == ()
    assert manifest.use_flagship_model is False
    assert manifest.model == "system.ai.gpt-5-6-sol"


def test_checksums_change_independently_for_harness_and_skills(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    original = calculate_definition_checksums(root)

    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n- 새 지침\n", encoding="utf-8")
    harness_changed = calculate_definition_checksums(root)
    assert harness_changed.harness != original.harness
    assert harness_changed.combined_skills == original.combined_skills

    skill = root / "skills/request-structurer/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\n- 추가 절차\n", encoding="utf-8")
    skill_changed = calculate_definition_checksums(root)
    assert skill_changed.combined_skills != harness_changed.combined_skills


def test_skill_and_subagent_follow_standard_source_contract(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    skills = load_skills(root)
    subagents = load_subagents(root, set(skills))
    assert set(skills) == {"request-structurer"}
    assert subagents["validator"].skills == ("request-structurer",)


def test_create_agent_rejects_non_kebab_id(tmp_path):
    with pytest.raises(ManifestError):
        create_agent("Claims_Helper", tmp_path / "bad")


def test_validate_agent_rejects_skill_name_that_does_not_match_folder(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    skill_path = root / "skills/request-structurer/SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "name: request-structurer", "name: renamed-skill", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="must match its folder"):
        validate_agent(root)


def test_validate_agent_rejects_scripts_and_symbolic_links(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    scripts = root / "skills/request-structurer/scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print('no')\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="references"):
        validate_agent(root)

    (scripts / "run.py").unlink()
    scripts.rmdir()
    (root / "skills/request-structurer/references/escape.md").symlink_to(
        root / "AGENTS.md"
    )
    with pytest.raises(ManifestError, match="Symbolic links"):
        validate_agent(root)


def test_validate_agent_rejects_unknown_subagent_skill(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    path = root / "agents/validator/AGENTS.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  - request-structurer", "  - missing-skill"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="unknown Skills"):
        validate_agent(root)
