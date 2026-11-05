"""Project folder and the settings aops reads from aops.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "aops.toml"

# Size of what is handed to the next harness, in tokens (design default).
DEFAULT_HANDOFF_TOKENS = {"codex": 2000, "claude": 4000}


@dataclass(frozen=True)
class Project:
    root: Path
    harness: str = "claude"
    model: str | None = None
    handoff_tokens: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_HANDOFF_TOKENS))

    @property
    def runs(self) -> Path:
        """Records that stay with the project (sessions, raw harness events). Not committed."""
        return self.root / "runs"

    @property
    def work(self) -> Path:
        """Scratch files such as handoff files. Not committed."""
        return self.root / ".aops"

    @classmethod
    def find(cls, start: Path | None = None) -> Project:
        """The nearest folder with aops.toml, or the start folder when there is none."""
        here = (start or Path.cwd()).resolve()
        for folder in (here, *here.parents):
            if (folder / CONFIG_FILE).is_file():
                return cls.load(folder)
        return cls(root=here)

    @classmethod
    def load(cls, root: Path) -> Project:
        data = tomllib.loads((root / CONFIG_FILE).read_text(encoding="utf-8"))
        agent = data.get("agent", {})
        session = data.get("session", {})
        tokens = {**DEFAULT_HANDOFF_TOKENS, **{k: int(v) for k, v in session.get("handoff_tokens", {}).items()}}
        return cls(
            root=root.resolve(),
            harness=agent.get("harness", "claude"),
            model=agent.get("model"),
            handoff_tokens=tokens,
        )
