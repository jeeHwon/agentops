"""One turn of a session: pick the segment, hand off what the harness has not seen, run, record."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import handoff, harness
from .project import Project
from .store import Store, now


def run_turn(project: Project, prompt: str, *, harness_name: str | None = None, store: Store | None = None,
             get_harness: Callable[..., harness.Harness] = harness.get) -> dict[str, Any]:
    store = store or Store(project)
    session = store.current_session() or store.create_session(harness_name or project.harness)
    name = harness_name or session["harness"]
    if name != session["harness"]:
        store.set_harness(session["id"], name)

    adapter = get_harness(name, model=project.model)
    version = adapter.version()

    segment = store.last_segment(session["id"])
    unseen: list[dict[str, Any]] = []
    if segment is None or (segment["harness"], segment["harness_version"]) != (name, version):
        # A new segment. Resume this harness's own session when the same harness and version ran
        # before; hand off only the turns it has not seen. The segment is recorded after the turn
        # succeeds, so a failed first turn leaves nothing half-made behind.
        earlier = store.resumable_segment(session["id"], name, version)
        native = earlier["native_session"] if earlier else None
        seen_up_to = store.last_turn_id(earlier["id"]) if earlier else 0
        unseen = store.turns(session["id"], after_turn=seen_up_to)
        segment = None
    else:
        native = segment["native_session"]

    sent = prompt
    if unseen:
        name_part = f"{session['id']}-{name}-after-{unseen[0]['id'] - 1}"
        file = handoff.write_file(project.work / "handoff" / f"{name_part}.md", unseen)
        sent = handoff.notice(unseen, file, project.handoff_tokens.get(name, 2000)) + prompt

    started_at = now()
    turn = adapter.run(sent, cwd=project.root, resume=native)
    if segment is None:
        segment = store.open_segment(session["id"], name, version, turn.native_session)
    else:
        store.set_native_session(segment["id"], turn.native_session)
    turn_id = store.add_turn(session["id"], segment["id"], prompt=prompt, sent=sent, response=turn.text,
                             tools=turn.tools, usage=turn.usage, started_at=started_at)
    store.append_events(session["id"], segment["id"], turn_id, name, turn.events)
    return {"session": session["id"], "segment": segment["id"], "turn": turn_id, "harness": name,
            "harness_version": version, "handoff_turns": len(unseen), "response": turn.text,
            "tools": turn.tools, "usage": turn.usage}
