"""Durable local automation definitions for the desktop service.

Definitions never execute providers by themselves. Dispatch remains an explicit
service action so the normal Model Hub, budget, privacy, and approval gates are
always applied.

Since B1.2A every scheduler firing is guarded by a durable per-occurrence claim
(automation_id + canonical scheduled slot) that commits before dispatch, so a
crash between claiming and running can never silently replay the same occurrence,
and scheduler bookkeeping updates targeted columns only, so it can never overwrite
a newer user edit (optimistic concurrency via the ``revision`` column).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from .conditions import validate_condition


AUTOMATION_TYPES = frozenset({"once", "recurring", "conditional", "monitor", "event_triggered"})
INTERRUPTED_CLAIM_ERROR = "interrupted: desktop application stopped before this occurrence completed"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any, name: str, maximum: int, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > maximum or any(ord(char) < 32 for char in result):
        raise ValueError(f"{name} must be {'1-' if required else '0-'}{maximum} printable characters")
    return result


class AutomationStore:
    """SQLite-backed automation definitions with no hidden scheduler side effects."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._initialize()
        # A claim left non-terminal by a previous process is an interrupted
        # execution, never a reason to replay it: freeze it as a durable,
        # visible failure instead.
        self._reconcile_interrupted_claims()

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

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        """Explicit BEGIN IMMEDIATE transaction for compare-and-set paths."""

        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_automations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, project_id TEXT NOT NULL,
                    goal TEXT NOT NULL, automation_type TEXT NOT NULL, schedule TEXT NOT NULL,
                    enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_run_at TEXT, last_run_id TEXT, metadata_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    condition_json TEXT, last_check_at TEXT
                )"""
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(desktop_automations)")
            }
            if "revision" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_automations ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
            if "condition_json" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_automations ADD COLUMN condition_json TEXT"
                )
            if "last_check_at" not in columns:
                connection.execute(
                    "ALTER TABLE desktop_automations ADD COLUMN last_check_at TEXT"
                )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_automation_occurrences (
                    id TEXT PRIMARY KEY, automation_id TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL, status TEXT NOT NULL,
                    claimed_at TEXT NOT NULL, run_id TEXT, completed_at TEXT, error TEXT,
                    check_json TEXT,
                    UNIQUE (automation_id, scheduled_for)
                )"""
            )
            occurrence_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(desktop_automation_occurrences)")
            }
            if "check_json" not in occurrence_columns:
                connection.execute(
                    "ALTER TABLE desktop_automation_occurrences ADD COLUMN check_json TEXT"
                )

    def _reconcile_interrupted_claims(self) -> None:
        """Freeze claims that died before dispatch (no run_id ever attached).

        Claims that already carry a run_id belong to the canonical RunRegistry
        lifecycle; reconcile_automation_occurrences() in service.py resolves
        those against the real RunJob outcome so a genuinely successful run is
        never rewritten into an interrupted failure here.
        """

        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automation_occurrences SET status = 'failed', error = ?, completed_at = ? "
                "WHERE status IN ('claimed', 'running') AND run_id IS NULL",
                (INTERRUPTED_CLAIM_ERROR, _now()),
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        condition_json = row["condition_json"] if "condition_json" in row.keys() else None
        last_check_at = row["last_check_at"] if "last_check_at" in row.keys() else None
        return {
            "id": row["id"], "name": row["name"], "project_id": row["project_id"],
            "goal": row["goal"], "automation_type": row["automation_type"],
            "schedule": row["schedule"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"], "last_run_id": row["last_run_id"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "revision": int(row["revision"]),
            "condition": json.loads(condition_json) if condition_json else None,
            "last_check_at": last_check_at,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM desktop_automations ORDER BY updated_at DESC").fetchall()
        return [self._record(row) for row in rows]

    def get(self, automation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM desktop_automations WHERE id = ?", (automation_id,)).fetchone()
        return self._record(row) if row else None

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        automation_id = _text(value.get("id") or uuid4().hex, "id", 100)
        kind = _text(value.get("automation_type", "once"), "automation_type", 32).lower()
        if kind not in AUTOMATION_TYPES:
            raise ValueError("automation_type is not supported")
        existing = self.get(automation_id)
        now = _now()
        created_at = existing["created_at"] if existing else now
        revision = (existing["revision"] if existing else 0) + 1
        enabled = bool(value.get("enabled", existing["enabled"] if existing else False))
        metadata = value.get("metadata", existing["metadata"] if existing else {})
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a JSON object")
        if "condition" in value:
            condition = value.get("condition")
        else:
            condition = existing.get("condition") if existing else None
        if kind == "conditional":
            # Save-time gate (B1.2B): new/updated conditional automations must
            # carry an explicit valid condition object or the save is rejected.
            validate_condition(condition)
        record = {
            "id": automation_id,
            "name": _text(value.get("name", existing["name"] if existing else ""), "name", 120),
            "project_id": _text(value.get("project_id", existing["project_id"] if existing else ""), "project_id", 100),
            "goal": _text(value.get("goal", existing["goal"] if existing else ""), "goal", 8000),
            "automation_type": kind,
            "schedule": _text(value.get("schedule", existing["schedule"] if existing else "manual"), "schedule", 240),
            "enabled": enabled, "created_at": created_at, "updated_at": now,
            "last_run_at": (
                value.get("last_run_at")
                if value.get("last_run_at")
                else existing["last_run_at"] if existing else None
            ),
            "last_run_id": (
                value.get("last_run_id")
                if value.get("last_run_id")
                else existing["last_run_id"] if existing else None
            ),
            "metadata_json": json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True),
            "revision": revision,
            "condition_json": (
                json.dumps(dict(condition), ensure_ascii=False, sort_keys=True)
                if isinstance(condition, Mapping)
                else None
            ),
            "last_check_at": (
                value.get("last_check_at")
                if value.get("last_check_at")
                else existing["last_check_at"] if existing else None
            ),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO desktop_automations
                (id,name,project_id,goal,automation_type,schedule,enabled,created_at,updated_at,last_run_at,last_run_id,metadata_json,revision,condition_json,last_check_at)
                VALUES (:id,:name,:project_id,:goal,:automation_type,:schedule,:enabled,:created_at,:updated_at,:last_run_at,:last_run_id,:metadata_json,:revision,:condition_json,:last_check_at)""",
                record,
            )
        return self.get(automation_id) or {}

    def set_enabled(self, automation_id: str, enabled: bool) -> dict[str, Any]:
        """Targeted scheduler-safe toggle: never rewrites user-owned fields."""

        if self.get(automation_id) is None:
            raise ValueError("automation not found")
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automations SET enabled = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (1 if enabled else 0, _now(), automation_id),
            )
        return self.get(automation_id) or {}

    def merge_metadata(self, automation_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        """Merge scheduler-owned metadata keys into the freshest record."""

        current = self.get(automation_id)
        if current is None:
            raise ValueError("automation not found")
        metadata = dict(current.get("metadata") or {})
        metadata.update(dict(updates))
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automations SET metadata_json = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), _now(), automation_id),
            )
        return self.get(automation_id) or {}

    def record_skip(self, automation_id: str, reason: str) -> dict[str, Any]:
        """Record an honest dispatch failure/skip on the automation metadata."""

        current = self.get(automation_id)
        if current is None:
            raise ValueError("automation not found")
        try:
            metadata = dict(json.loads(current.get("metadata_json") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        skips = list(metadata.get("recent_skips") or [])
        skips.append({"at": _now(), "reason": str(reason)[:200]})
        metadata["recent_skips"] = skips[-5:]
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automations SET metadata_json = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    _now(),
                    automation_id,
                ),
            )
        return self.get(automation_id) or {}

    def mark_run(self, automation_id: str, run_id: str, now: str | None = None) -> dict[str, Any]:
        stamp = now or _now()
        if self.get(automation_id) is None:
            raise ValueError("automation not found")
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automations SET last_run_at = ?, last_run_id = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (stamp, _text(run_id, "run_id", 100), stamp, automation_id),
            )
        return self.get(automation_id) or {}

    # ------------------------------------------------------------------
    # Durable occurrence claims (B1.2A)

    @staticmethod
    def _claim_record(row: sqlite3.Row) -> dict[str, Any]:
        check_json = row["check_json"] if "check_json" in row.keys() else None
        return {
            "id": row["id"],
            "automation_id": row["automation_id"],
            "scheduled_for": row["scheduled_for"],
            "status": row["status"],
            "claimed_at": row["claimed_at"],
            "run_id": row["run_id"],
            "completed_at": row["completed_at"],
            "error": row["error"],
            "check": json.loads(check_json) if check_json else None,
        }

    def claim_occurrence(
        self, automation_id: str, scheduled_for: str, now: str | None = None
    ) -> dict[str, Any] | None:
        """Atomically claim one scheduled occurrence for execution.

        Returns the durable claim, or None when the automation is gone or no
        longer enabled — verified on the freshest row inside the BEGIN
        IMMEDIATE write transaction — or when this occurrence was already
        claimed. A prior claim whose dispatch was explicitly refused while the
        process was still alive (nothing ran) is released and reclaimable;
        every other existing claim blocks replay of the occurrence.
        """

        stamp = now or _now()
        with self._immediate() as connection:
            row = connection.execute(
                "SELECT enabled FROM desktop_automations WHERE id = ?", (automation_id,)
            ).fetchone()
            if row is None or not row["enabled"]:
                return None
            existing = connection.execute(
                "SELECT id, status, error FROM desktop_automation_occurrences "
                "WHERE automation_id = ? AND scheduled_for = ?",
                (automation_id, scheduled_for),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "failed" and (existing["error"] or "").startswith(
                    "dispatch refused:"
                ):
                    # The refusal never started external work, so this stored
                    # occurrence may be retried: revive the SAME retained row
                    # instead of deleting provenance or weakening UNIQUE.
                    connection.execute(
                        "UPDATE desktop_automation_occurrences SET status = 'claimed', "
                        "claimed_at = ?, run_id = NULL, completed_at = NULL, error = NULL "
                        "WHERE id = ?",
                        (stamp, existing["id"]),
                    )
                    claim_id = existing["id"]
                else:
                    return None
            else:
                claim_id = uuid4().hex
                connection.execute(
                    "INSERT INTO desktop_automation_occurrences "
                    "(id, automation_id, scheduled_for, status, claimed_at) VALUES (?, ?, ?, 'claimed', ?)",
                    (claim_id, automation_id, scheduled_for, stamp),
                )
        return {
            "id": claim_id,
            "automation_id": automation_id,
            "scheduled_for": scheduled_for,
            "status": "claimed",
            "claimed_at": stamp,
            "run_id": None,
            "completed_at": None,
            "error": None,
        }

    def refuse_claim(self, occurrence_id: str, error: str) -> None:
        """Retain a claim whose dispatch was refused before any work started.

        The row stays as durable provenance (status 'failed', error prefixed
        'dispatch refused:'); claim_occurrence may later revive exactly this
        class of row because nothing external ever executed for it.
        """

        message = error if error.startswith("dispatch refused:") else f"dispatch refused: {error}"
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automation_occurrences SET status = 'failed', error = ?, completed_at = ? "
                "WHERE id = ? AND status IN ('claimed', 'running')",
                (message[:200], _now(), occurrence_id),
            )

    def mark_claim_dispatched(self, occurrence_id: str, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automation_occurrences SET status = 'running', run_id = ? "
                "WHERE id = ? AND status = 'claimed'",
                (_text(run_id, "run_id", 100), occurrence_id),
            )

    def set_condition_check(self, occurrence_id: str, payload: Mapping[str, Any]) -> None:
        """Persist deterministic condition-check provenance on the claim."""

        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automation_occurrences SET check_json = ? "
                "WHERE id = ? AND status IN ('claimed', 'running')",
                (
                    json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                    occurrence_id,
                ),
            )

    def record_check_at(self, automation_id: str, stamp: str) -> dict[str, Any]:
        """Advance conditional CHECK cadence without touching run bookkeeping."""

        if self.get(automation_id) is None:
            raise ValueError("automation not found")
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_automations SET last_check_at = ?, updated_at = ?, revision = revision + 1 WHERE id = ?",
                (stamp, _now(), automation_id),
            )
        return self.get(automation_id) or {}

    def complete_claim(
        self, occurrence_id: str, status: str, error: str | None = None
    ) -> bool:
        """Transition a non-terminal claim to a terminal state exactly once.

        Idempotent: terminal rows are never rewritten and completed_at is set
        only on the first transition. Returns True when this call changed the
        row.
        """

        if status not in {"succeeded", "failed", "skipped"}:
            raise ValueError("terminal claim status must be succeeded, failed, or skipped")
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE desktop_automation_occurrences "
                "SET status = ?, error = ?, completed_at = "
                "CASE WHEN completed_at IS NULL THEN ? ELSE completed_at END "
                "WHERE id = ? AND status IN ('claimed', 'running')",
                (status, error, now, occurrence_id),
            )
        return cursor.rowcount > 0

    def complete_claim_by_run(self, run_id: str, job_status: str, error: str | None = None) -> int:
        """Map a canonical RunJob terminal outcome onto its linked occurrence.

        succeeded maps to succeeded; every other terminal RunJob outcome
        (partial / failed / cancelled) truthfully maps to failed carrying the
        real job status. Non-terminal jobs are ignored.
        """

        if job_status == "succeeded":
            return self._complete_linked_claims(run_id, "succeeded", None)
        if job_status in {"partial", "failed", "cancelled"}:
            detail = error or f"run finished with status {job_status}"
            return self._complete_linked_claims(run_id, "failed", f"run {job_status}: {detail}")
        return 0

    def _complete_linked_claims(self, run_id: str, status: str, error: str | None) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM desktop_automation_occurrences "
                "WHERE run_id = ? AND status IN ('claimed', 'running')",
                (run_id,),
            ).fetchall()
        changed = 0
        for row in rows:
            if self.complete_claim(row["id"], status, error):
                changed += 1
        return changed

    def get_occurrence(self, occurrence_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_automation_occurrences WHERE id = ?", (occurrence_id,)
            ).fetchone()
        return self._claim_record(row) if row else None

    def find_occurrence(self, automation_id: str, scheduled_for: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_automation_occurrences WHERE automation_id = ? AND scheduled_for = ?",
                (automation_id, scheduled_for),
            ).fetchone()
        return self._claim_record(row) if row else None

    def find_claim_by_run(self, run_id: str) -> dict[str, Any] | None:
        """Latest occurrence claim carrying this run_id (B1.4A review link)."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_automation_occurrences "
                "WHERE run_id = ? ORDER BY claimed_at DESC, rowid DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return self._claim_record(row) if row else None

    def list_occurrences(
        self,
        automation_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Occurrence records; optionally bounded and newest-first.

        B1.3B history access uses newest_first=True with a small limit so the
        Studio panel never loads an unbounded table.
        """

        order = "DESC" if newest_first else "ASC"
        parameters: list[Any] = []
        query = "SELECT * FROM desktop_automation_occurrences"
        if automation_id is not None:
            query += " WHERE automation_id = ?"
            parameters.append(automation_id)
        query += f" ORDER BY claimed_at {order}, rowid {order}"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(max(0, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._claim_record(row) for row in rows]

    def latest_occurrence(self, automation_id: str) -> dict[str, Any] | None:
        occurrences = self.list_occurrences(automation_id, limit=1, newest_first=True)
        return occurrences[0] if occurrences else None

    def delete(self, automation_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM desktop_automations WHERE id = ?", (automation_id,))
            connection.execute(
                "DELETE FROM desktop_automation_occurrences WHERE automation_id = ?", (automation_id,)
            )
        if cursor.rowcount == 0:
            raise ValueError("automation not found")
