"""A2.1C startup reconciliation for durable Agent Fabric state.

Reconciles interrupted Agent Fabric executions from the canonical amca.db
BEFORE new Agent Fabric scheduling is permitted. Pure classification and
authoritative local settlement: reconciliation makes ZERO model-provider
calls and never tests whether an ambiguous request executed by resending it.

Frozen recovery matrix (A2 contract + A2.1B acceptance):

- PLANNED / RESERVED (no dispatch intent)  -> SAFE_TO_RESUME; no second
  reservation is created; continuation stays a runtime decision (A2.1D).
- DISPATCH_INTENT_DURABLE / DISPATCHED / RESPONSE_RECEIVED (no committed
  response) -> UNKNOWN_EXTERNAL_OUTCOME: no redispatch, no reservation
  release, no downstream success; the linked reservation becomes/remains
  UNCERTAIN and keeps counting against budget.
- RESPONSE_COMMITTED -> the persisted payload is authoritative; it is
  reconstructed for later continuation without a provider call, and the
  linked reservation settles only when the attempt has no uncertain rows.
- SETTLED -> terminal; consistency verification only.

Idempotency contract: reconcile(reconcile(state)) == reconcile(state) for
externally unchanged durable state - every write is a guarded transition
from a non-final state, so repeated startups change nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from amca.agent_recovery import AgentRecoveryStore

# Task-level recovery classifications (explicit, never inferred).
TASK_RECOVERY_RESUMABLE = "RESUMABLE"
TASK_RECOVERY_BLOCKED_UNCERTAIN = "BLOCKED_UNCERTAIN_EXTERNAL_OUTCOME"
TASK_RECOVERY_RECOVERED_COMMITTED = "RECOVERED_COMMITTED_RESULT"
TASK_RECOVERY_TERMINAL = "TERMINAL"
TASK_RECOVERY_INTEGRITY_ERROR = "RECOVERY_INTEGRITY_ERROR"

_UNKNOWN_STATES = {"DISPATCH_INTENT_DURABLE", "DISPATCHED", "RESPONSE_RECEIVED"}
_BUDGET_EPSILON = 1e-9


class AgentFabricSchedulingBlocked(RuntimeError):
    """Raised when Agent Fabric scheduling is attempted while the startup
    reconciliation barrier has not reached READY."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SchedulingBarrier:
    """Fail-closed gate between process start and paid Agent Fabric work.

    Lifecycle: RECONCILING from construction until reconciliation completes
    successfully (READY). Any reconciliation or integrity failure leaves the
    barrier CLOSED permanently for this process - scheduling must not bypass
    the gate by falling back to 'start anyway'.
    """

    RECONCILING = "reconciling"
    READY = "ready"
    CLOSED = "closed"

    def __init__(self) -> None:
        self._state = self.RECONCILING
        self._reason: str | None = None
        self.transitions: list[tuple[str, str | None, str]] = [
            ("reconciling", None, _now())
        ]

    @property
    def state(self) -> str:
        return self._state

    @property
    def reason(self) -> str | None:
        return self._reason

    def mark_ready(self) -> None:
        if self._state != self.RECONCILING:
            raise ValueError(
                f"cannot mark ready from state {self._state!r}"
            )
        self._state = self.READY
        self.transitions.append(("ready", None, _now()))

    def mark_closed(self, reason: str) -> None:
        if self._state == self.READY:
            raise ValueError("cannot close a barrier that already opened")
        self._state = self.CLOSED
        self._reason = reason[:400]
        self.transitions.append(("closed", self._reason, _now()))

    def ensure_schedulable(self) -> None:
        if self._state != self.READY:
            detail = f" (reason: {self._reason})" if self._reason else ""
            raise AgentFabricSchedulingBlocked(
                f"Agent Fabric scheduling is blocked during startup "
                f"reconciliation (state={self._state}{detail})"
            )


