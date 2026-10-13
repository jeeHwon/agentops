from __future__ import annotations

import subprocess
from pathlib import Path

from codex_agentops.release import (
    CodexRuntimeBuilder,
    UcSkillPublisher,
    build_release,
    hash_skill_directory,
    load_release,
    write_release_metadata,
)


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def _result(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_release_pins_two_skill_snapshots_and_subagent(tmp_path):
    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    assert release.release_version == "1.0.0"
    assert release.harness_profile == "codex"
    assert [skill.alias for skill in release.skills] == [
        "release-summary",
        "release-validation",
    ]
    assert release.subagents[0].skills == ("release-validation",)
    for skill in release.skills:
        assert skill.uc_name == (
            f"poc_catalog.agentops_test.release-sample-agent-{skill.alias}"
        )
        assert skill.publication_mode == "agent"
        assert hash_skill_directory(SAMPLE / skill.source) == skill.sha256

    root = tmp_path / "copy"
    root.mkdir()
    for name in ("AGENTS.md", "README.md", "spec.md"):
        (root / name).write_bytes((SAMPLE / name).read_bytes())
    import shutil

    shutil.copytree(SAMPLE / "skills", root / "skills")
    shutil.copytree(SAMPLE / "agents", root / "agents")
    rebuilt = build_release(root, "1.0.0", "poc_catalog.agentops_test")
    write_release_metadata(rebuilt)
    loaded = load_release(root, expected_agent_id="release-sample-agent")
    assert loaded.release_version == "1.0.0"
    assert loaded.skills == rebuilt.skills
    assert loaded.spec["source_system"] == "local"


def test_publish_creates_missing_uc_skills_with_explicit_profile():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        if command[1:3] == ["api", "get"]:
            return _result(command, 1, stderr="RESOURCE_DOES_NOT_EXIST")
        return _result(command)

    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    published = UcSkillPublisher("test-profile", runner=runner).publish(release)

    assert [item.action for item in published] == ["created", "created"]
    assert sum(command[1:3] == ["api", "get"] for command in commands) == 2
    assert sum("/finalize" in " ".join(command) for command in commands) == 2
    assert sum(command[1:3] == ["fs", "cp"] for command in commands) == 2
    assert all("test-profile" in command for command in commands)


def test_publish_updates_existing_uc_skill_but_release_keeps_snapshot_hash():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        return _result(command, 0, stdout="{}")

    release = build_release(SAMPLE, "1.0.1", "poc_catalog.agentops_test")
    published = UcSkillPublisher("test-profile", runner=runner).publish(release)

    assert [item.action for item in published] == ["updated", "updated"]
    assert not any("skills?parent=" in " ".join(command) for command in commands)
    assert all(item.sha256 in {skill.sha256 for skill in release.skills} for item in published)


def test_shared_reference_never_overwrites_existing_uc_skill(tmp_path):
    import shutil

    root = tmp_path / "agent"
    shutil.copytree(SAMPLE, root)
    skill_path = root / "skills/release-summary/SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "description: 문서의 핵심 내용, 결정 사항과 후속 행동을 원문 근거에 맞게 요약할 때 사용합니다.",
            "description: 문서의 핵심 내용, 결정 사항과 후속 행동을 원문 근거에 맞게 요약할 때 사용합니다.\n"
            "uc_mode: shared-reference\n"
            "uc_name: main.shared.release-summary",
            1,
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def runner(command):
        commands.append(list(command))
        return _result(command, 0, stdout="{}")

    release = build_release(root, "1.0.0", "main.agentops")
    published = UcSkillPublisher("test-profile", runner=runner).publish(release)

    shared = next(item for item in published if item.alias == "release-summary")
    assert shared.action == "referenced"
    assert shared.uc_name == "main.shared.release-summary"
    assert not any(
        command[1:3] == ["fs", "cp"] and "main/shared/release-summary" in " ".join(command)
        for command in commands
    )


def test_codex_runtime_is_generated_from_source_without_mutating_source(tmp_path):
    import shutil

    root = tmp_path / "agent"
    shutil.copytree(SAMPLE, root)
    before = hash_skill_directory(root / "skills/release-summary")
    result = CodexRuntimeBuilder().prepare(root)

    assert result.runtime_root == root
    assert (root / ".agents/skills/release-summary/SKILL.md").is_file()
    assert (root / ".agents/skills/release-validation/SKILL.md").is_file()
    assert (root / ".codex/agents/validator.toml").is_file()
    assert (root / ".aops/runtime.json").is_file()
    assert hash_skill_directory(root / "skills/release-summary") == before
