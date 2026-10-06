from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_plugin_manifest_and_hook_contract_are_wired():
    manifest = json.loads((ROOT / "plugins/codex-agentops/.codex-plugin/plugin.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    hooks = json.loads((ROOT / "plugins/codex-agentops/hooks/hooks.json").read_text())
    assert manifest["name"] == "codex-agentops"
    assert manifest["version"] == project["project"]["version"]
    assert set(hooks["hooks"]) == {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "Interrupt",
        "SessionEnd",
    }
    for groups in hooks["hooks"].values():
        assert groups[0]["hooks"][0]["timeout"] <= 5
