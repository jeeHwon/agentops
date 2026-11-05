"""Sessions, segments and turns (SQLite) and raw harness events (JSONL, append-only).

A session is one conversation owned by aops. A segment is the part of a session run by one
harness session; switching harness opens a new segment. A turn is one prompt and its answer.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import Project

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  harness TEXT NOT NULL,              -- harness for the next turn
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  harness TEXT NOT NULL,
  harness_version TEXT NOT NULL,
  native_session TEXT,                -- the harness's own session ID
  started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  segment_id INTEGER NOT NULL REFERENCES segments(id),
  prompt TEXT NOT NULL,               -- what the user asked
  sent TEXT NOT NULL,                 -- what the harness received (prompt + handoff, if any)
  response TEXT NOT NULL,
  tools TEXT NOT NULL,                -- JSON list of tool names
  usage TEXT NOT NULL,                -- JSON object as reported by the harness
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, project: Project):
        self.project = project
        project.runs.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(project.runs / "aops.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def _one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(sql, args)]

    # sessions
    def create_session(self, harness: str) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, harness, now()))
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('current_session', ?)", (session_id,))
        return self.session(session_id)

    def session(self, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sessions WHERE id = ?", session_id)

    def current_session(self) -> dict[str, Any] | None:
        row = self._one("SELECT value FROM state WHERE key = 'current_session'")
        return self.session(row["value"]) if row else None

    def sessions(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT s.*, COUNT(t.id) AS turns, MAX(t.ended_at) AS last_turn_at FROM sessions s "
            "LEFT JOIN turns t ON t.session_id = s.id GROUP BY s.id ORDER BY s.created_at"
        )

    def set_harness(self, session_id: str, harness: str) -> None:
        with self.db:
            self.db.execute("UPDATE sessions SET harness = ? WHERE id = ?", (harness, session_id))

    # segments
    def segments(self, session_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM segments WHERE session_id = ? ORDER BY id", session_id)

    def last_segment(self, session_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM segments WHERE session_id = ? ORDER BY id DESC LIMIT 1", session_id)

    def resumable_segment(self, session_id: str, harness: str, version: str) -> dict[str, Any] | None:
        """Latest segment run by the same harness and version, whose native session can be resumed."""
        return self._one(
            "SELECT * FROM segments WHERE session_id = ? AND harness = ? AND harness_version = ? "
            "AND native_session IS NOT NULL ORDER BY id DESC LIMIT 1",
            session_id, harness, version,
        )

    def open_segment(self, session_id: str, harness: str, version: str, native_session: str | None) -> dict[str, Any]:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO segments (session_id, harness, harness_version, native_session, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, harness, version, native_session, now()),
            )
        return self._one("SELECT * FROM segments WHERE id = ?", cursor.lastrowid)

    def set_native_session(self, segment_id: int, native_session: str | None) -> None:
        with self.db:
            self.db.execute("UPDATE segments SET native_session = ? WHERE id = ?", (native_session, segment_id))

    # turns
    def turns(self, session_id: str, after_turn: int = 0) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT t.*, g.harness FROM turns t JOIN segments g ON g.id = t.segment_id "
            "WHERE t.session_id = ? AND t.id > ? ORDER BY t.id",
            session_id, after_turn,
        )
        for row in rows:
            row["tools"], row["usage"] = json.loads(row["tools"]), json.loads(row["usage"])
        return rows

    def last_turn_id(self, segment_id: int) -> int:
        row = self._one("SELECT MAX(id) AS id FROM turns WHERE segment_id = ?", segment_id)
        return row["id"] or 0

    def add_turn(self, session_id: str, segment_id: int, *, prompt: str, sent: str, response: str,
                 tools: list[str], usage: dict[str, Any], started_at: str) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO turns (session_id, segment_id, prompt, sent, response, tools, usage, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, segment_id, prompt, sent, response, json.dumps(tools),
                 json.dumps(usage, default=str), started_at, now()),
            )
        return cursor.lastrowid

    # raw events
    def append_events(self, session_id: str, segment_id: int, turn_id: int, harness: str,
                      events: list[dict[str, Any]]) -> Path:
        path = self.project.runs / "events" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as out:
            for event in events:
                record = {"schema_version": SCHEMA_VERSION, "at": now(), "session": session_id,
                          "segment": segment_id, "turn": turn_id, "harness": harness, "event": event}
                out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path