def reconcile_agent_fabric_state(store: AgentRecoveryStore) -> dict[str, Any]:
    """Classify and reconcile all durable Agent Fabric state.

    Returns a bounded summary dict with per-disposition counters and any
    integrity errors found. Zero provider calls are made.
    """

    summary: dict[str, int] = {
        "organizations_scanned": 0,
        "tasks_scanned": 0,
        "attempts_scanned": 0,
        "safe_to_resume": 0,
        "committed_recovered": 0,
        "unknown_external_outcome": 0,
        "settlements_completed": 0,
        "uncertain_exposures": 0,
        "integrity_errors": 0,
    }
    integrity_errors: list[dict[str, str]] = []

    def record_integrity(kind: str, detail: str) -> None:
        integrity_errors.append({"kind": kind, "detail": detail[:400]})
        summary["integrity_errors"] += 1

    for organization_id in store.list_organization_ids():
        organization = store.get_organization(organization_id)
        if organization is None:
            continue
        summary["organizations_scanned"] += 1
        reservations = store.list_reservations(organization_id)
        task_ids = [t["id"] for t in store.list_tasks_by_organization(organization_id)]

        seen_active_reservations: dict[tuple[str, int], int] = {}
        for reservation in reservations:
            if reservation["status"] == "active":
                key = (reservation["task_id"], int(reservation["attempt"]))
                seen_active_reservations[key] = (
                    seen_active_reservations.get(key, 0) + 1
                )
        for (task_id, attempt), count in sorted(seen_active_reservations.items()):
            if count > 1:
                record_integrity(
                    "multiple_active_reservations",
                    f"org={organization_id} task={task_id} attempt={attempt} "
                    f"has {count} ACTIVE reservations",
                )

        for task_id in task_ids:
            summary["tasks_scanned"] += 1
            _reconcile_task(
                store,
                organization,
                task_id,
                reservations,
                summary,
                record_integrity,
            )

        _verify_budget_position(store, organization, record_integrity)

    return {
        "summary": summary,
        "integrity_errors": integrity_errors,
        "reconciled_at": _now(),
    }


def _attempt_rows(rows: list[dict[str, Any]], attempt: int) -> list[dict[str, Any]]:
    return [row for row in rows if int(row["task_attempt_number"]) == attempt]


def _classify_rows(rows: list[dict[str, Any]]) -> str:
    """Aggregate one task-attempt's call rows into the frozen matrix."""

    states = {row["state"] for row in rows}
    if not states:
        # Admitted but never journalled: nothing crossed the boundary.
        return TASK_RECOVERY_RESUMABLE
    if states & _UNKNOWN_STATES:
        # Uncertainty dominates: one ambiguous sibling blocks the attempt.
        return TASK_RECOVERY_BLOCKED_UNCERTAIN
    if "RESPONSE_COMMITTED" in states:
        return TASK_RECOVERY_RECOVERED_COMMITTED
    if states == {"SETTLED"}:
        return TASK_RECOVERY_TERMINAL
    # Only PLANNED/RESERVED rows remain.
    return TASK_RECOVERY_RESUMABLE


def _persist_task_disposition(store: AgentRecoveryStore, task_id: str, classification: str) -> None:
    uncertain = classification == TASK_RECOVERY_BLOCKED_UNCERTAIN
    store.set_task_recovery(task_id, uncertain=uncertain, disposition=classification)


