from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote

import yaml

from .manifest import ManifestError, validate_codex_agent_config


NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillRelease:
    alias: str
    uc_name: str
    version: str
    sha256: str
    source: str | None = None

    @property
    def uc_parts(self) -> tuple[str, str, str]:
        parts = self.uc_name.split(".")
        if len(parts) != 3:
            raise ReleaseError(f"UC Skill name must use <catalog>.<schema>.<skill>: {self.uc_name}")
        return parts[0], parts[1], parts[2]


@dataclass(frozen=True)
class SubagentRelease:
    id: str
    instructions: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class AgentRelease:
    root: Path
    agent_id: str
    release_version: str
    model_profile: str
    harness_profile: str
    mcp_tool_profile: str
    skills: tuple[SkillRelease, ...]
    subagents: tuple[SubagentRelease, ...]


@dataclass(frozen=True)
class PublishedSkill:
    alias: str
    uc_name: str
    version: str
    sha256: str


@dataclass(frozen=True)
class AssemblyResult:
    runtime_root: Path
    config_path: Path
    skill_paths: dict[str, Path]


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def load_release(root: str | Path, *, expected_agent_id: str | None = None) -> AgentRelease:
    agent_root = Path(root).expanduser().resolve()
    raw, contract = _read_release_contract(agent_root)

    agent_id = _required_string(raw, "agent_id", contract)
    if not NAME_RE.fullmatch(agent_id):
        raise ReleaseError(f"{contract} agent_id must use lower-case kebab-case.")
    if expected_agent_id and agent_id != expected_agent_id:
        raise ReleaseError(
            f"{contract} agent_id '{agent_id}' does not match agent.yaml '{expected_agent_id}'."
        )
    release_version = _required_string(raw, "release_version", contract)
    if not VERSION_RE.fullmatch(release_version):
        raise ReleaseError("release_version contains unsupported characters.")

    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        raise ReleaseError(f"{contract} profiles must be a YAML object.")
    model_profile = _required_string(profiles, "model", f"{contract} profiles")
    harness_profile = _required_string(profiles, "harness", f"{contract} profiles")
    mcp_tool_profile = _required_string(profiles, "mcp_tools", f"{contract} profiles")

    raw_skills = raw.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ReleaseError(f"{contract} skills must contain at least one UC Skill.")
    skills: list[SkillRelease] = []
    aliases: set[str] = set()
    uc_names: set[str] = set()
    for index, item in enumerate(raw_skills):
        context = f"{contract} skills[{index}]"
        if not isinstance(item, dict):
            raise ReleaseError(f"{context} must be a YAML object.")
        alias = _required_string(item, "alias", context)
        if not NAME_RE.fullmatch(alias):
            raise ReleaseError(f"{context} alias must use lower-case kebab-case.")
        uc_name = _required_string(item, "uc_name", context)
        _validate_uc_name(uc_name, context)
        version = _required_string(item, "version", context)
        if not VERSION_RE.fullmatch(version):
            raise ReleaseError(f"{context} version contains unsupported characters.")
        sha256 = _required_string(item, "sha256", context).lower()
        if not SHA256_RE.fullmatch(sha256):
            raise ReleaseError(f"{context} sha256 must be 64 lower-case hexadecimal characters.")
        source_value = item.get("source")
        source = None if source_value is None else str(source_value).strip()
        if source_value is not None and not source:
            raise ReleaseError(f"{context} source must not be empty.")
        if alias in aliases:
            raise ReleaseError(f"Duplicate Skill alias: {alias}")
        if uc_name in uc_names:
            raise ReleaseError(f"Duplicate UC Skill: {uc_name}")
        aliases.add(alias)
        uc_names.add(uc_name)
        skills.append(SkillRelease(alias, uc_name, version, sha256, source))

    raw_subagents = raw.get("subagents", [])
    if not isinstance(raw_subagents, list):
        raise ReleaseError(f"{contract} subagents must be a list.")
    subagents: list[SubagentRelease] = []
    subagent_ids: set[str] = set()
    for index, item in enumerate(raw_subagents):
        context = f"{contract} subagents[{index}]"
        if not isinstance(item, dict):
            raise ReleaseError(f"{context} must be a YAML object.")
        subagent_id = _required_string(item, "id", context)
        if not NAME_RE.fullmatch(subagent_id):
            raise ReleaseError(f"{context} id must use lower-case kebab-case.")
        instructions = _required_string(item, "instructions", context)
        instruction_path = _safe_relative_path(agent_root, instructions, context)
        if not instruction_path.is_file():
            raise ReleaseError(f"Subagent instructions do not exist: {instruction_path}")
        if instruction_path.suffix == ".toml":
            try:
                validate_codex_agent_config(instruction_path, expected_name=subagent_id)
            except ManifestError as exc:
                raise ReleaseError(str(exc)) from exc
        raw_aliases = item.get("skills", [])
        if not isinstance(raw_aliases, list) or not all(isinstance(value, str) for value in raw_aliases):
            raise ReleaseError(f"{context} skills must be a list of Skill aliases.")
        unknown = sorted(set(raw_aliases) - aliases)
        if unknown:
            raise ReleaseError(f"{context} references unknown Skills: {', '.join(unknown)}")
        if subagent_id in subagent_ids:
            raise ReleaseError(f"Duplicate subagent id: {subagent_id}")
        subagent_ids.add(subagent_id)
        subagents.append(SubagentRelease(subagent_id, instructions, tuple(raw_aliases)))

    return AgentRelease(
        root=agent_root,
        agent_id=agent_id,
        release_version=release_version,
        model_profile=model_profile,
        harness_profile=harness_profile,
        mcp_tool_profile=mcp_tool_profile,
        skills=tuple(skills),
        subagents=tuple(subagents),
    )


