from pathlib import Path

from aops.harness import Turn
from aops.project import Project
from aops.runner import run_turn
from aops.store import Store


class FakeHarness:
    """Answers with what it remembers; records what it received."""

    calls: list[dict] = []

    def __init__(self, name: str, version: str = "1"):
        self.name, self._version = name, version

    def version(self) -> str:
        return self._version

    def run(self, prompt: str, *, cwd: Path, resume: str | None = None) -> Turn:
        FakeHarness.calls.append({"harness": self.name, "prompt": prompt, "resume": resume})
        native = resume or f"{self.name}-native-{len(FakeHarness.calls)}"
        return Turn(text=f"{self.name} answer {len(FakeHarness.calls)}", native_session=native,
                    tools=["Read"], events=[{"raw": prompt}])


def fake(versions: dict[str, str] | None = None):
    return lambda name, **_: FakeHarness(name, (versions or {}).get(name, "1"))


def setup(tmp_path):
    FakeHarness.calls = []
    project = Project(root=tmp_path)
    return project, Store(project)


def test_first_turn_has_no_handoff(tmp_path):
    project, store = setup(tmp_path)
    result = run_turn(project, "hello", store=store, get_harness=fake())
    assert result["handoff_turns"] == 0
    assert FakeHarness.calls[0] == {"harness": "claude", "prompt": "hello", "resume": None}
    assert store.segments(result["session"])[0]["native_session"] == "claude-native-1"
    events = (tmp_path / "runs" / "events" / f"{result['session']}.jsonl").read_text()
    assert '"raw": "hello"' in events


def test_same_harness_continues_its_own_session(tmp_path):
    project, store = setup(tmp_path)
    run_turn(project, "one", store=store, get_harness=fake())
    result = run_turn(project, "two", store=store, get_harness=fake())
    assert result["handoff_turns"] == 0
    assert FakeHarness.calls[1] == {"harness": "claude", "prompt": "two", "resume": "claude-native-1"}
    assert len(store.segments(result["session"])) == 1


def test_switch_hands_off_and_switch_back_resumes(tmp_path):
    project, store = setup(tmp_path)
    run_turn(project, "remember the word apple", store=store, get_harness=fake())
    to_codex = run_turn(project, "what was the word?", harness_name="codex", store=store, get_harness=fake())
    assert to_codex["handoff_turns"] == 1
    sent = FakeHarness.calls[1]["prompt"]
    assert "[aops handoff]" in sent and "remember the word apple" in sent and sent.endswith("what was the word?")
    assert FakeHarness.calls[1]["resume"] is None
    assert list((tmp_path / ".aops" / "handoff").glob("*.md"))

    back = run_turn(project, "and now?", harness_name="claude", store=store, get_harness=fake())
    # Claude resumes its own session and only receives the Codex turn it did not see.
    assert FakeHarness.calls[2]["resume"] == "claude-native-1"
    assert back["handoff_turns"] == 1
    assert "what was the word?" in FakeHarness.calls[2]["prompt"]
    assert "remember the word apple" not in FakeHarness.calls[2]["prompt"]


def test_new_harness_version_gets_a_full_handoff(tmp_path):
    project, store = setup(tmp_path)
    run_turn(project, "one", store=store, get_harness=fake({"claude": "1"}))
    result = run_turn(project, "two", store=store, get_harness=fake({"claude": "2"}))
    assert result["handoff_turns"] == 1
    assert FakeHarness.calls[1]["resume"] is None


def test_handoff_keeps_latest_turns_within_budget(tmp_path):
    project = Project(root=tmp_path, handoff_tokens={"codex": 60})
    store = Store(project)
    FakeHarness.calls = []
    for i in range(5):
        run_turn(project, f"turn {i} " + "x" * 120, store=store, get_harness=fake())
    run_turn(project, "next", harness_name="codex", store=store, get_harness=fake())
    sent = FakeHarness.calls[-1]["prompt"]
    assert "turn 4" in sent and "turn 0" not in sent
    assert "earlier turn(s) are only in the file" in sent


def test_failed_switch_turn_keeps_the_handoff_for_the_retry(tmp_path):
    project, store = setup(tmp_path)
    run_turn(project, "remember the word apple", store=store, get_harness=fake())

    class Broken(FakeHarness):
        def run(self, prompt, *, cwd, resume=None):
            raise RuntimeError("harness failed")

    try:
        run_turn(project, "first try", harness_name="codex", store=store, get_harness=lambda name, **_: Broken(name))
    except RuntimeError:
        pass
    retry = run_turn(project, "second try", harness_name="codex", store=store, get_harness=fake())
    assert retry["handoff_turns"] == 1
    assert "remember the word apple" in FakeHarness.calls[-1]["prompt"]
    assert [s["harness"] for s in store.segments(retry["session"])] == ["claude", "codex"]