def _reconcile_task(
    store: AgentRecoveryStore,
    organization: dict[str, Any],
    task_id: str,
    reservations: list[dict[str, Any]],
    summary: dict[str, int],
    record_integrity: Any,
) -> None:
    organization_id = organization["organization_id"]
    if store.get_task(task_id) is None:
        record_integrity("missing_task", f"task={task_id} referenced by call rows")
        return

    rows = store.list_call_attempts(task_id)
    summary["attempts_scanned"] += len({int(r["task_attempt_number"]) for r in rows})

    classifications = {
        attempt: _classify_rows(_attempt_rows(rows, attempt))
        for attempt in sorted({int(r["task_attempt_number"]) for r in rows})
    }
    for attempt, classification in classifications.items():
        if classification == TASK_RECOVERY_RESUMABLE:
            summary["safe_to_resume"] += 1
        elif classification == TASK_RECOVERY_BLOCKED_UNCERTAIN:
            summary["unknown_external_outcome"] += 1

    # Committed results: verify payload presence and reconstruct availability.
    integrity_violation = False
    for attempt, classification in classifications.items():
        if classification == TASK_RECOVERY_RECOVERED_COMMITTED:
            summary["committed_recovered"] += 1
        if classification not in (
            TASK_RECOVERY_RECOVERED_COMMITTED,
            TASK_RECOVERY_TERMINAL,
        ):
            continue
        attempt_rows = _attempt_rows(rows, attempt)
        missing_payload = [
            row["call_key"]
            for row in attempt_rows
            if row["state"] == "RESPONSE_COMMITTED" and not row["response_payload"]
        ]
        if missing_payload:
            record_integrity(
                "committed_without_payload",
                f"org={organization_id} task={task_id} attempt={attempt} "
                f"calls={','.join(sorted(missing_payload))}",
            )
            integrity_violation = True

    overall = (
        TASK_RECOVERY_INTEGRITY_ERROR
        if integrity_violation
        else _overall_task_classification(classifications, rows)
    )
    _persist_task_disposition(store, task_id, overall)

    _reconcile_task_reservations(
        store,
        organization_id,
        task_id,
        rows,
        classifications,
        reservations,
        summary,
        record_integrity,
    )
    # Independent financial settlement runs for every task with durably
    # committed calls; it self-guards against double settlement.
    _maybe_complete_settlement(
        store,
        task_id,
        rows,
        classifications,
        reservations,
        summary,
    )


def _overall_task_classification(
    classifications: dict[int, str], rows: list[dict[str, Any]]
) -> str:
    if not classifications:
        return TASK_RECOVERY_RESUMABLE
    values = list(classifications.values())
    if TASK_RECOVERY_INTEGRITY_ERROR in values:
        return TASK_RECOVERY_INTEGRITY_ERROR
    if TASK_RECOVERY_BLOCKED_UNCERTAIN in values:
        return TASK_RECOVERY_BLOCKED_UNCERTAIN
    if all(value == TASK_RECOVERY_TERMINAL for value in values):
        return TASK_RECOVERY_TERMINAL
    if TASK_RECOVERY_RECOVERED_COMMITTED in values:
        return TASK_RECOVERY_RECOVERED_COMMITTED
    return TASK_RECOVERY_RESUMABLE


def _reservation_for(
    reservations: list[dict[str, Any]], task_id: str, attempt: int
) -> dict[str, Any] | None:
    matches = [
        r for r in reservations
        if r["task_id"] == task_id and int(r["attempt"]) == attempt
    ]
    return matches[0] if matches else None


def _reconcile_task_reservations(
    store: AgentRecoveryStore,
    organization_id: str,
    task_id: str,
    rows: list[dict[str, Any]],
    classifications: dict[int, str],
    reservations: list[dict[str, Any]],
    summary: dict[str, int],
    record_integrity: Any,
) -> None:
    for attempt, classification in classifications.items():
        reservation = _reservation_for(reservations, task_id, attempt)
        if reservation is None:
            continue
        status = reservation["status"]
        attempt_rows = _attempt_rows(rows, attempt)

        if status == "released":
            if any(row["state"] in _UNKNOWN_STATES for row in attempt_rows):
                record_integrity(
                    "released_with_unresolved_dispatch",
                    f"org={organization_id} task={task_id} attempt={attempt} "
                    "RELEASED reservation coexists with unresolved dispatched calls",
                )
            continue
        if status == "settled":
            continue

        if classification == TASK_RECOVERY_BLOCKED_UNCERTAIN:
            if status == "active":
                store.transition_reservation(reservation["reservation_id"], "uncertain")
            summary["uncertain_exposures"] += 1
        elif classification == TASK_RECOVERY_RESUMABLE and status == "active":
            # SAFE_TO_RESUME: keep exactly this reservation active; the
            # normal resume policy (runtime/A2.1D) decides continuation.
            pass


