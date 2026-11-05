from aops.harness import claude, codex


def item(kind, **fields):
    return {"method": "item/completed", "payload": {"item": {"type": kind, **fields}}}


def test_codex_read_picks_final_answer_tools_skills_and_usage():
    events = [
        item("commandExecution", command="cat .agents/skills/maple-sql-style/SKILL.md"),
        item("agentMessage", phase="commentary", text="looking"),
        item("mcpToolCall", server="dbsql", tool="query"),
        item("agentMessage", phase="final_answer", text="done"),
        {"method": "thread/tokenUsage/updated", "payload": {"tokenUsage": {"total": {"totalTokens": 10}}}},
    ]
    turn = codex.read(events, native_session="t1")
    assert turn.text == "done"
    assert turn.tools == ["shell", "skill:maple-sql-style", "dbsql.query"]
    assert turn.usage == {"total": {"totalTokens": 10}}
    assert turn.native_session == "t1" and turn.events is events


def test_codex_read_without_final_phase_uses_last_message():
    turn = codex.read([item("agentMessage", text="a"), item("agentMessage", text="b")], native_session=None)
    assert turn.text == "b" and turn.tools == []


def test_claude_skill_tool_is_recorded_by_skill_name():
    assert claude.tool_name("Skill", {"skill": "maple-sql-style"}) == "skill:maple-sql-style"
    assert claude.tool_name("Read", {"file_path": "x"}) == "Read"
