from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .events import HookEvent
from .usage import TokenUsage


SCHEMA_VERSION = 3
LEASE_NS = 120 * 1_000_000_000


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    event_name: str
    created_ns: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class ToolObservation:
    conversation_id: str
    call_id: str
    tool_name: str
    observed_ns: int
    duration_ms: float
    success: bool


@dataclass(frozen=True)
class RuntimeAgent:
    agent_id: str
    agent_type: str
    turn_id: str
    started_ns: int
    ended_ns: int | None
    token_usage: TokenUsage | None = None
    token_usage_source: str | None = None


@dataclass(frozen=True)
class PendingTurn:
    turn_key: str
    session_id: str
    turn_id: str
    agent_id: str
    agent_root: str
    terminal_event: str
    attempts: int
    events: tuple[StoredEvent, ...]
    token_usage: TokenUsage | None = None
    token_usage_source: str | None = None
    runtime_agents: tuple[RuntimeAgent, ...] = ()
    tool_observations: tuple[ToolObservation, ...] = ()


class Outbox:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turn_uploads (
                    turn_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_root TEXT NOT NULL,
                    terminal_event TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_ns INTEGER NOT NULL DEFAULT 0,
                    lease_until_ns INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    trace_id TEXT,
                    updated_ns INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_id TEXT PRIMARY KEY,
                    turn_key TEXT NOT NULL REFERENCES turn_uploads(turn_key),
                    event_name TEXT NOT NULL,
                    created_ns INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_uploads_ready
                    ON turn_uploads(status, next_attempt_ns, terminal_event);
                CREATE INDEX IF NOT EXISTS idx_outbox_events_turn
                    ON outbox_events(turn_key, created_ns);
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    agent_root TEXT NOT NULL,
                    model TEXT,
                    started_ns INTEGER NOT NULL,
                    ended_ns INTEGER,
                    updated_ns INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS token_usage_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT,
                    observed_ns INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    cache_write_input_tokens INTEGER NOT NULL,
                    reasoning_output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_token_usage_session_time
                    ON token_usage_events(session_id, observed_ns);
                CREATE INDEX IF NOT EXISTS idx_token_usage_turn
                    ON token_usage_events(session_id, turn_id, source);
                CREATE TABLE IF NOT EXISTS runtime_agents (
                    runtime_agent_id TEXT PRIMARY KEY,
                    root_session_id TEXT NOT NULL,
                    root_turn_key TEXT NOT NULL REFERENCES turn_uploads(turn_key),
                    turn_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    started_ns INTEGER NOT NULL,
                    ended_ns INTEGER,
                    updated_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_agents_root_turn
                    ON runtime_agents(root_turn_key, started_ns);
                CREATE TABLE IF NOT EXISTS tool_observations (
                    event_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    observed_ns INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    success INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tool_observations_conversation_time
                    ON tool_observations(conversation_id, observed_ns);
                CREATE INDEX IF NOT EXISTS idx_tool_observations_call
                    ON tool_observations(call_id);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def record_session(
        self,
        *,
        session_id: str,
        agent_id: str,
        agent_root: str,
        model: str | None = None,
        observed_ns: int | None = None,
        ended: bool = False,
    ) -> None:
        now = observed_ns or time.time_ns()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_sessions(
                    session_id, agent_id, agent_root, model, started_ns, ended_ns, updated_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    agent_root = excluded.agent_root,
                    model = COALESCE(excluded.model, agent_sessions.model),
                    ended_ns = COALESCE(excluded.ended_ns, agent_sessions.ended_ns),
                    updated_ns = excluded.updated_ns
                """,
                (session_id, agent_id, agent_root, model, now, now if ended else None, now),
            )

    def has_agent_session(self, session_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row is not None

    def append_token_usage(
        self,
        *,
        event_id: str,
        session_id: str,
        observed_ns: int,
        source: str,
        usage: TokenUsage,
        turn_id: str | None = None,
        model: str | None = None,
        require_agent_session: bool = True,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if require_agent_session:
                session = connection.execute(
                    """
                    SELECT 1 FROM agent_sessions WHERE session_id = ?
                    UNION ALL
                    SELECT 1 FROM runtime_agents WHERE runtime_agent_id = ?
                    LIMIT 1
                    """,
                    (session_id, session_id),
                ).fetchone()
                if session is None:
                    return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO token_usage_events(
                    event_id, session_id, turn_id, observed_ns, source, model,
                    input_tokens, output_tokens, cached_input_tokens,
                    cache_write_input_tokens, reasoning_output_tokens, total_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    turn_id,
                    observed_ns,
                    source,
                    model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_input_tokens,
                    usage.cache_write_input_tokens,
                    usage.reasoning_output_tokens,
                    usage.total_tokens,
                ),
            )
            return cursor.rowcount == 1

    def append_tool_observation(
        self,
        *,
        event_id: str,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        observed_ns: int,
        duration_ms: float,
        success: bool,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            known = connection.execute(
                """
                SELECT 1 FROM agent_sessions WHERE session_id = ?
                UNION ALL
                SELECT 1 FROM runtime_agents WHERE runtime_agent_id = ?
                LIMIT 1
                """,
                (conversation_id, conversation_id),
            ).fetchone()
            if known is None:
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO tool_observations(
                    event_id, conversation_id, call_id, tool_name,
                    observed_ns, duration_ms, success
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    call_id,
                    tool_name,
                    observed_ns,
                    max(0.0, duration_ms),
                    int(success),
                ),
            )
            return cursor.rowcount == 1

    def append(self, event: HookEvent) -> bool:
        now = time.time_ns()
        terminal = event.event_name if event.terminal else None
        runtime_agent_id = str(event.payload.get("codex_agent_id") or "").strip()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO agent_sessions(
                    session_id, agent_id, agent_root, model, started_ns, updated_ns
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    agent_root = excluded.agent_root,
                    model = COALESCE(excluded.model, agent_sessions.model),
                    updated_ns = excluded.updated_ns
                """,
                (
                    event.session_id,
                    event.agent_id,
                    event.agent_root,
                    event.payload.get("model"),
                    event.created_ns,
                    now,
                ),
            )
            root_turn_key = event.turn_key
            root_turn_id = event.turn_id
            root_agent_id = event.agent_id
            root_agent_root = event.agent_root
            if runtime_agent_id:
                active = connection.execute(
                    """
                    SELECT turn_key, turn_id, agent_id, agent_root
                    FROM turn_uploads
                    WHERE session_id = ? AND terminal_event IS NULL
                    ORDER BY updated_ns DESC
                    LIMIT 1
                    """,
                    (event.session_id,),
                ).fetchone()
                if active is not None:
                    root_turn_key = active["turn_key"]
                    root_turn_id = active["turn_id"]
                    root_agent_id = active["agent_id"]
                    root_agent_root = active["agent_root"]
                else:
                    existing_agent = connection.execute(
                        """
                        SELECT t.turn_key, t.turn_id, t.agent_id, t.agent_root
                        FROM runtime_agents AS r
                        JOIN turn_uploads AS t ON t.turn_key = r.root_turn_key
                        WHERE r.runtime_agent_id = ?
                        LIMIT 1
                        """,
                        (runtime_agent_id,),
                    ).fetchone()
                    if existing_agent is not None:
                        root_turn_key = existing_agent["turn_key"]
                        root_turn_id = existing_agent["turn_id"]
                        root_agent_id = existing_agent["agent_id"]
                        root_agent_root = existing_agent["agent_root"]
            connection.execute(
                """
                INSERT INTO turn_uploads(
                    turn_key, session_id, turn_id, agent_id, agent_root,
                    terminal_event, status, updated_ns
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(turn_key) DO UPDATE SET
                    terminal_event = COALESCE(turn_uploads.terminal_event, excluded.terminal_event),
                    updated_ns = excluded.updated_ns
                """,
                (
                    root_turn_key,
                    event.session_id,
                    root_turn_id,
                    root_agent_id,
                    root_agent_root,
                    terminal,
                    now,
                ),
            )
            if runtime_agent_id:
                agent_type = str(event.payload.get("codex_agent_type") or "default")
                ended_ns = event.created_ns if event.event_name == "SubagentStop" else None
                connection.execute(
                    """
                    INSERT INTO runtime_agents(
                        runtime_agent_id, root_session_id, root_turn_key, turn_id,
                        agent_type, started_ns, ended_ns, updated_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(runtime_agent_id) DO UPDATE SET
                        root_session_id = excluded.root_session_id,
                        root_turn_key = excluded.root_turn_key,
                        turn_id = excluded.turn_id,
                        agent_type = excluded.agent_type,
                        started_ns = MIN(runtime_agents.started_ns, excluded.started_ns),
                        ended_ns = COALESCE(excluded.ended_ns, runtime_agents.ended_ns),
                        updated_ns = excluded.updated_ns
                    """,
                    (
                        runtime_agent_id,
                        event.session_id,
                        root_turn_key,
                        event.turn_id,
                        agent_type,
                        event.created_ns,
                        ended_ns,
                        now,
                    ),
                )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox_events(
                    event_id, turn_key, event_name, created_ns, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    root_turn_key,
                    event.event_name,
                    event.created_ns,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )
            return cursor.rowcount == 1

    def claim(self, *, now_ns: int | None = None, require_usage: bool = False) -> PendingTurn | None:
        now = now_ns or time.time_ns()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM turn_uploads
                WHERE terminal_event IS NOT NULL
                  AND (
                    (status IN ('pending', 'failed') AND next_attempt_ns <= ?)
                    OR (status = 'uploading' AND lease_until_ns <= ?)
                )
                ORDER BY updated_ns ASC
                LIMIT 100
                """,
                (now, now),
            ).fetchall()
            selected: tuple[
                sqlite3.Row,
                list[sqlite3.Row],
                list[sqlite3.Row],
                TokenUsage | None,
                str | None,
                dict[str, tuple[TokenUsage | None, str | None]],
            ] | None = None
            for row in rows:
                event_rows = connection.execute(
                    """
                    SELECT event_id, event_name, created_ns, payload_json
                    FROM outbox_events
                    WHERE turn_key = ?
                    ORDER BY created_ns ASC, event_id ASC
                    """,
                    (row["turn_key"],),
                ).fetchall()
                agent_rows = connection.execute(
                    """
                    SELECT * FROM runtime_agents
                    WHERE root_turn_key = ?
                    ORDER BY started_ns, runtime_agent_id
                    """,
                    (row["turn_key"],),
                ).fetchall()
                usage, source, runtime_usage, usage_complete = self._usage_for_turn(
                    connection, row, event_rows, agent_rows
                )
                if require_usage and (usage is None or not usage_complete):
                    continue
                selected = row, event_rows, agent_rows, usage, source, runtime_usage
                break
            if selected is None:
                return None
            row, event_rows, agent_rows, usage, source, runtime_usage = selected
            conversation_ids = [row["session_id"]] + [
                item["runtime_agent_id"] for item in agent_rows
            ]
            placeholders = ",".join("?" for _ in conversation_ids)
            tool_rows = connection.execute(
                f"""
                SELECT * FROM tool_observations
                WHERE conversation_id IN ({placeholders})
                ORDER BY observed_ns, event_id
                """,
                conversation_ids,
            ).fetchall()
            updated = connection.execute(
                """
                UPDATE turn_uploads
                SET status = 'uploading', lease_until_ns = ?, updated_ns = ?
                WHERE turn_key = ?
                  AND (
                    (status IN ('pending', 'failed') AND next_attempt_ns <= ?)
                    OR (status = 'uploading' AND lease_until_ns <= ?)
                  )
                """,
                (now + LEASE_NS, now, row["turn_key"], now, now),
            )
            if updated.rowcount != 1:
                return None
        events = tuple(
            StoredEvent(
                event_id=item["event_id"],
                event_name=item["event_name"],
                created_ns=item["created_ns"],
                payload=json.loads(item["payload_json"]),
            )
            for item in event_rows
        )
        runtime_agents = tuple(
            RuntimeAgent(
                agent_id=item["runtime_agent_id"],
                agent_type=item["agent_type"],
                turn_id=item["turn_id"],
                started_ns=item["started_ns"],
                ended_ns=item["ended_ns"],
                token_usage=runtime_usage[item["runtime_agent_id"]][0],
                token_usage_source=runtime_usage[item["runtime_agent_id"]][1],
            )
            for item in agent_rows
        )
        tool_observations = tuple(
            ToolObservation(
                conversation_id=item["conversation_id"],
                call_id=item["call_id"],
                tool_name=item["tool_name"],
                observed_ns=item["observed_ns"],
                duration_ms=float(item["duration_ms"]),
                success=bool(item["success"]),
            )
            for item in tool_rows
        )
        return PendingTurn(
            turn_key=row["turn_key"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            agent_id=row["agent_id"],
            agent_root=row["agent_root"],
            terminal_event=row["terminal_event"],
            attempts=row["attempts"],
            events=events,
            token_usage=usage,
            token_usage_source=source,
            runtime_agents=runtime_agents,
            tool_observations=tool_observations,
        )

    @staticmethod
    def _usage_for_turn(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        event_rows: list[sqlite3.Row],
        agent_rows: list[sqlite3.Row],
    ) -> tuple[
        TokenUsage | None,
        str | None,
        dict[str, tuple[TokenUsage | None, str | None]],
        bool,
    ]:
        if not event_rows:
            return None, None, {}, False
        start_ns = min(item["created_ns"] for item in event_rows)
        end_ns = max(item["created_ns"] for item in event_rows)
        root_usage, root_source = _usage_for_conversation(
            connection,
            conversation_id=row["session_id"],
            turn_id=row["turn_id"],
            start_ns=start_ns,
            end_ns=end_ns,
        )
        runtime_usage: dict[str, tuple[TokenUsage | None, str | None]] = {}
        sources = {root_source} if root_source else set()
        total = root_usage
        complete = root_usage is not None
        for agent in agent_rows:
            agent_usage, agent_source = _usage_for_conversation(
                connection,
                conversation_id=agent["runtime_agent_id"],
                turn_id=agent["turn_id"],
                start_ns=agent["started_ns"],
                end_ns=agent["ended_ns"] or end_ns,
            )
            runtime_usage[agent["runtime_agent_id"]] = (agent_usage, agent_source)
            if agent_source:
                sources.add(agent_source)
            if agent_usage is None:
                if agent["ended_ns"] is not None:
                    complete = False
                continue
            total = agent_usage if total is None else total + agent_usage
        source = None
        if len(sources) == 1:
            source = next(iter(sources))
        elif sources:
            source = "mixed"
        return total, source, runtime_usage, complete

    def mark_uploaded(self, turn_key: str, trace_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE turn_uploads
                SET status = 'uploaded', trace_id = ?, last_error = NULL,
                    lease_until_ns = 0, updated_ns = ?
                WHERE turn_key = ?
                """,
                (trace_id, time.time_ns(), turn_key),
            )

    def mark_failed(self, turn_key: str, error: str, *, retry_after_seconds: float) -> None:
        now = time.time_ns()
        safe_error = error.replace("\n", " ")[:1000]
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE turn_uploads
                SET status = 'failed', attempts = attempts + 1,
                    next_attempt_ns = ?, lease_until_ns = 0,
                    last_error = ?, updated_ns = ?
                WHERE turn_key = ?
                """,
                (now + int(retry_after_seconds * 1_000_000_000), safe_error, now, turn_key),
            )

    def requeue_failed(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE turn_uploads
                SET status = 'pending', next_attempt_ns = 0, lease_until_ns = 0, updated_ns = ?
                WHERE status IN ('failed', 'uploading')
                """,
                (time.time_ns(),),
            )
            return cursor.rowcount

    def stats(self) -> dict[str, int]:
        result = {
            "pending": 0,
            "uploading": 0,
            "failed": 0,
            "uploaded": 0,
            "open": 0,
            "token_events": 0,
        }
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, terminal_event, COUNT(*) AS count FROM turn_uploads GROUP BY status, terminal_event"
            ).fetchall()
            result["token_events"] = connection.execute(
                "SELECT COUNT(*) FROM token_usage_events"
            ).fetchone()[0]
        for row in rows:
            if row["terminal_event"] is None:
                result["open"] += row["count"]
            else:
                result[row["status"]] = result.get(row["status"], 0) + row["count"]
        return result

    def uploaded_trace_id(self, turn_key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_id FROM turn_uploads WHERE turn_key = ? AND status = 'uploaded'",
                (turn_key,),
            ).fetchone()
        return row["trace_id"] if row else None

    def iter_failures(self, limit: int = 10) -> Iterator[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_key, last_error FROM turn_uploads
                WHERE status = 'failed' ORDER BY updated_ns DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        for row in rows:
            yield row["turn_key"], row["last_error"] or "unknown error"


def _usage_from_row(row: sqlite3.Row) -> TokenUsage:
    return TokenUsage(
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cached_input_tokens=row["cached_input_tokens"],
        cache_write_input_tokens=row["cache_write_input_tokens"],
        reasoning_output_tokens=row["reasoning_output_tokens"],
        total_tokens=row["total_tokens"],
    )


def _usage_for_conversation(
    connection: sqlite3.Connection,
    *,
    conversation_id: str,
    turn_id: str,
    start_ns: int,
    end_ns: int,
) -> tuple[TokenUsage | None, str | None]:
    otel_rows = connection.execute(
        """
        SELECT * FROM token_usage_events
        WHERE session_id = ? AND source = 'otel'
          AND observed_ns BETWEEN ? AND ?
        ORDER BY observed_ns, event_id
        """,
        (conversation_id, start_ns, end_ns),
    ).fetchall()
    if otel_rows:
        usage = TokenUsage(0, 0)
        for item in otel_rows:
            usage += _usage_from_row(item)
        return usage, "otel"
    transcript_row = connection.execute(
        """
        SELECT * FROM token_usage_events
        WHERE session_id = ? AND turn_id = ? AND source = 'transcript'
        ORDER BY observed_ns DESC LIMIT 1
        """,
        (conversation_id, turn_id),
    ).fetchone()
    if transcript_row is not None:
        return _usage_from_row(transcript_row), "transcript"
    return None, None
