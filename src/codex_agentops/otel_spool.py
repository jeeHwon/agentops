from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .otel_bridge import OtelSpan, _is_genai_span, _span_kind


MAX_SPOOL_BYTES = 250 * 1024 * 1024
ROOT_WAIT_WARNING_NS = 24 * 60 * 60 * 1_000_000_000


class OtelSpool:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("OTLP spool must not be a symbolic link.")
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spans (
                    trace_id TEXT NOT NULL, span_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (trace_id, span_id)
                );
                CREATE TABLE IF NOT EXISTS completed (
                    trace_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS seen (
                    trace_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS waiting (
                    trace_id TEXT PRIMARY KEY, first_received_ns INTEGER NOT NULL,
                    has_agent_root INTEGER NOT NULL DEFAULT 0
                );
            """)
            stale = (
                (trace_id, span_id)
                for trace_id, span_id, payload in connection.execute(
                    "SELECT trace_id, span_id, payload FROM spans"
                )
                if not _is_genai_span(OtelSpan(**json.loads(payload)))
            )
            connection.executemany(
                "DELETE FROM spans WHERE trace_id = ? AND span_id = ?", stale
            )
            received_ns = time.time_ns()
            for trace_id, payload in connection.execute("SELECT trace_id, payload FROM spans"):
                connection.execute(
                    "INSERT OR IGNORE INTO waiting (trace_id, first_received_ns) VALUES (?, ?)",
                    (trace_id, received_ns),
                )
                if _span_kind(OtelSpan(**json.loads(payload))) == "AGENT":
                    connection.execute(
                        "UPDATE waiting SET has_agent_root = 1 WHERE trace_id = ?", (trace_id,)
                    )
            connection.execute(
                "DELETE FROM waiting WHERE NOT EXISTS "
                "(SELECT 1 FROM spans WHERE spans.trace_id = waiting.trace_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def append(self, payload: bytes) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = sum(
                connection.execute(f"SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM {table}").fetchone()[0]
                for table in ("batches", "spans", "completed")
            )
            if pending + len(payload) > MAX_SPOOL_BYTES:
                raise OverflowError("OTLP spool is full")
            connection.execute("INSERT INTO batches (payload) VALUES (?)", (payload,))

    def batches(self) -> list[tuple[int, bytes]]:
        with self._connect() as connection:
            return connection.execute("SELECT id, payload FROM batches ORDER BY id").fetchall()

    def persist_batch(self, batch_id: int, spans: tuple[OtelSpan, ...]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for span in spans:
                if not _is_genai_span(span):
                    continue
                if span.attributes.get("agent.name") == "session-title":
                    continue
                if connection.execute(
                    "SELECT 1 FROM seen WHERE trace_id = ?", (span.trace_id,)
                ).fetchone():
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO spans VALUES (?, ?, ?)",
                    (span.trace_id, span.span_id, json.dumps(asdict(span), ensure_ascii=False)),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO waiting (trace_id, first_received_ns) VALUES (?, ?)",
                    (span.trace_id, time.time_ns()),
                )
                if _span_kind(span) == "AGENT":
                    connection.execute(
                        "UPDATE waiting SET has_agent_root = 1 WHERE trace_id = ?",
                        (span.trace_id,),
                    )
            connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))

    def pending_spans(self) -> tuple[OtelSpan, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM spans ORDER BY rowid").fetchall()
        return tuple(OtelSpan(**json.loads(row[0])) for row in rows)

    def save_groups(self, groups: tuple[tuple[OtelSpan, ...], ...]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for group in groups:
                trace_id = group[0].trace_id
                connection.execute(
                    "INSERT OR IGNORE INTO completed VALUES (?, ?)",
                    (trace_id, json.dumps([asdict(span) for span in group], ensure_ascii=False)),
                )
                for consumed_id in {span.trace_id for span in group}:
                    connection.execute("INSERT OR IGNORE INTO seen VALUES (?)", (consumed_id,))
                    connection.execute("DELETE FROM spans WHERE trace_id = ?", (consumed_id,))
                    connection.execute("DELETE FROM waiting WHERE trace_id = ?", (consumed_id,))

    def completed(self) -> list[tuple[str, tuple[OtelSpan, ...]]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT trace_id, payload FROM completed ORDER BY rowid").fetchall()
        return [(trace_id, tuple(OtelSpan(**item) for item in json.loads(payload)))
                for trace_id, payload in rows]

    def stats(self, *, now_ns: int | None = None) -> dict[str, int]:
        with self._connect() as connection:
            result = {name: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for name, table in (("pending_batches", "batches"),
                                          ("pending_spans", "spans"),
                                          ("retry_traces", "completed"))}
            result["waiting_for_root"] = connection.execute(
                "SELECT COUNT(*) FROM waiting WHERE has_agent_root = 0"
            ).fetchone()[0]
            cutoff = (now_ns if now_ns is not None else time.time_ns()) - ROOT_WAIT_WARNING_NS
            result["waiting_for_root_overdue"] = connection.execute(
                "SELECT COUNT(*) FROM waiting "
                "WHERE has_agent_root = 0 AND first_received_ns <= ?", (cutoff,)
            ).fetchone()[0]
            return result

    def mark_exported(self, trace_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM completed WHERE trace_id = ?", (trace_id,))
