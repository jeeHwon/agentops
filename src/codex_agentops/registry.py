from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import ResourceAlreadyExists
from databricks.sdk.service.catalog import VolumeType
from databricks.sdk.service.sql import StatementParameterListItem

from .manifest import AGENT_ID_RE, VERSION_RE, calculate_definition_checksums, validate_agent
from .release import (
    CodexRuntimeBuilder,
    UcSkillPublisher,
    build_release,
    load_release,
    release_to_dict,
    write_release_metadata,
)


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_VOLUME = "agent_artifacts"
DEFAULT_TABLE = "agent_releases"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistryLocation:
    catalog: str
    schema: str
    volume: str = DEFAULT_VOLUME
    table: str = DEFAULT_TABLE

    @property
    def schema_name(self) -> str:
        return f"{self.catalog}.{self.schema}"

    @property
    def table_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.table}"

    @property
    def volume_root(self) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"


@dataclass(frozen=True)
class AgentVersion:
    agent_id: str
    version: str
    name: str
    description: str
    agent_type: str
    model: str
    artifact_path: str
    artifact_sha256: str
    artifact_size: int
    harness_sha256: str
    skills_sha256: str
    skill_names: tuple[str, ...]
    registered_by: str
    registered_at: str
    release_manifest: dict[str, Any]


@dataclass(frozen=True)
class AgentArchive:
    content: bytes
    sha256: str
    files: dict[str, str]


def parse_registry(
    value: str, *, volume: str = DEFAULT_VOLUME, table: str = DEFAULT_TABLE
) -> RegistryLocation:
    parts = value.split(".")
    if len(parts) != 2:
        raise RegistryError("Registry must use <catalog>.<schema> format.")
    for label, identifier in (
        ("catalog", parts[0]),
        ("schema", parts[1]),
        ("volume", volume),
        ("table", table),
    ):
        if not IDENTIFIER_RE.fullmatch(identifier):
            raise RegistryError(f"Invalid Registry {label}: {identifier}")
    return RegistryLocation(parts[0], parts[1], volume, table)


