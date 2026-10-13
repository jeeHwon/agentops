from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote

from .manifest import (
    VERSION_RE,
    ManifestError,
    calculate_definition_checksums,
    load_manifest,
    load_skills,
    load_subagents,
    read_release_metadata,
    validate_agent,
)


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillRelease:
    alias: str
    uc_name: str
    sha256: str
    source: str
    publication_mode: str = "agent"

    @property
    def version(self) -> str:
        """Compatibility alias: the immutable version is the content hash."""
        return self.sha256[:12]

    @property
    def uc_parts(self) -> tuple[str, str, str]:
        parts = self.uc_name.split(".")
        if len(parts) != 3:
            raise ReleaseError(f"UC Skill name must use <catalog>.<schema>.<skill>: {self.uc_name}")
        return parts[0], parts[1], parts[2]


@dataclass(frozen=True)
class SubagentRelease:
    id: str
    source: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class AgentRelease:
    root: Path
    agent_id: str
    release_version: str
    model: str
    harness_profile: str
    skills: tuple[SkillRelease, ...]
    subagents: tuple[SubagentRelease, ...]
    harness_sha256: str
    skills_sha256: str
    spec: dict[str, Any]


@dataclass(frozen=True)
class PublishedSkill:
    alias: str
    uc_name: str
    sha256: str
    action: str

    @property
    def version(self) -> str:
        return self.sha256[:12]


@dataclass(frozen=True)
class AssemblyResult:
    runtime_root: Path
    config_path: Path
    skill_paths: dict[str, Path]


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def build_release(root: str | Path, version: str, skill_schema: str) -> AgentRelease:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("Version must contain only letters, numbers, dot, underscore, or hyphen.")
    manifest = validate_agent(root)
    schema_parts = skill_schema.split(".")
    if len(schema_parts) != 2 or not all(schema_parts):
        raise ReleaseError("Skill schema must use <catalog>.<schema> format.")
    skills = load_skills(manifest.root)
    checksums = calculate_definition_checksums(manifest.root)
    skill_releases = tuple(
        SkillRelease(
            alias=name,
            uc_name=(
                definition.uc_name
                if definition.uc_mode == "shared-reference"
                else f"{skill_schema}.{manifest.agent_id}-{name}"
            ),
            sha256=hash_skill_directory(definition.root),
            source=f"skills/{name}",
            publication_mode=definition.uc_mode,
        )
        for name, definition in sorted(skills.items())
    )
    subagents = load_subagents(manifest.root, set(skills))
    return AgentRelease(
        root=manifest.root,
        agent_id=manifest.agent_id,
        release_version=version,
        model=manifest.model,
        harness_profile="codex",
        skills=skill_releases,
        subagents=tuple(
            SubagentRelease(
                id=name,
                source=f"agents/{name}/AGENTS.md",
                skills=definition.skills,
            )
            for name, definition in sorted(subagents.items())
        ),
        harness_sha256=checksums.harness,
        skills_sha256=checksums.combined_skills,
        spec={
            "name": manifest.name,
            "description": manifest.description,
            "agent_type": manifest.agent_type,
            "owner": manifest.owner,
            "owner_group": manifest.owner_group,
            "source_system": manifest.source_system,
            "target_users": list(manifest.target_users),
            "cost_center": manifest.cost_center,
            "service_criticality": manifest.service_criticality,
            "data_sensitivity": manifest.data_sensitivity,
            "security_access_level": manifest.security_access_level,
            "risk_level": manifest.risk_level,
            "hitl_required": manifest.hitl_required,
            "required_uc_permissions": list(manifest.required_uc_permissions),
            "use_flagship_model": manifest.use_flagship_model,
            "success_criteria": manifest.success_criteria,
        },
    )


def write_release_metadata(release: AgentRelease) -> Path:
    target = release.root / ".aops" / "release.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = release_to_dict(release)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def release_to_dict(release: AgentRelease) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "agent_id": release.agent_id,
        "version": release.release_version,
        "agent_type": "harness",
        "model": release.model,
        "harness": release.harness_profile,
        "harness_sha256": release.harness_sha256,
        "skills_sha256": release.skills_sha256,
        "spec": release.spec,
        "skills": [
            {
                "alias": skill.alias,
                "uc_name": skill.uc_name,
                "sha256": skill.sha256,
                "source": skill.source,
                "publication_mode": skill.publication_mode,
            }
            for skill in release.skills
        ],
        "subagents": [
            {"id": item.id, "source": item.source, "skills": list(item.skills)}
            for item in release.subagents
        ],
    }


def load_release(root: str | Path, *, expected_agent_id: str | None = None) -> AgentRelease:
    agent_root = Path(root).expanduser().resolve()
    raw = read_release_metadata(agent_root)
    if raw is None:
        raise ReleaseError(f"Generated Agent Release metadata is missing: {agent_root / '.aops/release.json'}")
    return release_from_dict(raw, root=agent_root, expected_agent_id=expected_agent_id)


