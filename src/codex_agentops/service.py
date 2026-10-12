from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings, load_settings
from .manifest import AgentManifest, create_agent, load_manifest, read_release_metadata, validate_agent
from .registry import AgentVersion, RegistryClient, RegistryError, parse_registry
from .release import AssemblyResult, CodexRuntimeBuilder


class AgentOpsService:
    """Platform-neutral backend API used by the CLI and external Web UIs."""

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
                codex_otel_port=self.settings.otel_port,
            )
        return self._registry_client

    def init_agent(self, target: str | Path, agent_id: str) -> Path:
        root = create_agent(agent_id, target)
        CodexRuntimeBuilder(otel_port=self.settings.otel_port).prepare(root)
        return root

    def validate_agent(self, path: str | Path) -> AgentManifest:
        manifest = validate_agent(path)
        CodexRuntimeBuilder(otel_port=self.settings.otel_port).prepare(manifest.root)
        return manifest

    def list_agents(self, *, all_versions: bool = False) -> list[AgentVersion]:
        return self.registry.list(all_versions=all_versions)

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
        return CodexRuntimeBuilder(otel_port=self.settings.otel_port).prepare(path)

    def get_agent_status(self, path: str | Path) -> dict[str, Any]:
        manifest = load_manifest(path)
        release = read_release_metadata(manifest.root)
        return {
            "agent_id": manifest.agent_id,
            "agent_type": manifest.agent_type,
            "model": manifest.model,
            "release_version": (release or {}).get("version", "working"),
            "codex_runtime_ready": (
                (manifest.root / ".agents" / "skills").is_dir()
                and (manifest.root / ".codex" / "config.toml").is_file()
            ),
            "root": str(manifest.root),
        }
