"""Durable record of the user's explicit intelligence-gate choices.

Why this exists
---------------
The gate controller documents a security property: *"initial state always OFF
… nothing auto-enables"*.  That property is about the **system** deciding to
turn a feature on.  It is not a statement about the user's own decision, and
losing that decision on every restart is the defect the v1.1.8 plan names under
"Auto settings persistence".

So this store persists the user's explicit enable/disable choices and nothing
else:

* it never enables a gate the user did not enable - a fresh install has no row
  and therefore still starts from the shipped default (every gate OFF);
* a restore is the user's decision being honoured, not the system auto-enabling;
* readiness and dependency validation still run, and a capability that cannot
  operate keeps reporting its reason through the existing readiness surface
  rather than being silently disabled.

The store is inert on construction: it creates nothing and writes nothing until
a choice is recorded, so opening a database stays side-effect free.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

TABLE = "intelligence_gate_state"

#: The gates a user can choose.  Kept here so an unknown name in the database is
#: ignored rather than resurrected into the registry.
CHOOSABLE_GATES = ("adaptive_planner", "hybrid_execution", "nano_agents", "model_router")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class GateStateStore:
    """The user's durable gate choices, in the desktop SQLite file."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self._lock = threading.RLock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection that is always closed.

        ``sqlite3.Connection`` as a context manager only commits or rolls back;
        it does not close.  Every call site goes through this helper so the file
        handle is released and a temp database can be removed.
        """
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def _table_exists(self, connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
            ).fetchone()
            is not None
        )

    def load(self) -> dict[str, bool]:
        """The recorded choices.  An empty mapping means "never chosen"."""
        if not self.database.exists():
            return {}
        with self._lock, self._connect() as connection:
            if not self._table_exists(connection):
                return {}
            try:
                rows = connection.execute(
                    f"SELECT gate, enabled FROM {TABLE}"
                ).fetchall()
            except sqlite3.Error:
                # An unreadable record must not decide anything: start from the
                # shipped default rather than guessing what the user wanted.
                return {}
        return {
            str(row["gate"]): bool(row["enabled"])
            for row in rows
            if str(row["gate"]) in CHOOSABLE_GATES
        }

    def save(self, gates: Mapping[str, Any]) -> None:
        """Record the current choice for every choosable gate."""
        timestamp = _now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                        gate TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                for gate in CHOOSABLE_GATES:
                    if gate not in gates:
                        continue
                    connection.execute(
                        f"""INSERT INTO {TABLE} (gate, enabled, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(gate) DO UPDATE SET
                            enabled=excluded.enabled, updated_at=excluded.updated_at""",
                        (gate, 1 if gates[gate] else 0, timestamp),
                    )
                connection.execute("COMMIT")
            except BaseException:
                raise
