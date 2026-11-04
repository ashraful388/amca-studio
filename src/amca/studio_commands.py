"""AMCA reusable slash-commands registry (facilities panel).

Commands are named, reusable instruction snippets the operator invokes from
the composer. Invoking a command NEVER executes anything blind: the UI
inserts the command text into the composer for review first (same path as
Skills onUse), so the existing approval boundary is preserved. Scope is
either one project or global (all projects).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .studio_memory import _clean_text, _now


class StudioCommandStore:
    """Manages reusable command definitions in SQLite."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS studio_commands (
                    command_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    instructions TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'global',
                    project_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_studio_commands_scope
                   ON studio_commands(scope, project_id, enabled)"""
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
            "command_id": row["command_id"],
            "name": row["name"],
            "description": row["description"],
            "instructions": row["instructions"],
            "scope": row["scope"],
            "project_id": row["project_id"],
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_command(
        self,
        *,
        name: str,
        instructions: str,
        description: str = "",
        scope: str = "global",
        project_id: str | None = None,
        enabled: bool = True,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        """Saves or updates a reusable command. Names are slash-safe slugs."""
        name = _clean_text(name, "name", 64, required=True).lstrip("/").lower()
        if not name:
            raise ValueError("Command name must not be empty")
        if not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError("Command name may only contain letters, numbers, dash and underscore")
        instructions = _clean_text(instructions, "instructions", 8000, required=True)
        lowered = instructions.casefold()
        forbidden_tokens = ("sk-proj-", "ghp_", "xoxb-", "xapp-", "aws_secret", "-----begin private key-----")
        if any(token in lowered for token in forbidden_tokens):
            raise ValueError("Command instructions must not contain raw authentication tokens, private keys, or credentials")
        description = _clean_text(description, "description", 500)
        scope = _clean_text(scope, "scope", 16) or "global"
        if scope not in ("global", "project"):
            raise ValueError("Command scope must be 'global' or 'project'")
        if scope == "project":
            project_id = _clean_text(project_id, "project_id", 128, required=True)
        else:
            project_id = None

        now = _now()
        target_id = command_id or f"cmd-{uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO studio_commands (
                    command_id, name, description, instructions, scope, project_id, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(command_id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    instructions=excluded.instructions,
                    scope=excluded.scope,
                    project_id=excluded.project_id,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at""",
                (target_id, name, description, instructions, scope, project_id, int(enabled), now, now),
            )
            row = connection.execute(
                "SELECT * FROM studio_commands WHERE command_id = ?", (target_id,)
            ).fetchone()
            return self._record(row)

    def list_commands(self, *, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Lists enabled + disabled commands: globals plus this project's own."""
        with self._connect() as connection:
            if project_id:
                rows = connection.execute(
                    """SELECT * FROM studio_commands
                       WHERE scope = 'global' OR (scope = 'project' AND project_id = ?)
                       ORDER BY name ASC LIMIT ?""",
                    (project_id, int(limit)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM studio_commands ORDER BY name ASC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [self._record(row) for row in rows]

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM studio_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            return self._record(row) if row else None

    def delete_command(self, command_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM studio_commands WHERE command_id = ?", (command_id,)
            )
            return cursor.rowcount > 0

    def set_enabled(self, command_id: str, *, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE studio_commands SET enabled = ?, updated_at = ? WHERE command_id = ?",
                (int(bool(enabled)), _now(), command_id),
            )
            row = connection.execute(
                "SELECT * FROM studio_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            return self._record(row) if row else None
