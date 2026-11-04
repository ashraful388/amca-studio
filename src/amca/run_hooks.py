"""AMCA run-event hooks engine (facilities panel).

Hooks fire on a FIXED event enum drawn from the existing runtime surfaces
(EventBus in events.py, run lifecycle in agent_runtime.py). Actions reuse
existing sinks only — there is no new execution primitive:
- notify: records a hook firing the UI can list (no side effects).
- append-memory: writes into the existing studio memory store.
- run-command: files a governed command proposal (/commands/request path),
  which still needs human approval before anything runs.

Dispatch subscribes read-only; a hook action can never bypass approvals.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

from .studio_memory import _clean_text, _now

HOOK_EVENTS = (
    "run_started",
    "run_completed",
    "run_failed",
    "approval_pending",
    "artifact_created",
)

HOOK_ACTIONS = (
    "notify",
    "append-memory",
    "run-command",
)


class RunHookStore:
    """Manages run-event hook definitions in SQLite."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS run_hooks (
                    hook_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    event TEXT NOT NULL,
                    match_json TEXT NOT NULL DEFAULT '{}',
                    action TEXT NOT NULL,
                    action_arg TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    fire_count INTEGER NOT NULL DEFAULT 0,
                    last_fired_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS run_hook_firings (
                    firing_id TEXT PRIMARY KEY,
                    hook_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
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
            "hook_id": row["hook_id"],
            "name": row["name"],
            "event": row["event"],
            "match": json.loads(row["match_json"] or "{}"),
            "action": row["action"],
            "action_arg": row["action_arg"],
            "enabled": bool(row["enabled"]),
            "fire_count": int(row["fire_count"]),
            "last_fired_at": row["last_fired_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_hook(
        self,
        *,
        name: str,
        event: str,
        action: str,
        match: dict[str, Any] | None = None,
        action_arg: str = "",
        enabled: bool = True,
        hook_id: str | None = None,
    ) -> dict[str, Any]:
        """Saves or updates a hook. Event/action come from fixed enums."""
        name = _clean_text(name, "name", 128, required=True)
        event = _clean_text(event, "event", 32, required=True)
        if event not in HOOK_EVENTS:
            raise ValueError(f"Hook event must be one of: {', '.join(HOOK_EVENTS)}")
        action = _clean_text(action, "action", 32, required=True)
        if action not in HOOK_ACTIONS:
            raise ValueError(f"Hook action must be one of: {', '.join(HOOK_ACTIONS)}")
        action_arg = _clean_text(action_arg, "action_arg", 2000)
        match = dict(match or {})

        now = _now()
        target_id = hook_id or f"hook-{uuid4().hex[:12]}"
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO run_hooks (
                    hook_id, name, event, match_json, action, action_arg, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hook_id) DO UPDATE SET
                    name=excluded.name,
                    event=excluded.event,
                    match_json=excluded.match_json,
                    action=excluded.action,
                    action_arg=excluded.action_arg,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at""",
                (target_id, name, event, json.dumps(match), action, action_arg, int(enabled), now, now),
            )
            row = connection.execute(
                "SELECT * FROM run_hooks WHERE hook_id = ?", (target_id,)
            ).fetchone()
            return self._record(row)

    def list_hooks(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_hooks ORDER BY name ASC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_hook(self, hook_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM run_hooks WHERE hook_id = ?", (hook_id,)
            ).fetchone()
            return self._record(row) if row else None

    def delete_hook(self, hook_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("DELETE FROM run_hook_firings WHERE hook_id = ?", (hook_id,))
            cursor = connection.execute("DELETE FROM run_hooks WHERE hook_id = ?", (hook_id,))
            return cursor.rowcount > 0

    def set_enabled(self, hook_id: str, *, enabled: bool) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE run_hooks SET enabled = ?, updated_at = ? WHERE hook_id = ?",
                (int(bool(enabled)), _now(), hook_id),
            )
            row = connection.execute(
                "SELECT * FROM run_hooks WHERE hook_id = ?", (hook_id,)
            ).fetchone()
            return self._record(row) if row else None

    def list_firings(self, *, hook_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if hook_id:
                rows = connection.execute(
                    """SELECT * FROM run_hook_firings WHERE hook_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (hook_id, int(limit)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM run_hook_firings ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [
            {
                "firing_id": r["firing_id"],
                "hook_id": r["hook_id"],
                "event": r["event"],
                "detail": json.loads(r["detail_json"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def dispatch(
        self,
        event: str,
        detail: Mapping[str, Any] | None = None,
        *,
        on_action: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> int:
        """Fires enabled hooks matching event + match-subset. Returns fired count.

        Match semantics: every key in the hook's match JSON must equal the
        same key in the event detail (subset match). on_action lets the
        service layer execute sink actions (memory write, command proposal)
        without this store importing the service.
        """
        if event not in HOOK_EVENTS:
            raise ValueError(f"Unknown hook event: {event}")
        if detail is not None and not isinstance(detail, Mapping):
            raise ValueError("Hook event detail must be a JSON object")
        detail = dict(detail or {})
        fired = 0
        for hook in self.list_hooks():
            if not hook["enabled"] or hook["event"] != event:
                continue
            match = hook["match"] or {}
            if any(detail.get(k) != v for k, v in match.items()):
                continue
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO run_hook_firings (firing_id, hook_id, event, detail_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (f"fire-{uuid4().hex[:12]}", hook["hook_id"], event, json.dumps(detail), _now()),
                )
                connection.execute(
                    "UPDATE run_hooks SET fire_count = fire_count + 1, last_fired_at = ? WHERE hook_id = ?",
                    (_now(), hook["hook_id"]),
                )
            if on_action is not None:
                on_action(hook, detail)
            fired += 1
        return fired
