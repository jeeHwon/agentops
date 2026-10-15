from __future__ import annotations

from codex_agentops.redaction import MAX_BODY_CHARS, REDACTED, redact_json_body, redact_text
from codex_agentops.config import Settings
from codex_agentops.events import from_hook_payload
from codex_agentops.manifest import create_agent, load_manifest


def test_redacts_secrets_and_pii_before_storage():
    value = (
        "email=user@example.com phone=010-1234-5678 "
        "api_key=sk-abcdefghijklmnopqrstuvwxyz password=hunter2 "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
    )
    result = redact_text(value)
    assert "user@example.com" not in result
    assert "010-1234-5678" not in result
    assert "hunter2" not in result
    assert "abcdefghijklmnopqrstuvwxyz" not in result
    assert REDACTED in result


def test_caps_each_serialized_body():
    result = redact_json_body({"output": "x" * (MAX_BODY_CHARS + 100)})
    assert result["truncated"] is True
    assert "[TRUNCATED" in result["content"]


def test_opt_in_hook_body_is_redacted_before_storage(tmp_path):
    root = create_agent("demo-agent", tmp_path / "agent")
    event = from_hook_payload(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t",
         "prompt": "alice@example.com password=hunter2"},
        load_manifest(root), Settings("test", "https://example.com", capture_content=True),
    )
    assert "alice@example.com" not in event.payload["prompt"]
    assert "hunter2" not in event.payload["prompt"]
