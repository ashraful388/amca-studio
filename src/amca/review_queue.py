"""Durable Review Queue for background outcomes (B1.4A).

A small SQLite-backed store of ReviewItems so Studio users can discover what
finished, what failed, and what needs attention from background automations
without digging through run logs.

Guarantees:
- one terminal source event -> at most one ReviewItem (UNIQUE source_key),
  stable across restarts and concurrent terminal callbacks;
- user state (unread/read/dismissed) persists independently of content;
- bounded reads (newest-first, limit); no transcripts, credentials, or
  environment data are ever stored - callers pass short human-readable
  title/summary text only.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


ITEM_STATUSES = frozenset({"unread", "read", "dismissed"})
_MAX_TEXT = 400


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value)[:limit]


class ReviewQueueStore:
    """SQLite-backed durable ReviewItem store."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    project_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL DEFAULT 'info',
                    status TEXT NOT NULL DEFAULT 'unread',
                    run_id TEXT,
                    occurrence_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_items_project "
                "ON review_items(project_id, created_at)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "source_key": row["source_key"],
            "source_type": row["source_type"],
            "project_id": row["project_id"],
            "title": row["title"],
            "summary": row["summary"],
            "severity": row["severity"],
            "status": row["status"],
            "run_id": row["run_id"],
            "occurrence_id": row["occurrence_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record(
        self,
        *,
        source_key: str,
        source_type: str,
        project_id: str,
        title: str,
        summary: str = "",
        severity: str = "info",
        run_id: str | None = None,
        occurrence_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert a ReviewItem; returns None when the source event already
        produced one (idempotent per terminal source event)."""

        item_id = uuid4().hex
        now = _now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO review_items
                       (id, source_key, source_type, project_id, title, summary,
                        severity, status, run_id, occurrence_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'unread', ?, ?, ?, ?)""",
                    (
                        item_id,
                        source_key,
                        _clip(source_type, 80),
                        _clip(project_id, 100),
                        _clip(title, 200),
                        _clip(summary, 400),
                        _clip(severity, 20),
                        run_id,
                        occurrence_id,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return self.get_item(item_id)

    def list_items(
        self,
        *,
        limit: int = 50,
        project_id: str | None = None,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM review_items"
        conditions: list[str] = []
        parameters: list[Any] = []
        if project_id:
            conditions.append("project_id = ?")
            parameters.append(project_id)
        if unread_only:
            conditions.append("status = 'unread'")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._record(row) for row in rows]

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM review_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._record(row) if row else None

    def mark_read(self, item_id: str) -> bool:
        return self._transition(item_id, "read")

    def dismiss(self, item_id: str) -> bool:
        return self._transition(item_id, "dismissed")

    def _transition(self, item_id: str, status: str) -> bool:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE review_items SET status = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('unread', 'read')",
                (status, now, item_id),
            )
        return cursor.rowcount > 0

    def unread_count(self, project_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM review_items WHERE status = 'unread'"
        parameters: list[Any] = []
        if project_id:
            query += " AND project_id = ?"
            parameters.append(project_id)
        with self._connect() as connection:
            (count,) = connection.execute(query, parameters).fetchone()
        return int(count)