class RegistryClient:
    def __init__(
        self,
        *,
        profile: str,
        warehouse_id: str,
        location: RegistryLocation,
        workspace: WorkspaceClient | None = None,
        skill_publisher: UcSkillPublisher | None = None,
        codex_otel_port: int = 4319,
    ) -> None:
        if not profile.strip():
            raise RegistryError("Databricks profile is required.")
        if not warehouse_id.strip():
            raise RegistryError("SQL warehouse ID is required.")
        self.profile = profile
        self.warehouse_id = warehouse_id
        self.location = location
        self.workspace = workspace or WorkspaceClient(profile=profile)
        self.skill_publisher = skill_publisher or UcSkillPublisher(profile)
        self.codex_otel_port = codex_otel_port

    def initialize(self) -> None:
        try:
            self.workspace.schemas.create(
                name=self.location.schema,
                catalog_name=self.location.catalog,
                comment="Harness Agent Registry metadata and immutable artifacts",
            )
        except ResourceAlreadyExists:
            pass
        try:
            self.workspace.volumes.create(
                catalog_name=self.location.catalog,
                schema_name=self.location.schema,
                name=self.location.volume,
                volume_type=VolumeType.MANAGED,
                comment="Immutable Harness Agent release artifacts",
            )
        except ResourceAlreadyExists:
            pass
        self._execute_sql(
            f"""
            CREATE TABLE IF NOT EXISTS {_quoted_table(self.location)} (
              agent_id STRING NOT NULL,
              version STRING NOT NULL,
              name STRING NOT NULL,
              description STRING NOT NULL,
              agent_type STRING NOT NULL,
              model STRING NOT NULL,
              artifact_path STRING NOT NULL,
              artifact_sha256 STRING NOT NULL,
              artifact_size BIGINT NOT NULL,
              harness_sha256 STRING NOT NULL,
              skills_sha256 STRING NOT NULL,
              skill_names ARRAY<STRING> NOT NULL,
              registered_by STRING NOT NULL,
              registered_at TIMESTAMP NOT NULL,
              manifest_json STRING NOT NULL
            )
            USING DELTA
            COMMENT 'Immutable Markdown-first Harness Agent releases'
            """
        )

    def register(self, root: str | Path, version: str) -> AgentVersion:
        if not VERSION_RE.fullmatch(version):
            raise RegistryError("Version must contain only letters, numbers, dot, underscore, or hyphen.")
        manifest = validate_agent(root)
        if self.get(manifest.agent_id, version, allow_missing=True) is not None:
            raise RegistryError(f"Agent version already exists: {manifest.agent_id}@{version}")

        release = build_release(manifest.root, version, self.location.schema_name)
        try:
            published = self.skill_publisher.publish(release)
        except Exception as exc:
            raise RegistryError(f"Cannot publish Agent Skills: {exc}") from exc
        write_release_metadata(release)
        archive = build_agent_archive(manifest.root)
        if len(archive.content) > MAX_ARCHIVE_BYTES:
            raise RegistryError(f"Agent archive exceeds {MAX_ARCHIVE_BYTES} bytes.")
        checksums = calculate_definition_checksums(manifest.root)
        artifact_directory = f"{self.location.volume_root}/agents/{manifest.agent_id}/{version}"
        artifact_path = f"{artifact_directory}/{archive.sha256}.tar.gz"
        release_manifest = release_to_dict(release)
        release_manifest.update(
            {
                "artifact_sha256": archive.sha256,
                "artifact_contains_skill_snapshots": True,
                "published_skills": [
                    {"uc_name": item.uc_name, "sha256": item.sha256, "action": item.action}
                    for item in published
                ],
                "files": archive.files,
            }
        )

        uploaded = False
        try:
            self.workspace.files.create_directory(artifact_directory)
            self.workspace.files.upload(
                artifact_path,
                io.BytesIO(archive.content),
                overwrite=False,
            )
            uploaded = True
            self._execute_sql(
                f"""
                INSERT INTO {_quoted_table(self.location)}
                SELECT
                  :agent_id,
                  :version,
                  :name,
                  :description,
                  :agent_type,
                  :model,
                  :artifact_path,
                  :artifact_sha256,
                  CAST(:artifact_size AS BIGINT),
                  :harness_sha256,
                  :skills_sha256,
                  from_json(:skill_names, 'array<string>'),
                  current_user(),
                  current_timestamp(),
                  :manifest_json
                """,
                {
                    "agent_id": manifest.agent_id,
                    "version": version,
                    "name": manifest.name,
                    "description": manifest.description,
                    "agent_type": manifest.agent_type,
                    "model": manifest.model,
                    "artifact_path": artifact_path,
                    "artifact_sha256": archive.sha256,
                    "artifact_size": str(len(archive.content)),
                    "harness_sha256": checksums.harness,
                    "skills_sha256": checksums.combined_skills,
                    "skill_names": json.dumps(
                        [skill.alias for skill in release.skills], ensure_ascii=False
                    ),
                    "manifest_json": json.dumps(
                        release_manifest, ensure_ascii=False, sort_keys=True
                    ),
                },
            )
        except Exception as exc:
            if uploaded:
                try:
                    self.workspace.files.delete(artifact_path)
                except Exception:
                    pass
            if isinstance(exc, RegistryError):
                raise
            raise RegistryError(f"Cannot register Agent artifact: {exc}") from exc

        registered = self.get(manifest.agent_id, version)
        if registered is None:
            raise RegistryError("Agent metadata was not visible after registration.")
        return registered

    def list(self, *, all_versions: bool = False) -> list[AgentVersion]:
        suffix = "" if all_versions else (
            " QUALIFY ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY registered_at DESC) = 1"
        )
        rows = self._execute_sql(
            f"""
            SELECT agent_id, version, name, description, agent_type, model,
                   artifact_path, artifact_sha256, artifact_size, harness_sha256,
                   skills_sha256, to_json(skill_names), registered_by,
                   CAST(registered_at AS STRING), manifest_json
            FROM {_quoted_table(self.location)}
            {suffix}
            ORDER BY agent_id, registered_at DESC
            """
        )
        return [_row_to_agent_version(row) for row in rows]

    def get(
        self,
        agent_id: str,
        version: str | None = None,
        *,
        allow_missing: bool = False,
    ) -> AgentVersion | None:
        if not AGENT_ID_RE.fullmatch(agent_id):
            raise RegistryError("agent_id must use lower-case kebab-case.")
        if version is not None and not VERSION_RE.fullmatch(version):
            raise RegistryError("Invalid Agent version.")
        version_filter = " AND version = :version" if version else ""
        parameters = {"agent_id": agent_id}
        if version:
            parameters["version"] = version
        rows = self._execute_sql(
            f"""
            SELECT agent_id, version, name, description, agent_type, model,
                   artifact_path, artifact_sha256, artifact_size, harness_sha256,
                   skills_sha256, to_json(skill_names), registered_by,
                   CAST(registered_at AS STRING), manifest_json
            FROM {_quoted_table(self.location)}
            WHERE agent_id = :agent_id{version_filter}
            ORDER BY registered_at DESC
            LIMIT 1
            """,
            parameters,
        )
        if not rows:
            if allow_missing:
                return None
            reference = f"{agent_id}@{version}" if version else agent_id
            raise RegistryError(f"Agent not found in Registry: {reference}")
        return _row_to_agent_version(rows[0])

    def load(
        self,
        agent_id: str,
        *,
        version: str | None = None,
        destination: str | Path | None = None,
        prepare_codex: bool = True,
    ) -> tuple[Path, AgentVersion]:
        registered = self.get(agent_id, version)
        if registered is None:
            raise RegistryError(f"Agent not found in Registry: {agent_id}")
        response = self.workspace.files.download(registered.artifact_path)
        content = response.contents.read()
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != registered.artifact_sha256:
            raise RegistryError(
                f"Artifact checksum mismatch: expected {registered.artifact_sha256}, got {actual_sha256}"
            )

        target = Path(destination or agent_id).expanduser().resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise RegistryError(f"Destination is not an empty directory: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aops-load-", dir=target.parent) as temporary:
            temporary_root = Path(temporary) / "agent"
            temporary_root.mkdir()
            extract_agent_archive(content, temporary_root)
            _verify_file_checksums(temporary_root, registered.release_manifest.get("files", {}))
            manifest = validate_agent(temporary_root)
            if manifest.agent_id != registered.agent_id:
                raise RegistryError("Downloaded artifact agent_id does not match Registry metadata.")
            release = load_release(temporary_root, expected_agent_id=registered.agent_id)
            if release.release_version != registered.version:
                raise RegistryError("Downloaded artifact version does not match Registry metadata.")
            try:
                self.skill_publisher.verify_access(release)
            except Exception as exc:
                raise RegistryError(f"Cannot authorize Agent UC Skills: {exc}") from exc
            if target.exists():
                target.rmdir()
            shutil.move(str(temporary_root), str(target))
        if prepare_codex:
            CodexRuntimeBuilder(otel_port=self.codex_otel_port).prepare(target)
        return target, registered

    def _execute_sql(
        self,
        statement: str,
        parameters: dict[str, str] | None = None,
    ) -> list[list[Any]]:
        try:
            response = self.workspace.statement_execution.execute_statement(
                statement=statement,
                warehouse_id=self.warehouse_id,
                wait_timeout="50s",
                parameters=[
                    StatementParameterListItem(name=name, value=value)
                    for name, value in (parameters or {}).items()
                ],
            )
            deadline = time.monotonic() + 180
            while _statement_state(response) in {"PENDING", "RUNNING"}:
                if time.monotonic() >= deadline:
                    raise RegistryError("SQL statement did not finish within 180 seconds.")
                time.sleep(1)
                response = self.workspace.statement_execution.get_statement(response.statement_id)
            state = _statement_state(response)
            if state != "SUCCEEDED":
                error = getattr(getattr(response, "status", None), "error", None)
                message = getattr(error, "message", None) or f"state={state}"
                raise RegistryError(f"Registry SQL failed: {message}")
            return list(getattr(getattr(response, "result", None), "data_array", None) or [])
        except RegistryError:
            raise
        except Exception as exc:
            raise RegistryError(f"Registry SQL request failed: {exc}") from exc


