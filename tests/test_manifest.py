from __future__ import annotations

import pytest
import yaml

from codex_agentops.manifest import (
    ManifestError,
    calculate_definition_checksums,
    create_agent,
    find_agent_root,
    load_manifest,
    load_skills,
    load_subagents,
    read_markdown_frontmatter,
    validate_agent,
)
from codex_agentops.release import CodexRuntimeBuilder
from codex_agentops.release import build_release


def test_create_agent_uses_markdown_source_and_generated_codex_runtime(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)

    assert manifest.agent_id == "claims-helper"
    assert manifest.schema_version == 2
    assert manifest.agent_type == "skill-based-agent"
    assert (root / "SPEC.md").is_file()
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
    assert "[otel]" not in (root / ".codex/config.toml").read_text(encoding="utf-8")
    assert 'model = "system.ai.gpt-5-6-sol"' in (root / ".codex/config.toml").read_text()
    assert (root / ".codex/agents/validator.toml").is_file()
    assert (root / ".aops/runtime.json").is_file()
    assert validate_agent(root) == manifest


def test_sensitive_local_runtime_is_denied(tmp_path):
    root = create_agent("hr-agent", tmp_path / "hr-agent")
    spec = root / "SPEC.md"
    spec.write_text(spec.read_text().replace("data_sensitivity: 내부", "data_sensitivity: 개인정보"))
    assert load_manifest(root).agent_type == "skill-based-agent"
    with pytest.raises(ManifestError, match="대외비/개인정보"):
        CodexRuntimeBuilder().prepare(root)
    with pytest.raises(ManifestError, match="대외비/개인정보"):
        build_release(root, "1.0.0", "main.agents")
    assert not (root / ".agents").exists()


def test_enhanced_security_rejects_local_copy(tmp_path):
    root = create_agent("hr-agent", tmp_path / "hr-agent")
    spec = root / "SPEC.md"
    spec.write_text(spec.read_text().replace("security_level: 일반", "security_level: 강화"))
    with pytest.raises(ManifestError, match="강화"):
        CodexRuntimeBuilder().prepare(root)


def test_spec_contains_governance_defaults(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    manifest = load_manifest(root)

    assert manifest.agent_version == "1.0.0"
    assert manifest.build_type == "skill"
    assert manifest.serving_type == "apps"
    assert manifest.service_criticality == "하"
    assert manifest.data_sensitivity == "내부"
    assert manifest.security_level == "일반"
    assert manifest.uc_permissions == ()
    assert manifest.flagship_model is False
    assert manifest.model_service == "system.ai.gpt-5-6-sol"
    assert manifest.origin == "databricks"
    assert manifest.owner == "owner@example.com"
    assert manifest.success_criteria["correctness_min"] == 0.8


def test_latest_spec_fields_and_structured_uc_permissions_round_trip(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    spec = root / "SPEC.md"
    metadata, body = read_markdown_frontmatter(spec, "SPEC.md")
    metadata.update({
        "agent_version": "0.1.0",
        "build_type": "code",
        "serving_type": "SE",
        "origin": "innohub",
        "service_criticality": "중",
        "data_sensitivity": "내부",
        "security_level": "강화",
        "uc_permissions": [{"object": "catalog.schema.table", "privilege": "SELECT"}],
        "flagship_model": True,
        "model_service": "claude-opus-5-5",
    })
    spec.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---" + body)

    manifest = validate_agent(root)
    assert manifest.agent_version == "0.1.0"
    assert manifest.uc_permissions == ({"object": "catalog.schema.table", "privilege": "SELECT"},)
    assert manifest.model_service == "claude-opus-5-5"


@pytest.mark.parametrize("field,replacement", [
    ("data_sensitivity: 내부", "data_sensitivity: 비밀"),
    ("agent_version: \"1.0.0\"", "agent_version: \"v1\""),
    ("  safety_min: 1.0", "  safety_min: 2.0"),
    ("  latency_p90_seconds: 10", "  latency_p90_seconds: -1"),
    ("uc_permissions: []", "uc_permissions:\n  - object: catalog.schema\n    privilege: SELECT"),
    ("flagship_model: false", "flagship_model: yes-but-not-a-boolean"),
    ("owner: owner@example.com", "owner: not-an-email"),
])
def test_spec_rejects_invalid_governance_fields(tmp_path, field, replacement):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    spec = root / "SPEC.md"
    spec.write_text(spec.read_text().replace(field, replacement))
    with pytest.raises(ManifestError, match="SPEC.md"):
        load_manifest(root)


def test_spec_rejects_duplicate_or_legacy_fields_and_wrong_filename(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    spec = root / "SPEC.md"
    content = spec.read_text()
    spec.write_text(content.replace("agent_name: Claims Helper", "agent_name: Claims Helper\nid: old"))
    with pytest.raises(ManifestError, match="unknown"):
        load_manifest(root)
    spec.write_text(content.replace("agent_id: claims-helper", "agent_id: claims-helper\nagent_id: other"))
    with pytest.raises(ManifestError, match="Duplicate YAML field"):
        load_manifest(root)
    spec.write_text(content)
    spec.rename(root / "SPEC.md".lower())
    with pytest.raises(ManifestError, match="exact filename"):
        load_manifest(root)


def test_spec_requires_all_design_sections(tmp_path):
    root = create_agent("claims-helper", tmp_path / "claims-helper")
    spec = root / "SPEC.md"
    spec.write_text(spec.read_text().replace("## 4. 성공 기준과 평가 방법", "## 성공 기준과 평가 방법"))
    with pytest.raises(ManifestError, match="numbered section 4"):
        validate_agent(root)


def test_optional_eval_dataset_is_validated_but_not_packaged(tmp_path):
    from codex_agentops.registry import build_agent_archive
    from codex_agentops.release import write_release_metadata

    root = create_agent("claims-helper", tmp_path / "claims-helper")
    dataset_root = root / "eval"
    dataset_root.mkdir()
    dataset = dataset_root / "eval_dataset.json"
    dataset.write_text('[{"question": "example"}]')
    assert validate_agent(root).agent_id == "claims-helper"
    write_release_metadata(build_release(root, "1.0.0", "main.agentops"))
    assert "eval/eval_dataset.json" not in build_agent_archive(root).files
    dataset.write_text("not json")
    with pytest.raises(ManifestError, match="Invalid eval/eval_dataset.json"):
        validate_agent(root)
    dataset.write_text("[]")
    (dataset_root / "tool.py").write_text("print('unexpected')")
    with pytest.raises(ManifestError, match="eval/"):
        validate_agent(root)


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
