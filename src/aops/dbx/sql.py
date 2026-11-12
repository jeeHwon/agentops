"""SQL on a SQL warehouse through the Statement Execution API, with named parameters."""

from __future__ import annotations

import json
import re
import time
from typing import Any

VARIANT_PREFIX = "{", "["
PARAM = re.compile(r"(?<!:):([A-Za-z_]\w*)")  # a named parameter marker, not a :: cast
# Delta concurrent-write conflicts: another transaction changed the same rows first.
CONFLICTS = ("DELTA_CONCURRENT_APPEND", "DELTA_CONCURRENT_DELETE", "DELTA_CONCURRENT_TRANSACTION",
             "ConcurrentAppendException", "ConcurrentDeleteReadException", "ConcurrentTransactionException")


class SqlError(RuntimeError):
    pass


class Sql:
    def __init__(self, workspace: Any, warehouse_id: str, schema: str):
        self.w = workspace
        self.warehouse_id = warehouse_id
        self.schema = schema  # <catalog>.<schema>

    def table(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        from databricks.sdk.service.sql import Disposition, Format, StatementParameterListItem, StatementState

        items = [StatementParameterListItem(name=k, value=_text(v)) for k, v in (params or {}).items()]
        resp = self.w.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id, statement=statement, parameters=items or None,
            wait_timeout="50s", disposition=Disposition.INLINE, format=Format.JSON_ARRAY,
        )
        while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(1)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status.state != StatementState.SUCCEEDED:
            error = resp.status.error.message if resp.status.error else resp.status.state
            raise SqlError(f"{error}\n{statement[:200]}")
        if not resp.manifest or not resp.manifest.schema or not resp.manifest.schema.columns:
            return []
        names = [c.name for c in resp.manifest.schema.columns]
        rows = list(resp.result.data_array or []) if resp.result else []
        chunk = resp.result.next_chunk_index if resp.result else None
        while chunk is not None:
            part = self.w.statement_execution.get_statement_result_chunk_n(resp.statement_id, chunk)
            rows.extend(part.data_array or [])
            chunk = part.next_chunk_index
        return [dict(zip(names, row)) for row in rows]

    def update(self, statement: str, params: dict[str, Any] | None = None) -> int:
        """UPDATE/MERGE; the number of rows it changed (used for compare-and-set state changes).

        Two writers may make the same state change at once (the server's follower thread and a status
        read). Delta then fails the later one with a concurrent-write conflict instead of matching zero
        rows; that means the other writer moved the row, so it counts as 0 and the caller reads again.
        """
        try:
            rows = self.run(statement, params)
        except SqlError as error:
            if any(code in str(error) for code in CONFLICTS):
                return 0
            raise
        return int(rows[0].get("num_affected_rows") or 0) if rows else 0

    def one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.run(statement, params)
        return rows[0] if rows else None

    def atomic(self, statements: list[tuple[str, dict[str, Any]]]) -> None:
        """Run the statements as one transaction (`BEGIN ATOMIC … END`): all of them commit, or none.

        Needs Unity Catalog managed tables with catalog-managed commits (the registry DDL turns it on).
        Each statement keeps its own parameter names; they are prefixed here so they cannot collide.
        """
        if not statements:
            return
        body, params = atomic_block(statements)
        self.run(body, params)


def atomic_block(statements: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    parts, merged = [], {}
    for i, (statement, values) in enumerate(statements):
        names = set(values or {})

        def rename(match: re.Match, i: int = i, names: set[str] = names) -> str:
            return f":s{i}_{match.group(1)}" if match.group(1) in names else match.group(0)

        parts.append("  " + PARAM.sub(rename, statement.strip().rstrip(";")) + ";")
        merged.update({f"s{i}_{k}": v for k, v in (values or {}).items()})
    return "BEGIN ATOMIC\n" + "\n".join(parts) + "\nEND", merged


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def loads(value: Any) -> Any:
    """VARIANT and JSON columns come back as JSON text."""
    if isinstance(value, str) and value.startswith(VARIANT_PREFIX):
        return json.loads(value)
    return value