def release_from_dict(
    raw: dict[str, Any],
    *,
    root: str | Path = ".",
    expected_agent_id: str | None = None,
) -> AgentRelease:
    agent_root = Path(root).expanduser().resolve()
    if raw.get("schema_version") != 1:
        raise ReleaseError("Generated Agent Release schema_version must be 1.")
    agent_id = _required_string(raw, "agent_id", "release.json")
    if expected_agent_id and agent_id != expected_agent_id:
        raise ReleaseError(
            f"Release agent_id '{agent_id}' does not match expected '{expected_agent_id}'."
        )
    version = _required_string(raw, "version", "release.json")
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("Release version contains unsupported characters.")
    raw_skills = raw.get("skills")
    if not isinstance(raw_skills, list) or not raw_skills:
        raise ReleaseError("Release must contain at least one Skill snapshot.")
    skills: list[SkillRelease] = []
    for item in raw_skills:
        if not isinstance(item, dict):
            raise ReleaseError("Release Skill metadata must be an object.")
        skill = SkillRelease(
            alias=_required_string(item, "alias", "release skill"),
            uc_name=_required_string(item, "uc_name", "release skill"),
            sha256=_required_string(item, "sha256", "release skill"),
            source=_required_string(item, "source", "release skill"),
            publication_mode=str(item.get("publication_mode") or "legacy"),
        )
        if len(skill.sha256) != 64:
            raise ReleaseError(f"Invalid Skill checksum: {skill.alias}")
        skill.uc_parts
        skills.append(skill)
    raw_subagents = raw.get("subagents", [])
    if not isinstance(raw_subagents, list):
        raise ReleaseError("Release subagents must be a list.")
    subagents = tuple(
        SubagentRelease(
            id=_required_string(item, "id", "release subagent"),
            source=_required_string(item, "source", "release subagent"),
            skills=tuple(item.get("skills", [])),
        )
        for item in raw_subagents
        if isinstance(item, dict)
    )
    return AgentRelease(
        root=agent_root,
        agent_id=agent_id,
        release_version=version,
        model=_required_string(raw, "model", "release.json"),
        harness_profile=str(raw.get("harness") or "codex"),
        skills=tuple(skills),
        subagents=subagents,
        harness_sha256=_required_string(raw, "harness_sha256", "release.json"),
        skills_sha256=_required_string(raw, "skills_sha256", "release.json"),
        spec=dict(raw.get("spec") or {}),
    )


def has_release_contract(root: str | Path) -> bool:
    return (Path(root).expanduser().resolve() / ".aops" / "release.json").is_file()


def hash_skill_directory(path: str | Path) -> str:
    root = Path(path).expanduser().resolve()
    if not (root / "SKILL.md").is_file():
        raise ReleaseError(f"Skill source must contain SKILL.md: {root}")
    entries = sorted(root.rglob("*"))
    symbolic_link = next((entry for entry in entries if entry.is_symlink()), None)
    if symbolic_link is not None:
        raise ReleaseError(f"Symbolic links are not allowed in Skill bundles: {symbolic_link}")
    digest = hashlib.sha256()
    for file in (entry for entry in entries if entry.is_file()):
        digest.update(file.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class UcSkillPublisher:
    """Publish local Agent Skills as governed UC Skills using the public UC Skill API."""

    def __init__(self, profile: str, *, runner: CommandRunner | None = None) -> None:
        if not profile.strip():
            raise ReleaseError("Databricks profile is required.")
        self.profile = profile
        self.runner = runner or _run_command

    def publish(self, release: AgentRelease) -> tuple[PublishedSkill, ...]:
        prepared: list[tuple[SkillRelease, Path, bool]] = []
        for skill in release.skills:
            source = _safe_relative_path(release.root, skill.source, f"Skill {skill.alias}")
            actual = hash_skill_directory(source)
            if actual != skill.sha256:
                raise ReleaseError(
                    f"Skill hash mismatch before publish: {skill.alias}; "
                    f"expected {skill.sha256}, got {actual}"
                )
            prepared.append((skill, source, self._exists(skill.uc_name)))

        published: list[PublishedSkill] = []
        for skill, source, existed in prepared:
            if skill.publication_mode == "shared-reference":
                if not existed:
                    raise ReleaseError(f"Referenced UC Skill does not exist: {skill.uc_name}")
                published.append(
                    PublishedSkill(
                        alias=skill.alias,
                        uc_name=skill.uc_name,
                        sha256=skill.sha256,
                        action="referenced",
                    )
                )
                continue
            catalog, schema, leaf = skill.uc_parts
            if not existed:
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
            published.append(
                PublishedSkill(
                    alias=skill.alias,
                    uc_name=skill.uc_name,
                    sha256=skill.sha256,
                    action="updated" if existed else "created",
                )
            )
        return tuple(published)

    def publish_release(
        self, root: str | Path, *, version: str = "working", skill_schema: str
    ) -> tuple[PublishedSkill, ...]:
        return self.publish(build_release(root, version, skill_schema))

    def verify_access(self, release: AgentRelease) -> None:
        for skill in release.skills:
            result = self.runner(
                [
                    "databricks",
                    "api",
                    "get",
                    f"/api/2.1/unity-catalog/skills/{quote(skill.uc_name, safe='.')}",
                    "-p",
                    self.profile,
                ]
            )
            if result.returncode != 0:
                raise ReleaseError(
                    f"UC Skill access denied or missing: {skill.uc_name}: {_command_message(result)}"
                )

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
            for token in ("RESOURCE_DOES_NOT_EXIST", "NOT_FOUND", "NOT FOUND", "DOES NOT EXIST", "404")
        ):
            return False
        raise ReleaseError(f"Cannot check UC Skill {full_name}: {_command_message(result)}")

    def _checked(self, command: Sequence[str], action: str) -> None:
        result = self.runner(command)
        if result.returncode != 0:
            raise ReleaseError(f"Cannot {action}: {_command_message(result)}")