def _maybe_complete_settlement(
    store: AgentRecoveryStore,
    task_id: str,
    rows: list[dict[str, Any]],
    classifications: dict[int, str],
    reservations: list[dict[str, Any]],
    summary: dict[str, int],
) -> None:
    """Finish financial settlement only from authoritative durable evidence."""

    for attempt, classification in classifications.items():
        reservation = _reservation_for(reservations, task_id, attempt)
        if reservation is None or reservation["status"] in ("settled", "released"):
            continue
        attempt_rows = _attempt_rows(rows, attempt)
        # Independent settlement: every durably COMMITTED call closes its
        # authoritative cost even when sibling calls of the same attempt
        # remain UNCERTAIN. Uncertain siblings keep their exposure
        # budget-accounted; they block semantic continuation, never
        # financial truth.
        closable_rows = [
            row
            for row in attempt_rows
            if row["state"] in ("RESPONSE_COMMITTED", "SETTLED")
        ]
        if not closable_rows:
            continue
        # Financial close happens once: an already-terminal reservation needs
        # nothing, and an UNCERTAIN reservation is only revisited when a NEW
        # closable row appeared (which cannot happen without runtime activity).
        pending_rows = [
            row for row in closable_rows if row["state"] != "SETTLED"
        ]
        if not pending_rows and reservation["status"] != "active":
            continue
        actual_cost = sum(
            float(row["actual_cost_usd"] or 0.0) for row in closable_rows
        )
        uncertain_remaining = len(attempt_rows) - len(closable_rows)
        new_status = "uncertain" if uncertain_remaining else "settled"
        store.transition_reservation(
            reservation["reservation_id"],
            new_status,
            settled_actual_usd=actual_cost,
        )
        store.settle_call_attempts(
            organization_id=reservation["organization_id"],
            task_id=task_id,
            task_attempt_number=attempt,
            include_uncommitted=False,
        )
        summary["settlements_completed"] += 1


def _verify_budget_position(
    store: AgentRecoveryStore,
    organization: dict[str, Any],
    record_integrity: Any,
) -> None:
    """settled + active + uncertain <= configured budget, counted once."""

    organization_id = organization["organization_id"]
    position = store.organization_budget_position(organization_id)
    budget = float(organization["budget_usd"])
    if position["total_committed"] > budget + _BUDGET_EPSILON:
        record_integrity(
            "budget_invariant_violation",
            f"org={organization_id} committed="
            f"{position['total_committed']:.6f} > budget={budget:.6f}",
        )
    # Fresh read: reconciliation may have transitioned reservations above.
    current_reservations = store.list_reservations(organization_id)
    counted = position["active"] + position["uncertain"]
    reserved_once = sum(
        float(r["amount_usd"])
        for r in current_reservations
        if r["status"] in ("active", "uncertain")
    )
    if abs(counted - reserved_once) > _BUDGET_EPSILON:
        record_integrity(
            "exposure_counted_more_than_once",
            f"org={organization_id} position={counted:.6f} "
            f"reservations={reserved_once:.6f}",
        )


def reconstruct_committed_result(
    store: AgentRecoveryStore,
    *,
    task_id: str,
    task_attempt_number: int,
    call_key: str | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct durably committed results WITHOUT any adapter invocation.

    Preserves call identity, adapter provenance, task attempt, response
    payload, and cost metadata as persisted at RESPONSE_COMMITTED time.
    """

    results = []
    for row in store.list_call_attempts(task_id):
        if int(row["task_attempt_number"]) != int(task_attempt_number):
            continue
        if call_key is not None and row["call_key"] != call_key:
            continue
        if row["state"] not in ("RESPONSE_COMMITTED", "SETTLED"):
            continue
        results.append({
            "call_key": row["call_key"],
            "call_attempt_id": row["call_attempt_id"],
            "task_attempt_number": int(row["task_attempt_number"]),
            "adapter_id": row["adapter_id"],
            "executor_kind": row["executor_kind"],
            "state": row["state"],
            "response_payload": row["response_payload"],
            "actual_cost_usd": float(row["actual_cost_usd"] or 0.0),
            "reserved_usd": float(row["reserved_usd"] or 0.0),
            "response_committed_at": row["response_committed_at"],
        })
    return results


MAX_SUMMARY_LINES = 200


def append_reconciliation_summary(path: Path, payload: dict[str, Any]) -> None:
    """Persist one bounded JSONL reconciliation summary line (no secrets)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()[-(MAX_SUMMARY_LINES - 1):]
    lines.append(json.dumps(payload, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
