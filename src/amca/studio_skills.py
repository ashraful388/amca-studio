"""AMCA server-side skills registry (facilities panel).

Device skills (localStorage `amca.skills`) stay as the offline fallback and
are importable one-shot via the UI; this store is the durable, inspectable
server copy with the same shape (name + instructions). "Run in chat" inserts
the skill text into the composer for review — skills never execute blind.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .studio_memory import _clean_text, _now


class StudioSkillStore:
    """Manages server-side skill definitions in SQLite."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS studio_skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "skill_id": row["skill_id"],
            "name": row["name"],
            "instructions": row["instructions"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_skill(
        self,
        *,
        name: str,
        instructions: str,
        enabled: bool = True,
        skill_id: str | None = None,
    ) -> dict[str, Any]:
        """Saves or updates a server-side skill."""
        name = _clean_text(name, "name", 128, required=True)
        instructions = _clean_text(instructions, "instructions", 16000, required=True)

        lowered = instructions.casefold()
        forbidden_tokens = ("sk-proj-", "ghp_", "xoxb-", "xapp-", "aws_secret", "-----begin private key-----")
        if any(token in lowered for token in forbidden_tokens):
            raise ValueError("Skill instructions must not contain raw authentication tokens, private keys, or credentials")

        now = _now()
        target_id = skill_id or f"skl-{uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO studio_skills (
                    skill_id, name, instructions, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    name=excluded.name,
                    instructions=excluded.instructions,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at""",
                (target_id, name, instructions, int(enabled), now, now),
            )
            row = connection.execute(
                "SELECT * FROM studio_skills WHERE skill_id = ?", (target_id,)
            ).fetchone()
            return self._record(row)

    def list_skills(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM studio_skills ORDER BY name ASC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM studio_skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
            return self._record(row) if row else None

    def delete_skill(self, skill_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM studio_skills WHERE skill_id = ?", (skill_id,)
            )
            return cursor.rowcount > 0

    def set_enabled(self, skill_id: str, *, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE studio_skills SET enabled = ?, updated_at = ? WHERE skill_id = ?",
                (int(bool(enabled)), _now(), skill_id),
            )
            row = connection.execute(
                "SELECT * FROM studio_skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
            return self._record(row) if row else None
