from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


RUNTIME_ROOT = Path(__file__).parents[1] / "runtime"


@dataclass(frozen=True)
class AgentRuntime:
    config: dict
    system_prompt: str

    @property
    def agent_id(self) -> str:
        return str(self.config["agent_id"])

    @property
    def release_version(self) -> str:
        return str(self.config["release_version"])

    @property
    def skills(self) -> list[dict]:
        return list(self.config.get("skills", []))


def load_runtime() -> AgentRuntime:
    config_path = RUNTIME_ROOT / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("runtime/config.yaml must contain an object")

    sections: list[str] = []
    for title, relative in (
        ("Agent Harness", "AGENTS.md"),
        ("Business Specification", "spec.md"),
    ):
        path = RUNTIME_ROOT / relative
        if path.is_file():
            sections.append(f"# {title}\n\n{path.read_text(encoding='utf-8')}")

    for skill in raw.get("skills", []):
        path = RUNTIME_ROOT / str(skill["markdown"])
        body = path.read_text(encoding="utf-8")
        sections.append(
            "# Skill: "
            f"{skill['alias']} (UC={skill['uc_name']}, version={skill['version']}, "
            f"sha256={skill['sha256']})\n\n{body}"
        )

    for subagent in raw.get("subagents", []):
        path = RUNTIME_ROOT / str(subagent["instructions"])
        body = path.read_text(encoding="utf-8")
        sections.append(
            f"# Subagent Definition: {subagent['id']}\n\n"
            f"Assigned skills: {', '.join(subagent.get('skills', [])) or 'none'}\n\n{body}"
        )

    if not sections:
        raise RuntimeError("Agent Runtime does not contain Harness instructions")
    return AgentRuntime(config=raw, system_prompt="\n\n---\n\n".join(sections))


runtime = load_runtime()