class CodexRuntimeBuilder:
    def __init__(self, *, otel_port: int = 4319) -> None:
        self.otel_port = otel_port

    def prepare(self, root: str | Path) -> AssemblyResult:
        manifest = validate_agent(root)
        skills = load_skills(manifest.root)
        subagents = load_subagents(manifest.root, set(skills))
        generated_agents = manifest.root / ".agents"
        generated_codex = manifest.root / ".codex"
        with tempfile.TemporaryDirectory(prefix="aops-runtime-", dir=manifest.root.parent) as temporary:
            staging = Path(temporary)
            agents_staging = staging / ".agents"
            codex_staging = staging / ".codex"
            for name, skill in skills.items():
                destination = agents_staging / "skills" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(skill.root, destination)
            (codex_staging / "agents").mkdir(parents=True, exist_ok=True)
            (codex_staging / "config.toml").write_text(
                _codex_project_config(self.otel_port),
                encoding="utf-8",
            )
            for name, subagent in subagents.items():
                (codex_staging / "agents" / f"{name}.toml").write_text(
                    _codex_subagent_toml(subagent), encoding="utf-8"
                )
            _replace_directory(agents_staging, generated_agents)
            _replace_directory(codex_staging, generated_codex)

        runtime_meta = manifest.root / ".aops" / "runtime.json"
        runtime_meta.parent.mkdir(parents=True, exist_ok=True)
        release = read_release_metadata(manifest.root)
        runtime_meta.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "runtime": "codex",
                    "agent_id": manifest.agent_id,
                    "release_version": (release or {}).get("version", "working"),
                    "model": manifest.model,
                    "skills": sorted(skills),
                    "subagents": sorted(subagents),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return AssemblyResult(
            runtime_root=manifest.root,
            config_path=generated_codex / "config.toml",
            skill_paths={name: generated_agents / "skills" / name for name in skills},
        )


class AgentReleaseLoader:
    """Compatibility facade used by the CLI and platform adapters."""

    def __init__(self, profile: str | None = None, *, runner: CommandRunner | None = None) -> None:
        self.profile = profile or ""
        self.runner = runner or _run_command

    def assemble(
        self,
        root: str | Path,
        *,
        destination: str | Path | None = None,
        replace: bool = False,
    ) -> AssemblyResult:
        if destination is not None and Path(destination).resolve() != Path(root).resolve():
            raise ReleaseError("Codex runtime is generated inside the Agent source directory.")
        _ = replace
        return CodexRuntimeBuilder().prepare(root)

    def sync_skills(self, root: str | Path) -> dict[str, Path]:
        return CodexRuntimeBuilder().prepare(root).skill_paths


def _codex_subagent_toml(subagent: Any) -> str:
    instructions = subagent.instructions
    if subagent.skills:
        instructions += "\n\n사용할 Skill: " + ", ".join(f"${name}" for name in subagent.skills)
    return (
        f"name = {json.dumps(subagent.name, ensure_ascii=False)}\n"
        f"description = {json.dumps(subagent.description, ensure_ascii=False)}\n"
        f"sandbox_mode = {json.dumps(subagent.sandbox_mode, ensure_ascii=False)}\n"
        f"developer_instructions = {json.dumps(instructions, ensure_ascii=False)}\n"
    )


def _codex_project_config(otel_port: int) -> str:
    base = f"http://127.0.0.1:{otel_port}"
    return (
        "[agents]\n"
        "enabled = true\n"
        "max_concurrent_threads_per_session = 4\n\n"
        "[otel]\n"
        'environment = "agentops-harness"\n'
        "log_user_prompt = false\n"
        "log_agent_responses = false\n"
        f'exporter = {{ otlp-http = {{ endpoint = "{base}/v1/logs", protocol = "json" }} }}\n'
        f'metrics_exporter = {{ otlp-http = {{ endpoint = "{base}/v1/metrics", protocol = "json" }} }}\n'
    )


def _replace_directory(source: Path, target: Path) -> None:
    backup = target.with_name(f"{target.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(source, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def _command_message(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()


def _safe_relative_path(root: Path, value: str, context: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseError(f"{context} path must stay inside the Agent root: {value}")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseError(f"{context} path escapes the Agent root: {value}") from exc
    return resolved


def _required_string(values: dict[str, Any], key: str, context: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseError(f"{context} requires non-empty {key}.")
    return value.strip()
