"""SQLite durability for AMCA project snapshots and semantic events."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from pathlib import Path
import sqlite3
import threading

from .domain import CognitiveEvent, ProjectState, utc_now
from .security import assert_serialized_json_is_safe


class ModelCallReservationStatus(str, Enum):
    """Durable lifecycle for an admitted model call."""

    RESERVED = "reserved"
    SETTLED = "settled"
    RECONCILED = "reconciled"
    RELEASED = "released"


class BudgetReservationError(RuntimeError):
    """Raised when an atomic durable reservation would exceed its budget."""


@dataclass(frozen=True, slots=True)
class ModelCallReservation:
    """One durable, leased model-call admission record.

    ``RECONCILED`` is deliberately conservative: when a process disappears
    after admission, AMCA charges the estimate until a late authoritative
    settlement arrives. This prevents a restart from spending the same budget
    twice when the provider may have accepted the original call.
    """

    id: str
    project_id: str
    owner_id: str
    adapter_id: str
    is_api: bool
    estimated_cost_usd: float
    actual_cost_usd: float | None
    status: ModelCallReservationStatus
    reserved_at: str
    lease_expires_at: str
    settled_at: str | None = None
    reconciliation_reason: str | None = None

    @property
    def charged_cost_usd(self) -> float:
        if not self.is_api or self.status is ModelCallReservationStatus.RELEASED:
            return 0.0
        if (
            self.status is ModelCallReservationStatus.SETTLED
            and self.actual_cost_usd is not None
        ):
            return self.actual_cost_usd
        return self.estimated_cost_usd


def _parse_utc(value: str, *, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _finite_nonnegative(value: float, *, name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return normalized


class SQLiteStore:
    """Persist immutable state versions and idempotent cognitive events.

    The connection is safe to call from multiple Python threads because access
    is serialized with a re-entrant lock. Methods are synchronous by design;
    callers in a latency-sensitive asyncio path should use ``asyncio.to_thread``
    or a dedicated persistence worker.
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> None:
        self.path = str(path)
        if self.path != ":memory:" and not self.path.startswith("file:"):
            Path(self.path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
            timeout=timeout,
            isolation_level=None,
            check_same_thread=False,
            uri=self.path.startswith("file:"),
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLiteStore is closed")

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    latest_version INTEGER NOT NULL CHECK (latest_version >= 0),
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_snapshots (
                    project_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 0),
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, version)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    start_state_version INTEGER NOT NULL
                        CHECK (start_state_version >= 0),
                    applied_state_version INTEGER
                        CHECK (applied_state_version IS NULL
                               OR applied_state_version >= 0),
                    status TEXT NOT NULL CHECK (status IN ('pending', 'applied')),
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_project_order
                    ON events(project_id, created_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_events_pending
                    ON events(project_id, status);

                CREATE TABLE IF NOT EXISTS model_cost_ledgers (
                    project_id TEXT PRIMARY KEY,
                    baseline_api_cost_usd REAL NOT NULL
                        CHECK (baseline_api_cost_usd >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_call_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    is_api INTEGER NOT NULL CHECK (is_api IN (0, 1)),
                    estimated_cost_usd REAL NOT NULL
                        CHECK (estimated_cost_usd >= 0),
                    actual_cost_usd REAL
                        CHECK (actual_cost_usd IS NULL OR actual_cost_usd >= 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('reserved', 'settled', 'reconciled', 'released')
                    ),
                    reserved_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    settled_at TEXT,
                    reconciliation_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_model_calls_project_status
                    ON model_call_reservations(project_id, status);
                CREATE INDEX IF NOT EXISTS idx_model_calls_owner_status
                    ON model_call_reservations(owner_id, status);
                CREATE INDEX IF NOT EXISTS idx_model_calls_expiry
                    ON model_call_reservations(status, lease_expires_at);
                """
            )
            version = int(
                self._connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if version not in (0, self.SCHEMA_VERSION):
                raise RuntimeError(
                    f"unsupported AMCA database schema version {version}"
                )
            self._connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _write_snapshot(
        connection: sqlite3.Connection, state: ProjectState
    ) -> None:
        state_json = state.to_json()
        assert_serialized_json_is_safe(state_json, path="project_state")
        existing = connection.execute(
            """
            SELECT state_json FROM state_snapshots
            WHERE project_id = ? AND version = ?
            """,
            (state.project_id, state.version),
        ).fetchone()
        if existing is not None and existing["state_json"] != state_json:
            raise ValueError(
                f"project {state.project_id!r} version {state.version} "
                "already has a different immutable snapshot"
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO state_snapshots
                (project_id, version, state_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (state.project_id, state.version, state_json, state.updated_at),
        )
        current = connection.execute(
            "SELECT latest_version FROM projects WHERE project_id = ?",
            (state.project_id,),
        ).fetchone()
        if current is None:
            connection.execute(
                """
                INSERT INTO projects
                    (project_id, latest_version, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    state.project_id,
                    state.version,
                    state_json,
                    state.created_at,
                    state.updated_at,
                ),
            )
        elif state.version >= int(current["latest_version"]):
            connection.execute(
                """
                UPDATE projects
                SET latest_version = ?, state_json = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (state.version, state_json, state.updated_at, state.project_id),
            )

    def save_state(self, state: ProjectState) -> None:
        """Save an immutable snapshot; an older save never regresses latest."""

        if not isinstance(state, ProjectState):
            raise TypeError("save_state requires a ProjectState")
        with self._transaction() as connection:
            self._write_snapshot(connection, state)

    save_snapshot = save_state

    def append_event(self, project_id: str, event: CognitiveEvent) -> None:
        """Durably enqueue an event without applying it to canonical state."""

        if not project_id:
            raise ValueError("project_id must not be empty")
        if not isinstance(event, CognitiveEvent):
            raise TypeError("append_event requires a CognitiveEvent")
        event_json = event.to_json()
        assert_serialized_json_is_safe(event_json, path="event")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT project_id, event_json FROM events WHERE event_id = ?",
                (event.id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["project_id"] != project_id
                    or existing["event_json"] != event_json
                ):
                    raise ValueError(f"event id {event.id!r} is already in use")
                return
            connection.execute(
                """
                INSERT INTO events
                    (event_id, project_id, event_json, start_state_version,
                     applied_state_version, status, created_at)
                VALUES (?, ?, ?, ?, NULL, 'pending', ?)
                """,
                (
                    event.id,
                    project_id,
                    event_json,
                    event.state_version,
                    event.created_at,
                ),
            )

    save_event = append_event

    def save_transition(self, state: ProjectState, event: CognitiveEvent) -> None:
        """Atomically persist one applied event and its resulting state version."""

        if not isinstance(state, ProjectState):
            raise TypeError("save_transition requires a ProjectState")
        if not isinstance(event, CognitiveEvent):
            raise TypeError("save_transition requires a CognitiveEvent")
        if event.id not in state.applied_event_ids:
            raise ValueError("resulting state does not record the event as applied")
        if state.version <= event.state_version:
            raise ValueError("resulting state version must advance past event version")
        event_json = event.to_json()
        assert_serialized_json_is_safe(event_json, path="event")
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT project_id, event_json, applied_state_version
                FROM events WHERE event_id = ?
                """,
                (event.id,),
            ).fetchone()
            if existing is not None and (
                existing["project_id"] != state.project_id
                or existing["event_json"] != event_json
            ):
                raise ValueError(f"event id {event.id!r} is already in use")
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO events
                        (event_id, project_id, event_json, start_state_version,
                         applied_state_version, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'applied', ?)
                    """,
                    (
                        event.id,
                        state.project_id,
                        event_json,
                        event.state_version,
                        state.version,
                        event.created_at,
                    ),
                )
            else:
                prior_version = existing["applied_state_version"]
                if prior_version is not None and int(prior_version) != state.version:
                    raise ValueError(
                        f"event {event.id!r} was already applied at a different version"
                    )
                connection.execute(
                    """
                    UPDATE events
                    SET applied_state_version = ?, status = 'applied'
                    WHERE event_id = ?
                    """,
                    (state.version, event.id),
                )
            self._write_snapshot(connection, state)

    def mark_event_applied(self, event_id: str, applied_state_version: int) -> None:
        """Mark a queued event applied when transition persistence is external."""

        if applied_state_version < 0:
            raise ValueError("applied_state_version must be non-negative")
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE events SET status = 'applied', applied_state_version = ?
                WHERE event_id = ?
                """,
                (applied_state_version, event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown event {event_id!r}")

    def load_state(
        self, project_id: str, version: int | None = None
    ) -> ProjectState | None:
        """Load the latest or an exact immutable state version."""

        self._ensure_open()
        with self._lock:
            if version is None:
                row = self._connection.execute(
                    "SELECT state_json FROM projects WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT state_json FROM state_snapshots
                    WHERE project_id = ? AND version = ?
                    """,
                    (project_id, version),
                ).fetchone()
        return ProjectState.from_json(row["state_json"]) if row is not None else None

    load_latest_state = load_state

    def load_event(self, event_id: str) -> CognitiveEvent | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                "SELECT event_json FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return CognitiveEvent.from_json(row["event_json"]) if row is not None else None

    def load_events(
        self,
        project_id: str,
        *,
        after_version: int | None = None,
        pending_only: bool = False,
        limit: int | None = None,
    ) -> list[CognitiveEvent]:
        """Load events in durable insertion order.

        ``after_version`` filters on the canonical version at which an event was
        applied. Pending events are retained when no version filter is supplied.
        """

        if after_version is not None and after_version < 0:
            raise ValueError("after_version must be non-negative")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        clauses = ["project_id = ?"]
        parameters: list[object] = [project_id]
        if pending_only:
            clauses.append("status = 'pending'")
        if after_version is not None:
            clauses.append("applied_state_version > ?")
            parameters.append(after_version)
        sql = (
            "SELECT event_json FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY rowid"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [CognitiveEvent.from_json(row["event_json"]) for row in rows]

    def pending_events(self, project_id: str) -> list[CognitiveEvent]:
        return self.load_events(project_id, pending_only=True)

    def resume(
        self, project_id: str
    ) -> tuple[ProjectState, list[CognitiveEvent]] | None:
        """Return latest state and unapplied durable events for crash recovery."""

        state = self.load_state(project_id)
        if state is None:
            return None
        return state, self.pending_events(project_id)

    resume_project = resume

    def list_projects(self) -> list[str]:
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(
                "SELECT project_id FROM projects ORDER BY updated_at DESC, project_id"
            ).fetchall()
        return [str(row["project_id"]) for row in rows]

    def snapshot_versions(self, project_id: str) -> list[int]:
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT version FROM state_snapshots
                WHERE project_id = ? ORDER BY version
                """,
                (project_id,),
            ).fetchall()
        return [int(row["version"]) for row in rows]

    @staticmethod
    def _reservation_from_row(row: sqlite3.Row) -> ModelCallReservation:
        actual = row["actual_cost_usd"]
        return ModelCallReservation(
            id=str(row["reservation_id"]),
            project_id=str(row["project_id"]),
            owner_id=str(row["owner_id"]),
            adapter_id=str(row["adapter_id"]),
            is_api=bool(row["is_api"]),
            estimated_cost_usd=float(row["estimated_cost_usd"]),
            actual_cost_usd=float(actual) if actual is not None else None,
            status=ModelCallReservationStatus(str(row["status"])),
            reserved_at=str(row["reserved_at"]),
            lease_expires_at=str(row["lease_expires_at"]),
            settled_at=(
                str(row["settled_at"]) if row["settled_at"] is not None else None
            ),
            reconciliation_reason=(
                str(row["reconciliation_reason"])
                if row["reconciliation_reason"] is not None
                else None
            ),
        )

    @staticmethod
    def _ledger_charged_cost(
        connection: sqlite3.Connection, project_id: str
    ) -> float:
        baseline_row = connection.execute(
            """
            SELECT baseline_api_cost_usd FROM model_cost_ledgers
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        baseline = (
            float(baseline_row["baseline_api_cost_usd"])
            if baseline_row is not None
            else 0.0
        )
        row = connection.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                    WHEN is_api = 0 OR status = 'released' THEN 0.0
                    WHEN status = 'settled'
                        THEN COALESCE(actual_cost_usd, estimated_cost_usd)
                    ELSE estimated_cost_usd
                END
            ), 0.0) AS charged
            FROM model_call_reservations
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        return baseline + float(row["charged"])

    @staticmethod
    def _reconcile_expired_locked(
        connection: sqlite3.Connection,
        *,
        at: str,
        project_id: str | None = None,
    ) -> int:
        clauses = ["status = 'reserved'", "lease_expires_at <= ?"]
        parameters: list[object] = [at]
        if project_id is not None:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        cursor = connection.execute(
            """
            UPDATE model_call_reservations
            SET status = 'reconciled', settled_at = ?,
                reconciliation_reason = 'reservation lease expired before settlement'
            WHERE """
            + " AND ".join(clauses),
            [at, *parameters],
        )
        return int(cursor.rowcount)

    @staticmethod
    def _ensure_cost_baseline(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        recorded_api_cost_usd: float,
        at: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO model_cost_ledgers
                (project_id, baseline_api_cost_usd, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, recorded_api_cost_usd, at, at),
        )
        # Snapshots include settled calls.  If legacy/external COST_UPDATE
        # events are present, lift only the portion not already represented by
        # authoritative settled rows; never lower a conservative baseline.
        row = connection.execute(
            """
            SELECT COALESCE(SUM(actual_cost_usd), 0.0) AS settled
            FROM model_call_reservations
            WHERE project_id = ? AND is_api = 1 AND status = 'settled'
            """,
            (project_id,),
        ).fetchone()
        minimum_baseline = max(0.0, recorded_api_cost_usd - float(row["settled"]))
        connection.execute(
            """
            UPDATE model_cost_ledgers
            SET baseline_api_cost_usd = MAX(baseline_api_cost_usd, ?),
                updated_at = ?
            WHERE project_id = ?
            """,
            (minimum_baseline, at, project_id),
        )

    def reserve_model_call(
        self,
        *,
        reservation_id: str,
        project_id: str,
        owner_id: str,
        adapter_id: str,
        is_api: bool,
        estimated_cost_usd: float,
        recorded_api_cost_usd: float,
        budget_usd: float | None,
        lease_seconds: float = 300.0,
        reserved_at: str | None = None,
    ) -> ModelCallReservation:
        """Atomically admit one call against a durable project cost ledger.

        The transaction reconciles expired leases, seeds a one-time baseline
        for pre-ledger project spend, checks all live/conservative charges, and
        inserts the reservation. Separate processes using the same SQLite file
        therefore cannot both spend the same remaining budget.
        """

        for name, value in (
            ("reservation_id", reservation_id),
            ("project_id", project_id),
            ("owner_id", owner_id),
            ("adapter_id", adapter_id),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must not be empty")
        estimate = _finite_nonnegative(
            estimated_cost_usd, name="estimated_cost_usd"
        )
        recorded = _finite_nonnegative(
            recorded_api_cost_usd, name="recorded_api_cost_usd"
        )
        if budget_usd is not None:
            budget_usd = _finite_nonnegative(budget_usd, name="budget_usd")
        lease = float(lease_seconds)
        if not math.isfinite(lease) or lease <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        reserved = reserved_at or utc_now()
        reserved_time = _parse_utc(reserved, name="reserved_at")
        expires = _format_utc(reserved_time + timedelta(seconds=lease))

        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM model_call_reservations
                WHERE reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                current = self._reservation_from_row(existing)
                if (
                    current.project_id != project_id
                    or current.owner_id != owner_id
                    or current.adapter_id != adapter_id
                    or current.is_api is not bool(is_api)
                    or current.estimated_cost_usd != estimate
                ):
                    raise ValueError(
                        f"model-call reservation id {reservation_id!r} is already in use"
                    )
                return current
            self._reconcile_expired_locked(
                connection, at=_format_utc(reserved_time), project_id=project_id
            )
            self._ensure_cost_baseline(
                connection,
                project_id=project_id,
                recorded_api_cost_usd=recorded,
                at=_format_utc(reserved_time),
            )
            charged = self._ledger_charged_cost(connection, project_id)
            additional = estimate if is_api else 0.0
            if budget_usd is not None and charged + additional > budget_usd:
                raise BudgetReservationError(
                    "durable model-call reservation would exceed the API budget"
                )
            connection.execute(
                """
                INSERT INTO model_call_reservations (
                    reservation_id, project_id, owner_id, adapter_id, is_api,
                    estimated_cost_usd, actual_cost_usd, status, reserved_at,
                    lease_expires_at, settled_at, reconciliation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'reserved', ?, ?, NULL, NULL)
                """,
                (
                    reservation_id,
                    project_id,
                    owner_id,
                    adapter_id,
                    int(bool(is_api)),
                    estimate,
                    _format_utc(reserved_time),
                    expires,
                ),
            )
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert row is not None
            return self._reservation_from_row(row)

    def increase_model_call_reservation(
        self,
        reservation_id: str,
        *,
        additional_estimated_cost_usd: float,
        recorded_api_cost_usd: float,
        budget_usd: float | None,
        at: str | None = None,
    ) -> ModelCallReservation:
        """Atomically reserve incremental cost before a rebase/retry call."""

        additional = _finite_nonnegative(
            additional_estimated_cost_usd,
            name="additional_estimated_cost_usd",
        )
        recorded = _finite_nonnegative(
            recorded_api_cost_usd, name="recorded_api_cost_usd"
        )
        if budget_usd is not None:
            budget_usd = _finite_nonnegative(budget_usd, name="budget_usd")
        timestamp = _format_utc(_parse_utc(at or utc_now(), name="at"))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown model-call reservation {reservation_id!r}")
            current = self._reservation_from_row(row)
            if current.status is not ModelCallReservationStatus.RESERVED:
                raise ValueError("only a live model-call reservation can be increased")
            self._reconcile_expired_locked(
                connection, at=timestamp, project_id=current.project_id
            )
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert row is not None
            current = self._reservation_from_row(row)
            if current.status is not ModelCallReservationStatus.RESERVED:
                raise ValueError("model-call reservation lease expired")
            self._ensure_cost_baseline(
                connection,
                project_id=current.project_id,
                recorded_api_cost_usd=recorded,
                at=timestamp,
            )
            charged = self._ledger_charged_cost(connection, current.project_id)
            if (
                current.is_api
                and budget_usd is not None
                and charged + additional > budget_usd
            ):
                raise BudgetReservationError(
                    "durable model-call reservation increase would exceed the API budget"
                )
            connection.execute(
                """
                UPDATE model_call_reservations
                SET estimated_cost_usd = estimated_cost_usd + ?
                WHERE reservation_id = ?
                """,
                (additional, reservation_id),
            )
            updated = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert updated is not None
            return self._reservation_from_row(updated)

    def settle_model_call(
        self,
        reservation_id: str,
        *,
        actual_cost_usd: float,
        settled_at: str | None = None,
    ) -> ModelCallReservation:
        """Replace an estimate with authoritative provider-reported usage."""

        actual = _finite_nonnegative(actual_cost_usd, name="actual_cost_usd")
        timestamp = _format_utc(
            _parse_utc(settled_at or utc_now(), name="settled_at")
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown model-call reservation {reservation_id!r}")
            current = self._reservation_from_row(row)
            if current.status is ModelCallReservationStatus.RELEASED:
                raise ValueError("a released reservation cannot be settled")
            if current.status is ModelCallReservationStatus.SETTLED:
                if current.actual_cost_usd != actual:
                    raise ValueError("model-call reservation already has a different cost")
                return current
            connection.execute(
                """
                UPDATE model_call_reservations
                SET status = 'settled', actual_cost_usd = ?, settled_at = ?,
                    reconciliation_reason = NULL
                WHERE reservation_id = ?
                """,
                (actual, timestamp, reservation_id),
            )
            updated = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert updated is not None
            return self._reservation_from_row(updated)

    def reconcile_model_call(
        self,
        reservation_id: str,
        *,
        reason: str,
        at: str | None = None,
    ) -> ModelCallReservation:
        """Conservatively charge an estimate when actual usage is unknowable."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reconciliation reason must not be empty")
        timestamp = _format_utc(_parse_utc(at or utc_now(), name="at"))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown model-call reservation {reservation_id!r}")
            current = self._reservation_from_row(row)
            if current.status is ModelCallReservationStatus.SETTLED:
                return current
            if current.status is ModelCallReservationStatus.RELEASED:
                raise ValueError("a released reservation cannot be reconciled")
            if current.status is ModelCallReservationStatus.RECONCILED:
                return current
            connection.execute(
                """
                UPDATE model_call_reservations
                SET status = 'reconciled', settled_at = ?,
                    reconciliation_reason = ?
                WHERE reservation_id = ?
                """,
                (timestamp, reason.strip(), reservation_id),
            )
            updated = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert updated is not None
            return self._reservation_from_row(updated)

    def reconcile_expired_model_calls(
        self, *, project_id: str | None = None, at: str | None = None
    ) -> int:
        """Mark expired live leases conservative after a crash/restart."""

        timestamp = _format_utc(_parse_utc(at or utc_now(), name="at"))
        with self._transaction() as connection:
            return self._reconcile_expired_locked(
                connection, at=timestamp, project_id=project_id
            )

    def reconcile_model_call_owner(
        self, owner_id: str, *, reason: str, at: str | None = None
    ) -> int:
        """Close every still-live reservation owned by a finished kernel run."""

        if not owner_id:
            raise ValueError("owner_id must not be empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reconciliation reason must not be empty")
        timestamp = _format_utc(_parse_utc(at or utc_now(), name="at"))
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE model_call_reservations
                SET status = 'reconciled', settled_at = ?,
                    reconciliation_reason = ?
                WHERE owner_id = ? AND status = 'reserved'
                """,
                (timestamp, reason.strip(), owner_id),
            )
            return int(cursor.rowcount)

    def release_model_call(
        self, reservation_id: str, *, reason: str, at: str | None = None
    ) -> ModelCallReservation:
        """Release only a reservation known not to have reached a provider."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("release reason must not be empty")
        timestamp = _format_utc(_parse_utc(at or utc_now(), name="at"))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown model-call reservation {reservation_id!r}")
            current = self._reservation_from_row(row)
            if current.status is ModelCallReservationStatus.RELEASED:
                return current
            if current.status is not ModelCallReservationStatus.RESERVED:
                raise ValueError("only a live reservation can be released")
            connection.execute(
                """
                UPDATE model_call_reservations
                SET status = 'released', actual_cost_usd = 0.0, settled_at = ?,
                    reconciliation_reason = ?
                WHERE reservation_id = ?
                """,
                (timestamp, reason.strip(), reservation_id),
            )
            updated = connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            assert updated is not None
            return self._reservation_from_row(updated)

    def load_model_call_reservation(
        self, reservation_id: str
    ) -> ModelCallReservation | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM model_call_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return self._reservation_from_row(row) if row is not None else None

    def list_model_call_reservations(
        self,
        project_id: str,
        *,
        status: ModelCallReservationStatus | str | None = None,
    ) -> list[ModelCallReservation]:
        self._ensure_open()
        parameters: list[object] = [project_id]
        sql = "SELECT * FROM model_call_reservations WHERE project_id = ?"
        if status is not None:
            normalized = ModelCallReservationStatus(status)
            sql += " AND status = ?"
            parameters.append(normalized.value)
        sql += " ORDER BY rowid"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [self._reservation_from_row(row) for row in rows]

    def model_call_charged_cost(self, project_id: str) -> float:
        """Return baseline + settled/live/reconciled API charges."""

        self._ensure_open()
        with self._lock:
            return self._ledger_charged_cost(self._connection, project_id)

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> SQLiteStore:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "BudgetReservationError",
    "ModelCallReservation",
    "ModelCallReservationStatus",
    "SQLiteStore",
]
