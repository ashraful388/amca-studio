"""Desktop preferences that must survive a restart.

The v1.1.8 plan requires a real **Default Auto** control: "Its selected state
must be visible, persist as intended, affect execution immediately, and be
covered by interaction and execution tests."

Before this, the only durable selection policy was per-conversation browser
localStorage, and the Model Hub card that announced "Default task selection:
Auto Intelligence" was a static element with no effect.  This store holds the
global default that a *new* conversation starts from.

It is inert on construction: nothing is created or written until a preference is
set, so opening a database stays side-effect free.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

TABLE = "desktop_preferences"

#: The selection policies a user can choose as the default.  These are the
#: existing wire values, not a new vocabulary.
SELECTION_POLICIES = ("auto_intelligence", "manual_model_set")

DEFAULT_SELECTION_POLICY = "auto_intelligence"

#: Interface text sizes.  The plan requires the user to be able to adjust the
#: interface typography; three named steps keep the choice bounded and
#: testable rather than a free-form number that could break a layout.
UI_FONT_SCALES = ("compact", "standard", "large")
DEFAULT_UI_FONT_SCALE = "standard"

#: How cloud excerpt transmissions are authorized.  ``exact_scope`` keeps the
#: per-turn human approve/deny card (the certified default).  ``automatic``
#: records the machine owner's standing policy: every transmission request is
#: decided approved at creation with the distinct ``owner-auto-policy``
#: provenance label, so the exact-scope digest / single-use / expiry chain
#: stays enforced but no human click is required.  This is the supported,
#: persistent replacement for the AMCA_DESKTOP_AUTOMATIC_CLOUD environment
#: override on owner-operated machines.
CLOUD_APPROVAL_MODES = ("exact_scope", "automatic")
DEFAULT_CLOUD_APPROVAL_MODE = "exact_scope"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class DesktopPreferencesStore:
    """Small durable key/value store for desktop-wide preferences."""

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

    def _read(self, key: str) -> str | None:
        if not self.database.exists():
            return None
        with self._lock, self._connect() as connection:
            if not self._table_exists(connection):
                return None
            try:
                row = connection.execute(
                    f"SELECT value FROM {TABLE} WHERE key=?", (key,)
                ).fetchone()
            except sqlite3.Error:
                return None
        return None if row is None else str(row["value"])

    def selection_policy(self) -> str:
        """The default selection policy a new conversation starts from."""
        value = self._read("selection_policy")
        return value if value in SELECTION_POLICIES else DEFAULT_SELECTION_POLICY

    def set_selection_policy(self, policy: str) -> str:
        resolved = str(policy or "").strip()
        if resolved not in SELECTION_POLICIES:
            raise ValueError(
                f"selection_policy must be one of {list(SELECTION_POLICIES)}"
            )
        self._write("selection_policy", resolved)
        return resolved

    def _write(self, key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    f"""INSERT INTO {TABLE} (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value, updated_at=excluded.updated_at""",
                    (key, value, _now()),
                )
                connection.execute("COMMIT")
            except BaseException:
                raise

    def ui_font_scale(self) -> str:
        """The interface text size the user chose."""
        value = self._read("ui_font_scale")
        return value if value in UI_FONT_SCALES else DEFAULT_UI_FONT_SCALE

    def set_ui_font_scale(self, scale: str) -> str:
        resolved = str(scale or "").strip()
        if resolved not in UI_FONT_SCALES:
            raise ValueError(f"ui_font_scale must be one of {list(UI_FONT_SCALES)}")
        self._write("ui_font_scale", resolved)
        return resolved

    def cloud_approval_mode(self) -> str:
        """How cloud excerpt transmissions are authorized on this machine."""
        value = self._read("cloud_approval_mode")
        return value if value in CLOUD_APPROVAL_MODES else DEFAULT_CLOUD_APPROVAL_MODE

    def set_cloud_approval_mode(self, mode: str) -> str:
        resolved = str(mode or "").strip()
        if resolved not in CLOUD_APPROVAL_MODES:
            raise ValueError(
                f"cloud_approval_mode must be one of {list(CLOUD_APPROVAL_MODES)}"
            )
        self._write("cloud_approval_mode", resolved)
        return resolved

    def to_dict(self) -> dict[str, Any]:
        policy = self.selection_policy()
        mode = self.cloud_approval_mode()
        return {
            "selection_policy": policy,
            "default_auto": policy == "auto_intelligence",
            "ui_font_scale": self.ui_font_scale(),
            "cloud_approval_mode": mode,
            "cloud_approval_automatic": mode == "automatic",
        }

    def update(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Apply a preference patch, ignoring unknown keys."""
        if "ui_font_scale" in values:
            self.set_ui_font_scale(str(values["ui_font_scale"]))
        if "cloud_approval_mode" in values:
            self.set_cloud_approval_mode(str(values["cloud_approval_mode"]))
        elif "cloud_approval_automatic" in values:
            # The UI toggle speaks in "automatic: on/off"; the wire keeps the
            # mode vocabulary.
            enabled = values["cloud_approval_automatic"]
            if not isinstance(enabled, bool):
                raise ValueError("cloud_approval_automatic must be a boolean")
            self.set_cloud_approval_mode("automatic" if enabled else "exact_scope")
        if "selection_policy" in values:
            self.set_selection_policy(str(values["selection_policy"]))
        elif "default_auto" in values:
            # The UI toggle speaks in "Default Auto: on/off"; the wire keeps the
            # existing policy vocabulary.
            enabled = values["default_auto"]
            if not isinstance(enabled, bool):
                raise ValueError("default_auto must be a boolean")
            self.set_selection_policy(
                "auto_intelligence" if enabled else "manual_model_set"
            )
        return self.to_dict()