def build_agent_archive(root: str | Path) -> AgentArchive:
    manifest = validate_agent(root)
    release = load_release(manifest.root, expected_agent_id=manifest.agent_id)
    files = [
        manifest.root / "AGENTS.md",
        manifest.root / "README.md",
        manifest.root / "spec.md",
        manifest.root / ".aops" / "release.json",
    ]
    for directory in (manifest.root / "skills", manifest.root / "agents"):
        files.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    entries = [*files]
    symbolic_link = next((path for path in entries if path.is_symlink()), None)
    if symbolic_link is not None:
        raise RegistryError(f"Symbolic links are not allowed in Agent artifacts: {symbolic_link}")
    if release.release_version == "working":
        raise RegistryError("A working Agent cannot be registered without an explicit version.")

    checksums: dict[str, str] = {}
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in sorted(files):
                relative = path.relative_to(manifest.root).as_posix()
                data = path.read_bytes()
                checksums[relative] = hashlib.sha256(data).hexdigest()
                info = tarfile.TarInfo(relative)
                info.size = len(data)
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))
    content = output.getvalue()
    return AgentArchive(content, hashlib.sha256(content).hexdigest(), checksums)


def extract_agent_archive(content: bytes, destination: str | Path) -> None:
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            for member in archive.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise RegistryError(f"Unsafe artifact path: {member.name}")
                if not member.isfile():
                    raise RegistryError(f"Agent artifact contains an unsupported entry: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise RegistryError(f"Cannot read artifact entry: {member.name}")
                target = root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                target.chmod(member.mode & 0o777)
    except RegistryError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise RegistryError(f"Invalid Agent artifact: {exc}") from exc


def _verify_file_checksums(root: Path, expected: Any) -> None:
    if not isinstance(expected, dict) or not expected:
        raise RegistryError("Registry manifest does not contain file checksums.")
    actual: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    normalized = {str(key): str(value) for key, value in expected.items()}
    if actual != normalized:
        raise RegistryError("Agent artifact file checksums do not match Registry metadata.")


def _quoted_table(location: RegistryLocation) -> str:
    return ".".join(f"`{part}`" for part in (location.catalog, location.schema, location.table))


def _statement_state(response: Any) -> str:
    state = getattr(getattr(response, "status", None), "state", None)
    return str(getattr(state, "value", state) or "UNKNOWN")


def _row_to_agent_version(row: list[Any]) -> AgentVersion:
    if len(row) != 15:
        raise RegistryError(f"Unexpected Registry row width: {len(row)}")
    try:
        skills = tuple(json.loads(row[11] or "[]"))
        manifest = json.loads(row[14] or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RegistryError("Invalid Registry JSON metadata.") from exc
    if not isinstance(manifest, dict):
        raise RegistryError("Registry manifest_json must be an object.")
    return AgentVersion(
        agent_id=str(row[0]),
        version=str(row[1]),
        name=str(row[2]),
        description=str(row[3]),
        agent_type=str(row[4]),
        model=str(row[5]),
        artifact_path=str(row[6]),
        artifact_sha256=str(row[7]),
        artifact_size=int(row[8]),
        harness_sha256=str(row[9]),
        skills_sha256=str(row[10]),
        skill_names=skills,
        registered_by=str(row[12]),
        registered_at=str(row[13]),
        release_manifest=manifest,
    )
