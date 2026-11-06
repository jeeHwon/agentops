from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aops.server import create_app  # noqa: E402
from aops.server.mapping import COMMANDS, command_map, markdown, routes  # noqa: E402

app = create_app()
client = TestClient(app)


def test_every_route_is_under_api_v1_and_tagged():
    for row in routes(app):
        assert row["path"].startswith("/api/v1/"), row
        assert row["tag"], row
        if row["tag"] != "서버" or row["command"] is not None or row["screen"]:
            assert row["editions"], row


def test_every_command_has_a_route_or_a_reason():
    for row in command_map(app):
        assert row["routes"] or row["cli_only"], row["command"]


def test_every_route_command_is_listed():
    listed = {c.name for c in COMMANDS}
    named = {r["command"] for r in routes(app) if r["command"]}
    assert named <= listed, named - listed


def test_stub_returns_a_problem_with_the_command():
    res = client.post("/api/v1/projects/p/validations", json={"mode": "job"})
    assert res.status_code == 501
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "not_implemented"
    assert res.json()["command"] == "aops validate"


def test_stream_stub_returns_a_problem():
    res = client.post("/api/v1/sessions/s/turns/stream", json={"prompt": "hi"})
    assert res.status_code == 501
    assert res.json()["command"] == 'aops run "<질문>"'


def test_meta_routes_work_now():
    assert client.get("/api/v1/meta/health").json() == {"status": "ok"}
    rows = client.get("/api/v1/meta/commands").json()
    assert {r["command"] for r in rows} == {c.name for c in COMMANDS}


def test_turn_stream_documents_ag_ui_events():
    spec = app.openapi()
    stream = spec["paths"]["/api/v1/sessions/{session_id}/turns/stream"]["post"]["responses"]["200"]["content"]
    schema = stream["text/event-stream"]["itemSchema"]["properties"]["data"]["contentSchema"]
    assert "RUN_STARTED" in schema["discriminator"]["mapping"]
    assert "CUSTOM" in schema["discriminator"]["mapping"]


def test_api_doc_is_current():
    doc = Path(__file__).resolve().parents[1] / "docs" / "api.md"
    assert doc.read_text(encoding="utf-8") == markdown(app), "run scripts/api_docs.py"