def has_release_contract(root: str | Path) -> bool:
    agent_root = Path(root).expanduser().resolve()
    if (agent_root / "release.yaml").is_file():
        return True
    try:
        raw = yaml.safe_load((agent_root / "agent.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return (
        isinstance(raw, dict)
        and raw.get("schema_version") == 2
        and isinstance(raw.get("release"), dict)
    )


def _read_release_contract(agent_root: Path) -> tuple[dict, str]:
    manifest_path = agent_root / "agent.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseError(f"Invalid Agent manifest {manifest_path}: {exc}") from exc
    if isinstance(manifest, dict) and manifest.get("schema_version") == 2:
        agent = manifest.get("agent")
        release = manifest.get("release")
        if not isinstance(agent, dict) or not isinstance(release, dict):
            raise ReleaseError("agent.yaml requires agent and release objects for publishing.")
        normalized = dict(release)
        normalized["schema_version"] = 1
        normalized["agent_id"] = agent.get("id")
        normalized["release_version"] = release.get("version")
        return normalized, "agent.yaml release"

    path = agent_root / "release.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleaseError(f"Missing Agent Release contract in agent.yaml: {manifest_path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseError(f"Invalid Agent Release Manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseError("release.yaml must be a YAML object.")
    if raw.get("schema_version") != 1:
        raise ReleaseError("release.yaml schema_version must be 1.")
    return raw, "release.yaml"


def hash_skill_directory(path: str | Path) -> str:
    root = Path(path).expanduser().resolve()
    if not (root / "SKILL.md").is_file():
        raise ReleaseError(f"Skill source must contain SKILL.md: {root}")
    entries = sorted(root.rglob("*"))
    symbolic_link = next((entry for entry in entries if entry.is_symlink()), None)
    if symbolic_link is not None:
        raise ReleaseError(f"Symbolic links are not allowed in Skill bundles: {symbolic_link}")
    files = [entry for entry in entries if entry.is_file()]
    digest = hashlib.sha256()
    for file in files:
        relative = file.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class UcSkillPublisher:
    def __init__(self, profile: str, *, runner: CommandRunner | None = None) -> None:
        if not profile.strip():
            raise ReleaseError("Databricks profile is required.")
        self.profile = profile
        self.runner = runner or _run_command

    def publish_release(self, root: str | Path) -> tuple[PublishedSkill, ...]:
        release = load_release(root)
        prepared: list[tuple[SkillRelease, Path, str]] = []
        for skill in release.skills:
            if not skill.source:
                raise ReleaseError(f"Skill source is required for publishing: {skill.alias}")
            source = _safe_relative_path(release.root, skill.source, f"Skill {skill.alias}")
            actual = hash_skill_directory(source)
            if actual != skill.sha256:
                raise ReleaseError(
                    f"Skill hash mismatch before publish: {skill.alias}; "
                    f"expected {skill.sha256}, got {actual}"
                )
            prepared.append((skill, source, actual))

        for skill, _, _ in prepared:
            if self._exists(skill.uc_name):
                raise ReleaseError(
                    f"UC Skill already exists and will not be overwritten: {skill.uc_name}. "
                    "Create a new Skill version and Agent release."
                )

        published: list[PublishedSkill] = []
        for skill, source, actual in prepared:
            catalog, schema, leaf = skill.uc_parts
            parent = quote(f"schemas/{catalog}.{schema}", safe="./")
            skill_id = quote(leaf, safe="-_")
            self._checked(
                [
                    "databricks",
                    "api",
                    "post",
                    f"/api/2.1/unity-catalog/skills?parent={parent}&skill_id={skill_id}",
                    "-p",
                    self.profile,
                    "--json",
                    "{}",
                ],
                f"create UC Skill {skill.uc_name}",
            )
            self._checked(
                [
                    "databricks",
                    "fs",
                    "cp",
                    str(source),
                    f"dbfs:/Skills/{catalog}/{schema}/{leaf}/",
                    "--recursive",
                    "--overwrite",
                    "-p",
                    self.profile,
                ],
                f"upload UC Skill bundle {skill.uc_name}",
            )
            self._checked(
                [
                    "databricks",
                    "api",
                    "post",
                    f"/api/2.1/unity-catalog/skills/{quote(skill.uc_name, safe='.')}/finalize",
                    "-p",
                    self.profile,
                ],
                f"finalize UC Skill {skill.uc_name}",
            )
            published.append(PublishedSkill(skill.alias, skill.uc_name, skill.version, actual))
        return tuple(published)

    def _exists(self, full_name: str) -> bool:
        result = self.runner(
            [
                "databricks",
                "api",
                "get",
                f"/api/2.1/unity-catalog/skills/{quote(full_name, safe='.')}",
                "-p",
                self.profile,
            ]
        )
        if result.returncode == 0:
            return True
        message = f"{result.stdout}\n{result.stderr}".upper()
        if any(
            token in message
            for token in (
                "RESOURCE_DOES_NOT_EXIST",
                "NOT_FOUND",
                "NOT FOUND",
                "DOES NOT EXIST",
                "404",
            )
        ):
            return False
        raise ReleaseError(f"Cannot check UC Skill {full_name}: {_command_message(result)}")

    def _checked(self, command: Sequence[str], action: str) -> None:
        result = self.runner(command)
        if result.returncode != 0:
            raise ReleaseError(f"Cannot {action}: {_command_message(result)}")


class AgentReleaseLoader:
    def __init__(self, profile: str, *, runner: CommandRunner | None = None) -> None:
        if not profile.strip():
            raise ReleaseError("Databricks profile is required.")
        self.profile = profile
        self.runner = runner or _run_command

    def sync_skills(self, root: str | Path) -> dict[str, Path]:
        release = load_release(root)
        agent_root = release.root
        target = agent_root / ".agents" / "skills"
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aops-skills-", dir=agent_root.parent) as temporary:
            temporary_root = Path(temporary)
            downloads = temporary_root / "downloads"
            staging = temporary_root / "skills"
            downloads.mkdir()
            staging.mkdir()
            for skill in release.skills:
                bundle = self._download_skill(skill, downloads / skill.alias)
                shutil.copytree(bundle, staging / skill.alias)

            backup = temporary_root / "previous-skills"
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
        return {skill.alias: target / skill.alias for skill in release.skills}

    def assemble(
        self,
        root: str | Path,
        *,
        destination: str | Path | None = None,
        replace: bool = False,
    ) -> AssemblyResult:
        release = load_release(root)
        target = Path(destination or release.root / ".aops" / "runtime").expanduser().resolve()
        if target.exists() and not replace:
            raise ReleaseError(f"Runtime destination already exists: {target}; use --force to replace it.")
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="aops-assemble-", dir=target.parent) as temporary:
            temporary_root = Path(temporary)
            downloads = temporary_root / "downloads"
            staging = temporary_root / "runtime"
            downloads.mkdir()
            staging.mkdir()

            skill_paths: dict[str, Path] = {}
            for skill in release.skills:
                bundle = self._download_skill(skill, downloads / skill.alias)
                runtime_skill = staging / "skills" / skill.alias
                runtime_skill.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(bundle, runtime_skill)
                codex_skill = staging / ".agents" / "skills" / skill.alias
                codex_skill.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(bundle, codex_skill)
                skill_paths[skill.alias] = runtime_skill

            for name in ("AGENTS.md",):
                source = release.root / name
                if source.is_file():
                    shutil.copy2(source, staging / name)
            codex_config = release.root / ".codex" / "config.toml"
            if codex_config.is_file():
                destination_path = staging / ".codex" / "config.toml"
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(codex_config, destination_path)

            subagent_paths: dict[str, str] = {}
            for subagent in release.subagents:
                source = _safe_relative_path(
                    release.root, subagent.instructions, f"Subagent {subagent.id}"
                )
                source_relative = Path(subagent.instructions)
                if source_relative.parts[:2] == (".codex", "agents"):
                    runtime_relative = Path(".codex") / "agents" / f"{subagent.id}.toml"
                else:
                    runtime_relative = Path("subagents") / f"{subagent.id}{source.suffix}"
                destination_path = staging / runtime_relative
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination_path)
                subagent_paths[subagent.id] = runtime_relative.as_posix()

            config = {
                "schema_version": 1,
                "agent_id": release.agent_id,
                "release_version": release.release_version,
                "profiles": {
                    "model": release.model_profile,
                    "harness": release.harness_profile,
                    "mcp_tools": release.mcp_tool_profile,
                },
                "harness": {
                    "agents_md": "AGENTS.md",
                },
                "skills": [
                    {
                        "alias": skill.alias,
                        "uc_name": skill.uc_name,
                        "version": skill.version,
                        "sha256": skill.sha256,
                        "markdown": f"skills/{skill.alias}/SKILL.md",
                    }
                    for skill in release.skills
                ],
                "subagents": [
                    {
                        "id": subagent.id,
                        "instructions": subagent_paths[subagent.id],
                        "skills": list(subagent.skills),
                    }
                    for subagent in release.subagents
                ],
            }
            config_path = staging / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )

            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)

        return AssemblyResult(
            runtime_root=target,
            config_path=target / "config.yaml",
            skill_paths={alias: target / "skills" / alias for alias in skill_paths},
        )

    def _download_skill(self, skill: SkillRelease, destination: Path) -> Path:
        catalog, schema, leaf = skill.uc_parts
        result = self.runner(
            [
                "databricks",
                "fs",
                "cp",
                f"dbfs:/Skills/{catalog}/{schema}/{leaf}/",
                str(destination),
                "--recursive",
                "-p",
                self.profile,
            ]
        )
        if result.returncode != 0:
            raise ReleaseError(
                f"Cannot download UC Skill {skill.uc_name}: {_command_message(result)}"
            )
        bundle = _find_downloaded_skill(destination)
        actual = hash_skill_directory(bundle)
        if actual != skill.sha256:
            raise ReleaseError(
                f"Downloaded UC Skill hash mismatch: {skill.uc_name}; "
                f"expected {skill.sha256}, got {actual}"
            )
        return bundle


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def _command_message(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _required_string(values: dict, key: str, context: str) -> str:
    value = str(values.get(key, "")).strip()
    if not value:
        raise ReleaseError(f"{context} requires '{key}'.")
    return value


def _validate_uc_name(value: str, context: str) -> None:
    parts = value.split(".")
    if len(parts) != 3 or any(not part or "/" in part or any(char.isspace() for char in part) for part in parts):
        raise ReleaseError(f"{context} uc_name must use <catalog>.<schema>.<skill>.")


def _safe_relative_path(root: Path, value: str, context: str) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ReleaseError(f"{context} path must be relative to the Agent root: {value}")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseError(f"{context} path escapes the Agent root: {value}") from exc
    return resolved


def _find_downloaded_skill(download_root: Path) -> Path:
    direct = download_root / "SKILL.md"
    if direct.is_file():
        return download_root
    matches = sorted(download_root.rglob("SKILL.md")) if download_root.exists() else []
    if len(matches) != 1:
        raise ReleaseError(
            f"Downloaded Skill must contain exactly one SKILL.md, found {len(matches)}: {download_root}"
        )
    return matches[0].parent
