"""What a harness receives when it joins a session that other harnesses worked on.

Two parts: a handoff file with every turn the harness has not seen, and a short notice placed
before the prompt with the most recent of those turns, cut to the harness's token budget.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 3  # rough estimate for mixed Korean and English text


def render(turns: list[dict[str, Any]]) -> str:
    parts = []
    for turn in turns:
        tools = f"\n(tools: {', '.join(turn['tools'])})" if turn["tools"] else ""
        parts.append(f"### turn {turn['id']} · {turn['harness']}\n\nUser:\n{turn['prompt']}\n\n"
                     f"Assistant:\n{turn['response']}{tools}")
    return "\n\n".join(parts)


def write_file(path: Path, turns: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# aops handoff\n\n" + render(turns) + "\n", encoding="utf-8")
    return path


def recent(turns: list[dict[str, Any]], budget_tokens: int) -> list[dict[str, Any]]:
    """The most recent turns that fit the budget. The latest turn is always kept."""
    kept, used = [], 0
    for turn in reversed(turns):
        size = len(render([turn])) // CHARS_PER_TOKEN
        if kept and used + size > budget_tokens:
            break
        kept.append(turn)
        used += size
    return list(reversed(kept))


def notice(turns: list[dict[str, Any]], file: Path, budget_tokens: int) -> str:
    shown = recent(turns, budget_tokens)
    omitted = len(turns) - len(shown)
    earlier = f" {omitted} earlier turn(s) are only in the file." if omitted else ""
    return (
        "[aops handoff] Earlier parts of this conversation ran in another assistant session. "
        f"The full record of what you have not seen is in {file}.{earlier} The most recent turns follow.\n\n"
        f"{render(shown)}\n\n[end of handoff] Continue the conversation with the next message.\n\n"
    )
