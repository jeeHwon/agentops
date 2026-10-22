from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings, load_settings
from .catalog import catalog_record
from .manifest import AgentManifest, create_agent, load_manifest, read_release_metadata, validate_agent
from .registry import AgentVersion, RegistryClient, RegistryError, parse_registry
from .release import AssemblyResult, CodexRuntimeBuilder


class AgentOpsService:
    """Local Python facade for the CLI's common agent lifecycle."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        registry_client: RegistryClient | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._registry_client = registry_client

    @property
    def registry(self) -> RegistryClient:
        if self._registry_client is None:
            if not self.settings.registry:
                raise RegistryError("Agent Registry가 설정되지 않았습니다. `aops configure`를 실행하세요.")
            if not self.settings.warehouse_id:
                raise RegistryError("SQL Warehouse가 설정되지 않았습니다. `aops configure`를 실행하세요.")
            self._registry_client = RegistryClient(
                profile=self.settings.profile,
                warehouse_id=self.settings.warehouse_id,
                location=parse_registry(self.settings.registry),
                settings=self.settings,
            )
        return self._registry_client

    def init_agent(
        self, target: str | Path, agent_id: str, *, build_type: str = "skill",
        framework: str = "langgraph",
    ) -> Path:
        if build_type == "skill":
            root = create_agent(agent_id, target)
            CodexRuntimeBuilder().prepare(root)
        else:
            from .project import create_project

            root = create_project(
                agent_id, target, build_type=build_type, framework=framework,
                profile=self.settings.profile,
            )
        return root

    def validate_agent(self, path: str | Path) -> AgentManifest:
        manifest = validate_agent(path)
        if manifest.build_type == "skill":
            CodexRuntimeBuilder().prepare(manifest.root)
        return manifest

    def list_agents(
        self,
        *,
        all_versions: bool = False,
        authorized_only: bool = True,
    ) -> list[AgentVersion]:
        return self.registry.list(
            all_versions=all_versions,
            authorized_only=authorized_only,
        )

    def export_catalog(self, *, all_versions: bool = False) -> list[dict[str, Any]]:
        registry = self.registry
        return [
            catalog_record(release, registry.location)
            for release in registry.list(all_versions=all_versions, authorized_only=True)
        ]

    def load_agent(
        self,
        agent_id: str,
        *,
        version: str | None = None,
        target: str | Path | None = None,
        runtime: str = "codex",
    ) -> tuple[Path, AgentVersion]:
        if runtime not in {"codex", "source"}:
            raise ValueError("runtime must be codex or source.")
        return self.registry.load(
            agent_id,
            version=version,
            destination=target,
            prepare_codex=runtime == "codex",
        )

    def register_agent(self, path: str | Path, version: str) -> AgentVersion:
        return self.registry.register(path, version)

    def prepare_codex_workspace(self, path: str | Path) -> AssemblyResult:
        return CodexRuntimeBuilder().prepare(path)

    def get_agent_status(self, path: str | Path) -> dict[str, Any]:
        manifest = load_manifest(path)
        release = read_release_metadata(manifest.root)
        return {
            "agent_id": manifest.agent_id,
            "agent_type": manifest.agent_type,
            "model": manifest.model_service,
            "release_version": (release or {}).get("version", "working"),
            "codex_runtime_ready": (
                (manifest.root / ".agents" / "skills").is_dir()
                and (manifest.root / ".codex" / "config.toml").is_file()
            ),
            "root": str(manifest.root),
        }
