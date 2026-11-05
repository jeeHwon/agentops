"""Codex through the openai-codex SDK (codex app-server), running the codex executable the user installed.

The executable keeps its own configuration, including any managed configuration set by the company.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import Turn

# Thread item types that are tool use, and how each is named in aops.
TOOL_ITEMS = {"commandExecution": "shell", "fileChange": "apply_patch", "webSearch": "web_search"}
# Codex has no skill event; it reads the skill's SKILL.md when it uses the skill.
SKILL_READ = re.compile(r"skills/([^/\s\"'\\]+)/SKILL\.md")


class CodexHarness:
    name = "codex"

    def __init__(self, model: str | None = None, codex_bin: str | None = None, sandbox: str | None = None):
        self.model = model
        self.codex_bin = codex_bin or shutil.which("codex")
        self.sandbox = sandbox  # read-only | workspace-write | full-access; None keeps Codex's own setting

    def version(self) -> str:
        out = subprocess.run([self.codex_bin or "codex", "--version"], capture_output=True, text=True, check=True).stdout
        return out.split()[-1]

    def run(self, prompt: str, *, cwd: Path, resume: str | None = None) -> Turn:
        from openai_codex import Codex, CodexConfig, Sandbox

        options: dict[str, Any] = {"cwd": str(cwd), "model": self.model}
        if self.sandbox:
            options["sandbox"] = Sandbox(self.sandbox)
        with Codex(CodexConfig(codex_bin=self.codex_bin, cwd=str(cwd))) as codex:
            thread = codex.thread_resume(resume, **options) if resume else codex.thread_start(**options)
            events = []
            for event in thread.turn(prompt).stream():
                payload = event.payload.model_dump(mode="json", by_alias=True) if hasattr(event.payload, "model_dump") else event.payload
                events.append({"method": event.method, "payload": payload})
        return read(events, native_session=thread.id)


def read(events: list[dict[str, Any]], native_session: str | None) -> Turn:
    """Answer, tools, skills and usage from raw app-server notifications."""
    turn = Turn(text="", native_session=native_session, events=events)
    messages: list[tuple[str | None, str]] = []  # (phase, text)
    for event in events:
        payload = event.get("payload") or {}
        if event.get("method") == "item/completed":
            item = payload.get("item", {})
            kind = item.get("type")
            if kind == "agentMessage":
                messages.append((item.get("phase"), item.get("text") or ""))
            elif kind == "mcpToolCall":
                turn.tools.append(f"{item.get('server')}.{item.get('tool')}")
            elif kind in TOOL_ITEMS:
                turn.tools.append(TOOL_ITEMS[kind])
                for skill in SKILL_READ.findall(json.dumps(item, ensure_ascii=False)):
                    if f"skill:{skill}" not in turn.tools:
                        turn.tools.append(f"skill:{skill}")
        elif event.get("method") == "thread/tokenUsage/updated":
            turn.usage = payload.get("tokenUsage") or payload.get("token_usage") or {}
    # Same rule as the SDK: the last final answer, else the last message.
    finals = [text for phase, text in messages if phase == "final_answer"]
    turn.text = finals[-1] if finals else (messages[-1][1] if messages else "")
    return turn
