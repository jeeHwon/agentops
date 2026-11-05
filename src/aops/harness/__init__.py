"""Harness adapters.

Each harness is one module with one class that follows `Harness`. The class imports its SDK
inside its methods, so aops itself has no harness dependency and other packages can add a
harness through the ``aops.harness`` entry point group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Protocol

BUILTIN = {
    "claude": "aops.harness.claude:ClaudeHarness",
    "codex": "aops.harness.codex:CodexHarness",
}


@dataclass
class Turn:
    """What one harness turn produced."""

    text: str
    native_session: str | None
    tools: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)  # raw harness output, as received


class Harness(Protocol):
    name: str

    def version(self) -> str:
        """Version of the harness executable that will run the turn."""

    def run(self, prompt: str, *, cwd: Path, resume: str | None = None) -> Turn:
        """Run one turn. `resume` is the harness's own session ID to continue."""


def names() -> list[str]:
    return sorted({*BUILTIN, *(ep.name for ep in entry_points(group="aops.harness"))})


def get(name: str, **options: Any) -> Harness:
    if name in BUILTIN:
        module, attr = BUILTIN[name].split(":")
        factory = getattr(import_module(module), attr)
    else:
        found = [ep for ep in entry_points(group="aops.harness") if ep.name == name]
        if not found:
            raise ValueError(f"unknown harness '{name}'. Available: {', '.join(names())}")
        factory = found[0].load()
    return factory(**options)
