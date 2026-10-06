from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from codex_agentops.release import (
    AgentReleaseLoader,
    ReleaseError,
    UcSkillPublisher,
    hash_skill_directory,
    load_release,
)


SAMPLE = Path(__file__).parents[1] / "samples" / "release-agent"


def _result(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_sample_release_pins_two_uc_skills_and_subagent():
    release = load_release(SAMPLE, expected_agent_id="release-sample-agent")

    assert release.release_version == "2.0.0"
    assert release.harness_profile == "omnigent-v1"
    assert [skill.alias for skill in release.skills] == [
        "release-summary",
        "release-validation",
    ]
    assert release.subagents[0].skills == ("release-validation",)
    for skill in release.skills:
        source = SAMPLE / str(skill.source)
        assert hash_skill_directory(source) == skill.sha256


def test_publish_is_create_only_and_uses_explicit_profile():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        if command[1:3] == ["api", "get"]:
            return _result(command, 1, stderr="RESOURCE_DOES_NOT_EXIST")
        return _result(command)

    published = UcSkillPublisher("test-profile", runner=runner).publish_release(SAMPLE)

    assert len(published) == 2
    assert sum(command[1:3] == ["api", "get"] for command in commands) == 2
    assert sum(command[1:3] == ["api", "post"] for command in commands) == 4
    assert sum(command[1:3] == ["fs", "cp"] for command in commands) == 2
    assert all("test-profile" in command for command in commands)
    assert not any("update" in " ".join(command) for command in commands)


def test_publish_rejects_existing_skill_before_writing():
    commands: list[list[str]] = []

    def runner(command):
        command = list(command)
        commands.append(command)
        return _result(command, 0, stdout="{}")

    with pytest.raises(ReleaseError, match="will not be overwritten"):
        UcSkillPublisher("test-profile", runner=runner).publish_release(SAMPLE)

    assert len(commands) == 1
    assert commands[0][1:3] == ["api", "get"]


def test_assemble_downloads_hash_verifies_and_generates_runtime(tmp_path):
    source_by_leaf = {
        "release-summary-v2-0-0": SAMPLE / ".agents/skills/release-summary",
        "release-validation-v2-0-0": SAMPLE / ".agents/skills/release-validation",
    }

    def runner(command):
        command = list(command)
        remote = command[3]
        destination = Path(command[4])
        leaf = remote.rstrip("/").split("/")[-1]
        shutil.copytree(source_by_leaf[leaf], destination)
        return _result(command)

    target = tmp_path / "runtime"
    result = AgentReleaseLoader("test-profile", runner=runner).assemble(
        SAMPLE, destination=target
    )

    assert result.runtime_root == target
    assert (target / "AGENTS.md").is_file()
    assert (target / "skills/release-summary/SKILL.md").is_file()
    assert (target / ".agents/skills/release-validation/SKILL.md").is_file()
    assert (target / ".codex/config.toml").is_file()
    assert (target / ".codex/agents/validator.toml").is_file()
    config = yaml.safe_load((target / "config.yaml").read_text(encoding="utf-8"))
    assert config["release_version"] == "2.0.0"
    assert config["profiles"] == {
        "model": "standard-v1",
        "harness": "omnigent-v1",
        "mcp_tools": "readonly-v1",
    }
    assert config["skills"][0]["markdown"] == "skills/release-summary/SKILL.md"
    assert config["subagents"][0]["skills"] == ["release-validation"]
    assert config["subagents"][0]["instructions"] == ".codex/agents/validator.toml"


def test_assemble_does_not_publish_runtime_on_hash_mismatch(tmp_path):
    def runner(command):
        command = list(command)
        destination = Path(command[4])
        shutil.copytree(SAMPLE / ".agents/skills/release-summary", destination)
        (destination / "SKILL.md").write_text("tampered", encoding="utf-8")
        return _result(command)

    target = tmp_path / "runtime"
    with pytest.raises(ReleaseError, match="hash mismatch"):
        AgentReleaseLoader("test-profile", runner=runner).assemble(SAMPLE, destination=target)

    assert not target.exists()
