from __future__ import annotations

from types import SimpleNamespace

from codex_agentops.config import Settings
from codex_agentops.service import AgentOpsService


class _Registry:
    def __init__(self):
        self.calls = []

    def list(self, *, all_versions=False):
        self.calls.append(("list", all_versions))
        return [SimpleNamespace(agent_id="sample")]

    def load(self, agent_id, *, version=None, destination=None, prepare_codex=True):
        self.calls.append(("load", agent_id, version, destination, prepare_codex))
        return destination, SimpleNamespace(agent_id=agent_id, version=version)

    def register(self, path, version):
        self.calls.append(("register", path, version))
        return SimpleNamespace(agent_id="sample", version=version)


def test_platform_service_exposes_same_registry_lifecycle(tmp_path):
    registry = _Registry()
    service = AgentOpsService(
        Settings("test", "https://example.cloud.databricks.com"),
        registry_client=registry,
    )

    assert service.list_agents()[0].agent_id == "sample"
    service.load_agent("sample", version="1.0.0", target=tmp_path / "source", runtime="source")
    service.load_agent("sample", version="1.0.0", target=tmp_path / "codex", runtime="codex")
    service.register_agent(tmp_path, "1.0.1")

    assert registry.calls == [
        ("list", False),
        ("load", "sample", "1.0.0", tmp_path / "source", False),
        ("load", "sample", "1.0.0", tmp_path / "codex", True),
        ("register", tmp_path, "1.0.1"),
    ]
