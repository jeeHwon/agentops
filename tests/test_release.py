from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import load_manifest
from codex_agentops.release import (
    CodexRuntimeBuilder,
    ReleaseError,
    UcSkillPublisher,
    build_release,
    hash_skill_directory,
    load_release,
    release_from_dict,
    release_to_dict,
    write_release_metadata,
)


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def _result(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_release_pins_two_skill_snapshots_and_subagent(tmp_path):
    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    assert release.release_version == "1.0.0"
    assert release.spec["build_type"] == "skill"
    assert release_to_dict(release)["agent_type"] == "skill-based-agent"
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
    for name in ("AGENTS.md", "README.md", "SPEC.md"):
        (root / name).write_bytes((SAMPLE / name).read_bytes())
    import shutil

    shutil.copytree(SAMPLE / "skills", root / "skills")
    shutil.copytree(SAMPLE / "agents", root / "agents")
    rebuilt = build_release(root, "1.0.0", "poc_catalog.agentops_test")
    write_release_metadata(rebuilt)
    loaded = load_release(root, expected_agent_id="release-sample-agent")
    assert loaded.release_version == "1.0.0"
    assert loaded.skills == rebuilt.skills
    assert loaded.spec["origin"] == "databricks"
    assert loaded.spec["agent_version"] == "1.0.0"
    assert loaded.spec["uc_permissions"] == []

    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nWork in progress.\n")
    event = from_hook_payload(
        {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t"},
        load_manifest(root), Settings("test", "https://example.com"),
    )
    assert event.payload["release_version"] == "working"


def test_release_rejects_version_that_differs_from_spec():
    with pytest.raises(ReleaseError, match="SPEC.md agent_version"):
        build_release(SAMPLE, "1.0.1", "main.agentops")


def test_release_rejects_unsupported_schema_type_and_version():
    release = release_to_dict(build_release(SAMPLE, "1.0.0", "main.agentops"))
    for field, value in (("schema_version", 1), ("agent_type", "harness"), ("version", "1.0")):
        unsupported = {**release, field: value}
        with pytest.raises(ReleaseError):
            release_from_dict(unsupported)


def test_codex_runtime_rejects_unsupported_release_before_generating_files(tmp_path):
    import json
    import shutil

    root = tmp_path / "source"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
    release = build_release(root, "1.0.0", "main.agentops")
    metadata = write_release_metadata(release)
    data = json.loads(metadata.read_text())
    data["schema_version"] = 1
    metadata.write_text(json.dumps(data))

    with pytest.raises(ReleaseError, match="schema_version"):
        CodexRuntimeBuilder().prepare(root)
    assert not (root / ".agents").exists()


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
    uploads = [command for command in commands if command[1:3] == ["fs", "cp"]]
    assert len(uploads) == 4
    assert all("--recursive" not in command for command in uploads)
    assert {Path(command[3]).name for command in uploads} == {
        "SKILL.md", "guidelines.md", "checklist.md",
    }
    assert sum(command[1:3] == ["fs", "mkdir"] for command in commands) == 4
    assert all("test-profile" in command for command in commands)


def test_publish_cleans_new_uc_skills_when_storage_does_not_support_files():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        if command[1:3] == ["api", "get"]:
            return _result(command, 1, stderr="RESOURCE_DOES_NOT_EXIST")
        if command[1:3] == ["fs", "mkdir"]:
            return _result(
                command, 1,
                stderr="The requested operation is not supported for this skill's storage location.",
            )
        return _result(command)

    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    with pytest.raises(ReleaseError, match="does not support /Skills file operations"):
        UcSkillPublisher("test-profile", runner=runner).publish(release)

    assert sum(command[1:3] == ["api", "post"] for command in commands) == 1
    assert sum(command[1:3] == ["api", "delete"] for command in commands) == 1
    assert not any("/finalize" in " ".join(command) for command in commands)


def test_publish_rolls_back_all_new_skills_on_later_upload_failure():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        if command[1:3] == ["api", "get"]:
            return _result(command, 1, stderr="RESOURCE_DOES_NOT_EXIST")
        if command[1:3] == ["fs", "cp"] and "release-validation" in command[4]:
            return _result(command, 1, stderr="upload failed")
        return _result(command)

    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    with pytest.raises(ReleaseError, match="upload failed"):
        UcSkillPublisher("test-profile", runner=runner).publish(release)

    deleted = [command[3] for command in commands if command[1:3] == ["api", "delete"]]
    assert deleted == [
        "/api/2.1/unity-catalog/skills/poc_catalog.agentops_test.release-sample-agent-release-validation",
        "/api/2.1/unity-catalog/skills/poc_catalog.agentops_test.release-sample-agent-release-summary",
    ]


def test_publish_never_deletes_preexisting_skill_on_failure():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        if command[1:3] == ["fs", "mkdir"]:
            return _result(command, 1, stderr="upload failed")
        return _result(command, 0, stdout="{}")

    release = build_release(SAMPLE, "1.0.0", "poc_catalog.agentops_test")
    with pytest.raises(ReleaseError, match="upload failed"):
        UcSkillPublisher("test-profile", runner=runner).publish(release)

    assert not any(command[1:3] == ["api", "delete"] for command in commands)


def test_publish_updates_existing_uc_skill_but_release_keeps_snapshot_hash(tmp_path):
    import shutil

    root = tmp_path / "source"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
    spec = root / "SPEC.md"
    spec.write_text(spec.read_text().replace('agent_version: "1.0.0"', 'agent_version: "1.0.1"'))
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        return _result(command, 0, stdout="{}")

    release = build_release(root, "1.0.1", "poc_catalog.agentops_test")
    published = UcSkillPublisher("test-profile", runner=runner).publish(release)

    assert [item.action for item in published] == ["updated", "updated"]
    assert not any("skills?parent=" in " ".join(command) for command in commands)
    assert all(item.sha256 in {skill.sha256 for skill in release.skills} for item in published)


def test_shared_reference_never_overwrites_existing_uc_skill(tmp_path):
    import shutil

    root = tmp_path / "agent"
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
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
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns(".aops", ".agents", ".codex"))
    before = hash_skill_directory(root / "skills/release-summary")
    result = CodexRuntimeBuilder().prepare(root)

    assert result.runtime_root == root
    assert (root / ".agents/skills/release-summary/SKILL.md").is_file()
    assert (root / ".agents/skills/release-validation/SKILL.md").is_file()
    assert (root / ".codex/agents/validator.toml").is_file()
    assert "[otel]" not in (root / ".codex/config.toml").read_text(encoding="utf-8")
    assert (root / ".aops/runtime.json").is_file()
    assert hash_skill_directory(root / "skills/release-summary") == before
