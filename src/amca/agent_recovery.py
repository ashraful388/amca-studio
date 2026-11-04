"""Durable Agent Fabric recovery state (A2.1A).

SQLite schemas and CRUD repositories for crash-resumable Agent Fabric
execution, implementing the accepted recovery contract
(docs/a2-agent-fabric-recovery-contract.md, milestone AG-A2.0.2):

- organizations, tasks, call attempts, reservations, verifier verdicts;
- stable sub-call identity UNIQUE(organization_id, task_id,
  task_attempt_number, call_key);
- mutually exclusive reservation states ACTIVE/UNCERTAIN/SETTLED/RELEASED
  with the hard-budget invariant incurred + active + uncertain <= budget;
- call-attempt journal states PLANNED -> RESERVED -> DISPATCH_INTENT_DURABLE
  -> DISPATCHED -> RESPONSE_RECEIVED -> RESPONSE_COMMITTED -> SETTLED with
  the contract's recovery dispositions.

This milestone is durable state and repositories ONLY: no startup
reconciliation, no automatic retries, and no live dispatch wiring (those are
A2.1B-D). Nothing here changes existing runtime behavior.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


CALL_STATES = (
    "PLANNED",
    "RESERVED",
    "DISPATCH_INTENT_DURABLE",
    "DISPATCHED",
    "RESPONSE_RECEIVED",
    "RESPONSE_COMMITTED",
    "SETTLED",
)

# Recovery dispositions per contract section 2.1.
RECOVERY_DISPOSITIONS = {
    "PLANNED": "SAFE_TO_RESUME",
    "RESERVED": "SAFE_TO_RESUME",
    "DISPATCH_INTENT_DURABLE": "UNKNOWN_EXTERNAL_OUTCOME",
    "DISPATCHED": "UNKNOWN_EXTERNAL_OUTCOME",
    "RESPONSE_RECEIVED": "UNKNOWN_EXTERNAL_OUTCOME",
    "RESPONSE_COMMITTED": "RECOVERED_FROM_COMMITTED_RESPONSE",
    "SETTLED": "TERMINAL",
}

RESERVATION_STATUSES = ("active", "uncertain", "settled", "released")


def recovery_disposition_for(state: str) -> str:
    """Recovery disposition for a call attempt found in *state* at crash."""

    return RECOVERY_DISPOSITIONS[state]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentRecoveryStore:
    """SQLite durable state + repositories for Agent Fabric recovery.

    The store operates on the CANONICAL AMCA database path (the same file
    ServiceContext uses) - it never creates a competing recovery database.
    Migrations are idempotent CREATE IF NOT EXISTS statements plus a guarded
    rebuild for the attempt-aware verifier-verdict identity, so pre-A2
    databases upgrade in place with existing data preserved.

    Cross-table transactions: `with store.transaction() as tx:` shares ONE
    connection and ONE commit boundary across repository calls, so logically
    coupled recovery facts (reservation + call attempt, response + state)
    commit or roll back together.
    """

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._tx_connection: sqlite3.Connection | None = None
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._tx_connection is not None:
            # Inside an explicit transaction: share the connection; the
            # transaction() owner owns the commit boundary.
            yield self._tx_connection
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator["AgentRecoveryStore"]:
        """Share one SQLite transaction across repository operations.

        Nested transactions are not supported. On success everything written
        through this view commits together; on any exception everything rolls
        back. Methods remain individually usable outside transactions.
        """

        if self._tx_connection is not None:
            raise RuntimeError("nested AgentRecoveryStore.transaction() call")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        previous = self._tx_connection
        self._tx_connection = connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield self
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            self._tx_connection = previous
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_organizations (
                    organization_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    budget_usd REAL NOT NULL,
                    max_concurrent INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL,
                    retired INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_tasks (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES desktop_agent_organizations(organization_id),
                    project_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    status TEXT NOT NULL,
                    recovery_disposition TEXT,
                    external_outcome_uncertain INTEGER NOT NULL DEFAULT 0,
                    executor_kind TEXT NOT NULL,
                    depends_on_json TEXT NOT NULL,
                    verifier_ids_json TEXT NOT NULL,
                    participant_ids_json TEXT NOT NULL,
                    collaboration_architecture TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result_summary TEXT,
                    verification_result_json TEXT,
                    error TEXT,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    local_calls INTEGER NOT NULL DEFAULT 0,
                    api_calls INTEGER NOT NULL DEFAULT 0,
                    reused_from TEXT,
                    reservation_id TEXT,
                    proposed_children_json TEXT,
                    proposed_by_manager_id TEXT,
                    manager_depth INTEGER NOT NULL DEFAULT 0,
                    authorization_record_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_call_attempts (
                    call_attempt_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES desktop_agent_organizations(organization_id),
                    task_id TEXT NOT NULL REFERENCES desktop_agent_tasks(id),
                    task_attempt_number INTEGER NOT NULL,
                    call_key TEXT NOT NULL,
                    executor_kind TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    participant_id TEXT,
                    phase_id TEXT,
                    round_index INTEGER,
                    work_unit_id TEXT,
                    reservation_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reserved_usd REAL NOT NULL DEFAULT 0.0,
                    actual_cost_usd REAL NOT NULL DEFAULT 0.0,
                    response_payload TEXT,
                    provider_request_id TEXT,
                    dispatch_intent_at TEXT,
                    dispatched_at TEXT,
                    response_received_at TEXT,
                    response_committed_at TEXT,
                    settled_at TEXT,
                    recovery_disposition TEXT,
                    UNIQUE(organization_id, task_id, task_attempt_number, call_key)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES desktop_agent_organizations(organization_id),
                    task_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    amount_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    settled_actual_usd REAL NOT NULL DEFAULT 0.0,
                    integrity_violation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    settled_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS desktop_agent_verification_verdicts (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES desktop_agent_organizations(organization_id),
                    task_id TEXT NOT NULL REFERENCES desktop_agent_tasks(id),
                    task_attempt_number INTEGER NOT NULL,
                    verifier_id TEXT NOT NULL,
                    call_attempt_id TEXT REFERENCES desktop_agent_call_attempts(call_attempt_id),
                    verdict TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL,
                    UNIQUE(organization_id, task_id, task_attempt_number, verifier_id)
                )"""
            )
            # A2.1D.1 migration: continuation needs the original scheduling
            # bounds; older databases gain the columns via guarded ALTER.
            task_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(desktop_agent_tasks)"
                )
            }
            if task_columns and "max_rounds" not in task_columns:
                connection.execute(
                    "ALTER TABLE desktop_agent_tasks "
                    "ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT 1"
                )
            if task_columns and "max_adaptive_phases" not in task_columns:
                connection.execute(
                    "ALTER TABLE desktop_agent_tasks "
                    "ADD COLUMN max_adaptive_phases INTEGER NOT NULL DEFAULT 4"
                )
            if task_columns and "sync_mode" not in task_columns:
                connection.execute(
                    "ALTER TABLE desktop_agent_tasks ADD COLUMN sync_mode TEXT"
                )

            # A3.3 economy telemetry columns (nullable = authoritative unknown)
            call_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(desktop_agent_call_attempts)"
                )
            }
            for column_ddl in (
                "ADD COLUMN input_tokens INTEGER",
                "ADD COLUMN output_tokens INTEGER",
                "ADD COLUMN token_source TEXT",
                "ADD COLUMN batch_transport_id TEXT",
                "ADD COLUMN provider_latency_ms REAL",
            ):
                column_name = column_ddl.split()[-2] if False else column_ddl.split()[2]
                if column_name not in call_columns:
                    connection.execute(
                        f"ALTER TABLE desktop_agent_call_attempts {column_ddl}"
                    )

            # Indexes for known recovery queries only (A2.1A.1 scope)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_desktop_agent_tasks_org "
                "ON desktop_agent_tasks(organization_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_attempts_org_task "
                "ON desktop_agent_call_attempts(organization_id, task_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_attempts_org_disposition "
                "ON desktop_agent_call_attempts(organization_id, recovery_disposition)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reservations_org_status "
                "ON desktop_agent_reservations(organization_id, status)"
            )
            # PS1-13K: support one-bounded-read verdict aggregation by org.
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_verdicts_org_task "
                "ON desktop_agent_verification_verdicts(organization_id, task_id)"
            )

            # A2.1A.1 migration: a dev-era verdicts table with the old
            # UNIQUE(task_id, verifier_id) identity is rebuilt into the
            # attempt-aware shape; legacy rows are preserved verbatim in a
            # backup table and never silently re-keyed.
            verdict_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(desktop_agent_verification_verdicts)"
                )
            }
            if verdict_columns and "task_attempt_number" not in verdict_columns:
                connection.execute(
                    "ALTER TABLE desktop_agent_verification_verdicts "
                    "RENAME TO desktop_agent_verification_verdicts_v1_legacy"
                )
                connection.execute(
                    """CREATE TABLE desktop_agent_verification_verdicts (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL REFERENCES desktop_agent_organizations(organization_id),
                        task_id TEXT NOT NULL REFERENCES desktop_agent_tasks(id),
                        task_attempt_number INTEGER NOT NULL,
                        verifier_id TEXT NOT NULL,
                        call_attempt_id TEXT REFERENCES desktop_agent_call_attempts(call_attempt_id),
                        verdict TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        evidence_refs_json TEXT NOT NULL,
                        cost_usd REAL NOT NULL DEFAULT 0.0,
                        created_at TEXT NOT NULL,
                        UNIQUE(organization_id, task_id, task_attempt_number, verifier_id)
                    )"""
                )

    # ------------------------------------------------------------------
    # Organizations

    def create_organization(
        self,
        *,
        organization_id: str,
        project_id: str,
        goal: str,
        budget_usd: float,
        max_concurrent: int,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO desktop_agent_organizations
                   (organization_id, project_id, goal, budget_usd, max_concurrent,
                    max_attempts, status, retired, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', 0, ?, ?)""",
                (organization_id, project_id, goal, float(budget_usd),
                 int(max_concurrent), int(max_attempts), now, now),
            )
        return self.get_organization(organization_id)

    def get_organization(self, organization_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_agent_organizations WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        return dict(row) if row else None

    def retire_organization(self, organization_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_agent_organizations SET retired = 1, updated_at = ? "
                "WHERE organization_id = ?",
                (_now(), organization_id),
            )

    # ------------------------------------------------------------------
    # Tasks

    def create_task(
        self,
        *,
        task_id: str,
        organization_id: str,
        project_id: str,
        agent_id: str,
        focus: str,
        executor_kind: str,
        depends_on: list[str] | None = None,
        verifier_ids: list[str] | None = None,
        participant_ids: list[str] | None = None,
        collaboration_architecture: str | None = None,
        manager_depth: int = 0,
        proposed_by_manager_id: str | None = None,
        authorization_record: dict[str, Any] | None = None,
        max_rounds: int = 1,
        max_adaptive_phases: int = 4,
        sync_mode: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO desktop_agent_tasks (
                    id, organization_id, project_id, agent_id, focus, status,
                    executor_kind, depends_on_json, verifier_ids_json,
                    participant_ids_json, collaboration_architecture,
                    manager_depth, proposed_by_manager_id,
                    authorization_record_json, created_at,
                    max_rounds, max_adaptive_phases, sync_mode)
                   VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?)""",
                (
                    task_id, organization_id, project_id, agent_id, focus,
                    executor_kind,
                    json_dumps(depends_on or []),
                    json_dumps(verifier_ids or []),
                    json_dumps(participant_ids or []),
                    collaboration_architecture,
                    int(manager_depth),
                    proposed_by_manager_id,
                    json_dumps(authorization_record or {}),
                    now,
                    int(max_rounds),
                    int(max_adaptive_phases),
                    sync_mode,
                ),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_agent_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._task_record(row) if row else None

    @staticmethod
    def _task_record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for column in ("depends_on_json", "verifier_ids_json",
                       "participant_ids_json", "authorization_record_json",
                       "verification_result_json", "proposed_children_json"):
            record[column] = json_loads_or_none(record.get(column))
        return record

    def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        error: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_agent_tasks SET status = ?, error = COALESCE(?, error), "
                "result_summary = COALESCE(?, result_summary) WHERE id = ?",
                (status, error, result_summary, task_id),
            )

    def set_task_recovery(self, task_id: str, *, uncertain: bool, disposition: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE desktop_agent_tasks SET external_outcome_uncertain = ?, "
                "recovery_disposition = ? WHERE id = ?",
                (1 if uncertain else 0, disposition, task_id),
            )

    def list_tasks_by_organization(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agent_tasks WHERE organization_id = ? "
                "ORDER BY created_at",
                (organization_id,),
            ).fetchall()
        return [self._task_record(row) for row in rows]

    # ------------------------------------------------------------------
    # Call attempts (stable sub-call identity)

    def record_call_state(
        self,
        *,
        organization_id: str,
        task_id: str,
        task_attempt_number: int,
        call_key: str,
        state: str,
        executor_kind: str,
        adapter_id: str,
        reservation_id: str,
        participant_id: str | None = None,
        phase_id: str | None = None,
        round_index: int | None = None,
        work_unit_id: str | None = None,
        reserved_usd: float = 0.0,
        actual_cost_usd: float = 0.0,
        response_payload: str | None = None,
        provider_request_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        token_source: str | None = None,
        batch_transport_id: str | None = None,
        provider_latency_ms: float | None = None,
    ) -> dict[str, Any]:
        """Insert or advance one call attempt's journal state atomically.

        UNIQUE(organization_id, task_id, task_attempt_number, call_key)
        guarantees the stable sub-call identity; journal states only move
        forward through the contract order.
        """

        if state not in CALL_STATES:
            raise ValueError(f"unknown call state: {state}")
        now = _now()
        stamp_column = {
            "DISPATCH_INTENT_DURABLE": "dispatch_intent_at",
            "DISPATCHED": "dispatched_at",
            "RESPONSE_RECEIVED": "response_received_at",
            "RESPONSE_COMMITTED": "response_committed_at",
            "SETTLED": "settled_at",
        }.get(state)
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT state FROM desktop_agent_call_attempts
                   WHERE organization_id = ? AND task_id = ?
                   AND task_attempt_number = ? AND call_key = ?""",
                (organization_id, task_id, int(task_attempt_number), call_key),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO desktop_agent_call_attempts (
                        call_attempt_id, organization_id, task_id,
                        task_attempt_number, call_key, executor_kind, adapter_id,
                        participant_id, phase_id, round_index, work_unit_id,
                        reservation_id, state, reserved_usd, actual_cost_usd,
                        response_payload, provider_request_id,
                        input_tokens, output_tokens, token_source,
                        batch_transport_id, provider_latency_ms,
                        dispatch_intent_at, dispatched_at, response_received_at,
                        response_committed_at, settled_at, recovery_disposition)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"call-{uuid_hex()}", organization_id, task_id,
                        int(task_attempt_number), call_key, executor_kind,
                        adapter_id, participant_id, phase_id, round_index,
                        work_unit_id, reservation_id, state,
                        float(reserved_usd), float(actual_cost_usd),
                        response_payload, provider_request_id,
                        input_tokens, output_tokens, token_source,
                        batch_transport_id, provider_latency_ms,
                        now if stamp_column else None,
                        now if state in ("DISPATCHED", "RESPONSE_RECEIVED",
                                          "RESPONSE_COMMITTED", "SETTLED") else None,
                        now if state in ("RESPONSE_RECEIVED", "RESPONSE_COMMITTED",
                                          "SETTLED") else None,
                        now if state in ("RESPONSE_COMMITTED", "SETTLED") else None,
                        now if state == "SETTLED" else None,
                        recovery_disposition_for(state),
                    ),
                )
            else:
                previous = existing["state"]
                if CALL_STATES.index(state) < CALL_STATES.index(previous):
                    raise ValueError(
                        f"call attempt journal cannot move backward: "
                        f"{previous} -> {state}"
                    )
                assignments = ["state = ?", "recovery_disposition = ?"]
                parameters: list[Any] = [state, recovery_disposition_for(state)]
                if stamp_column:
                    assignments.append(f"{stamp_column} = COALESCE({stamp_column}, ?)")
                    parameters.append(now)
                assignments.append(
                    "actual_cost_usd = CASE WHEN ? <> 0 THEN ? "
                    "ELSE actual_cost_usd END"
                )
                parameters.extend([float(actual_cost_usd), float(actual_cost_usd)])
                assignments.append("response_payload = COALESCE(?, response_payload)")
                parameters.append(response_payload)
                assignments.append(
                    "provider_request_id = COALESCE(?, provider_request_id)"
                )
                parameters.append(provider_request_id)
                assignments.append("input_tokens = COALESCE(?, input_tokens)")
                parameters.append(input_tokens)
                assignments.append("output_tokens = COALESCE(?, output_tokens)")
                parameters.append(output_tokens)
                assignments.append("token_source = COALESCE(?, token_source)")
                parameters.append(token_source)
                assignments.append(
                    "batch_transport_id = COALESCE(?, batch_transport_id)")
                parameters.append(batch_transport_id)
                assignments.append(
                    "provider_latency_ms = COALESCE(?, provider_latency_ms)")
                parameters.append(provider_latency_ms)
                parameters.extend([organization_id, task_id,
                                   int(task_attempt_number), call_key])
                connection.execute(
                    "UPDATE desktop_agent_call_attempts SET "
                    + ", ".join(assignments)
                    + " WHERE organization_id = ? AND task_id = ? "
                    + "AND task_attempt_number = ? AND call_key = ?",
                    parameters,
                )
        return self.get_call_attempt(organization_id, task_id,
                                     int(task_attempt_number), call_key)

    def get_call_attempt(
        self, organization_id: str, task_id: str, task_attempt_number: int, call_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM desktop_agent_call_attempts
                   WHERE organization_id = ? AND task_id = ?
                   AND task_attempt_number = ? AND call_key = ?""",
                (organization_id, task_id, int(task_attempt_number), call_key),
            ).fetchone()
        return dict(row) if row else None

    def list_call_attempts(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agent_call_attempts WHERE task_id = ? "
                "ORDER BY task_attempt_number, call_key",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_call_attempts_by_organization(
        self, organization_id: str
    ) -> list[dict[str, Any]]:
        """PS1-13K: one bounded read for the whole org (kills tree N+1).

        Organization scope implies project scope: organizations are single
        project (FK + build_run_tree org/project check); callers must verify
        the organization belongs to the requesting project first.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agent_call_attempts WHERE organization_id = ? "
                "ORDER BY task_id, task_attempt_number, call_key",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def uncertain_call_attempts(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_call_attempts
                   WHERE organization_id = ? AND recovery_disposition = ?
                   ORDER BY task_id, call_key""",
                (organization_id, "UNKNOWN_EXTERNAL_OUTCOME"),
            ).fetchall()
        return [dict(row) for row in rows]

    def settle_call_attempts(
        self,
        *,
        organization_id: str,
        task_id: str,
        task_attempt_number: int,
        include_uncommitted: bool = False,
    ) -> int:
        """Advance closable call attempts of ONE task attempt to SETTLED.

        Closable rows are those with a durably committed response, plus rows
        that never crossed the dispatch boundary (known local outcome). Rows
        dispatched without a committed response stay at their uncertain-capable
        state unless *include_uncommitted* is set - settlement must never
        fabricate an authoritative outcome for an ambiguous external call.
        Forward-only: already-SETTLED rows are untouched. Returns the number
        of rows advanced.
        """

        now = _now()
        closability = (
            "AND state <> 'SETTLED'"
            if include_uncommitted
            else """AND state <> 'SETTLED'
                   AND (response_committed_at IS NOT NULL
                        OR dispatch_intent_at IS NULL)"""
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE desktop_agent_call_attempts
                   SET state = 'SETTLED',
                       recovery_disposition = 'TERMINAL',
                       settled_at = COALESCE(settled_at, ?)
                   WHERE organization_id = ? AND task_id = ?
                   AND task_attempt_number = ? """
                + closability,
                (now, organization_id, task_id, int(task_attempt_number)),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Reservations (mutually exclusive state model)

    def create_reservation(
        self,
        *,
        reservation_id: str,
        organization_id: str,
        task_id: str,
        attempt: int,
        amount_usd: float,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO desktop_agent_reservations (
                    reservation_id, organization_id, task_id, attempt, amount_usd,
                    status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (reservation_id, organization_id, task_id, int(attempt),
                 float(amount_usd), _now()),
            )
        return self.get_reservation(reservation_id)

    def get_reservation(self, reservation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_agent_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return dict(row) if row else None

    def transition_reservation(
        self,
        reservation_id: str,
        new_status: str,
        *,
        settled_actual_usd: float | None = None,
        integrity_violation: bool = False,
    ) -> dict[str, Any]:
        """Atomically transition a reservation between contract states.

        ACTIVE -> UNCERTAIN (dispatch uncertainty)
        ACTIVE -> SETTLED (actual cost known)
        ACTIVE -> RELEASED (clean pre-dispatch interruption)
        UNCERTAIN -> SETTLED (recovered committed response)
        """

        if new_status not in RESERVATION_STATUSES:
            raise ValueError(f"unknown reservation status: {new_status}")
        now = _now()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT status FROM desktop_agent_reservations "
                "WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"unknown reservation: {reservation_id}")
            if current["status"] == "uncertain" and new_status == "released":
                raise ValueError(
                    f"reservation {reservation_id} holds UNCERTAIN exposure; "
                    f"release is not permitted (zero lost reservations)"
                )
            cursor = connection.execute(
                """UPDATE desktop_agent_reservations
                   SET status = ?,
                       settled_actual_usd = COALESCE(?, settled_actual_usd),
                       integrity_violation = CASE WHEN ? THEN 1 ELSE integrity_violation END,
                       settled_at = CASE WHEN ? IN ('settled', 'released')
                                         THEN COALESCE(settled_at, ?) ELSE settled_at END
                   WHERE reservation_id = ? AND status IN ('active', 'uncertain')""",
                (new_status, settled_actual_usd, 1 if integrity_violation else 0,
                 new_status, now, reservation_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(
                    f"reservation {reservation_id} is already terminal"
                )
        return self.get_reservation(reservation_id)

    def organization_budget_position(self, organization_id: str) -> dict[str, float]:
        """Hard-budget invariant position: incurred + active + uncertain."""

        with self._connect() as connection:
            rows = connection.execute(
                """SELECT status, amount_usd, settled_actual_usd
                   FROM desktop_agent_reservations WHERE organization_id = ?""",
                (organization_id,),
            ).fetchall()
        settled = sum(
            (r["settled_actual_usd"] or 0.0) if r["status"] == "settled"
            else 0.0
            for r in rows
        )
        active = sum(r["amount_usd"] for r in rows if r["status"] == "active")
        uncertain = sum(r["amount_usd"] for r in rows if r["status"] == "uncertain")
        return {
            "settled": settled,
            "active": active,
            "uncertain": uncertain,
            "total_committed": settled + active + uncertain,
        }


    # ------------------------------------------------------------------
    # Verifier verdicts (sealed independence; UNIQUE(task_id, verifier_id))

    def record_verdict(
        self,
        *,
        organization_id: str,
        task_id: str,
        task_attempt_number: int,
        verifier_id: str,
        verdict: str,
        summary: str,
        evidence_refs_json: list[str],
        cost_usd: float = 0.0,
        call_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one verifier verdict for ONE task attempt.

        Identity is (organization_id, task_id, task_attempt_number,
        verifier_id): the same verifier on a later attempt gets a distinct
        verdict row; old attempt evidence is never overwritten.
        """

        verdict_id = f"verdict-{uuid_hex()}"
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO desktop_agent_verification_verdicts (
                    id, organization_id, task_id, task_attempt_number,
                    verifier_id, call_attempt_id, verdict, summary,
                    evidence_refs_json, cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (verdict_id, organization_id, task_id,
                 int(task_attempt_number), verifier_id, call_attempt_id,
                 verdict, summary[:400], json_dumps(evidence_refs_json),
                 float(cost_usd), created_at),
            )
        return {
            "id": verdict_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "task_attempt_number": int(task_attempt_number),
            "verifier_id": verifier_id,
            "call_attempt_id": call_attempt_id,
            "verdict": verdict,
            "summary": summary[:400],
            "evidence_refs_json": evidence_refs_json,
            "cost_usd": float(cost_usd),
            "created_at": created_at,
        }

    def verdict_by_task_and_verifier(
        self, task_id: str, verifier_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_agent_verification_verdicts "
                "WHERE task_id = ? AND verifier_id = ?",
                (task_id, verifier_id),
            ).fetchone()
        return dict(row) if row else None

    def verdicts_by_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agent_verification_verdicts "
                "WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def verdicts_by_organization(
        self, organization_id: str
    ) -> list[dict[str, Any]]:
        """PS1-13K: one bounded read for the whole org (kills tree N+1)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agent_verification_verdicts "
                "WHERE organization_id = ? ORDER BY task_id, created_at",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def verdicts_by_attempt(
        self, organization_id: str, task_id: str, task_attempt_number: int
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_verification_verdicts
                   WHERE organization_id = ? AND task_id = ?
                   AND task_attempt_number = ? ORDER BY verifier_id""",
                (organization_id, task_id, int(task_attempt_number)),
            ).fetchall()
        return [dict(row) for row in rows]

    def verdicts_by_attempt(
        self, organization_id: str, task_id: str, task_attempt_number: int
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_verification_verdicts
                   WHERE organization_id = ? AND task_id = ?
                   AND task_attempt_number = ? ORDER BY verifier_id""",
                (organization_id, task_id, int(task_attempt_number)),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Listing helpers (A2.1C startup reconciliation)

    def list_organization_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT organization_id FROM desktop_agent_organizations "
                "ORDER BY organization_id"
            ).fetchall()
        return [row["organization_id"] for row in rows]

    def find_active_reservation(
        self, organization_id: str, task_id: str, attempt: int
    ) -> dict[str, Any] | None:
        """Existing ACTIVE reservation for one task attempt, if any (A2.1D)."""

        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM desktop_agent_reservations
                   WHERE organization_id = ? AND task_id = ? AND attempt = ?
                   AND status = 'active' ORDER BY created_at LIMIT 1""",
                (organization_id, task_id, int(attempt)),
            ).fetchone()
        return dict(row) if row else None

    def list_reservations(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM desktop_agent_reservations
                   WHERE organization_id = ? ORDER BY task_id, attempt""",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex


def json_dumps(value: list[str] | dict[str, Any] | None) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads_or_none(raw: str | None) -> Any:
    import json

    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None

