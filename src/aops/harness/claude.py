"""Claude Code through claude-agent-sdk, running the claude executable the user installed.

aops does not read or move the user's login; the executable uses whatever login it already has.
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import Turn


class ClaudeHarness:
    name = "claude"

    def __init__(self, model: str | None = None, cli_path: str | None = None):
        self.model = model
        self.cli_path = cli_path or shutil.which("claude")

    def version(self) -> str:
        if self.cli_path is None:
            from claude_agent_sdk._cli_version import __cli_version__  # the SDK's bundled CLI
            return __cli_version__
        out = subprocess.run([self.cli_path, "--version"], capture_output=True, text=True, check=True).stdout
        return out.split()[0]

    def run(self, prompt: str, *, cwd: Path, resume: str | None = None) -> Turn:
        return asyncio.run(self._run(prompt, cwd, resume))

    async def _run(self, prompt: str, cwd: Path, resume: str | None) -> Turn:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

        options = ClaudeAgentOptions(cwd=str(cwd), resume=resume, model=self.model, cli_path=self.cli_path)
        turn, texts = Turn(text="", native_session=resume), []
        async for message in query(prompt=prompt, options=options):
            turn.events.append({"type": type(message).__name__, **_plain(message)})
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        turn.tools.append(tool_name(block.name, block.input))
            elif isinstance(message, ResultMessage):
                turn.native_session = message.session_id
                turn.usage = dict(message.usage or {})
                turn.text = message.result or ""
        turn.text = turn.text or "\n".join(texts)
        return turn


def tool_name(name: str, tool_input: dict[str, Any] | None) -> str:
    """Claude runs skills through its Skill tool; record them as skill:<name>."""
    if name == "Skill" and (tool_input or {}).get("skill"):
        return f"skill:{tool_input['skill']}"
    return name


def _plain(message: Any) -> dict[str, Any]:
    return dataclasses.asdict(message) if dataclasses.is_dataclass(message) else dict(vars(message))
