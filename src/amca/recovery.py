"""Crash-safe, offline recovery workflows for persisted AMCA projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain import ProjectState
from .state import StateManager
from .storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class RecoveryFailure:
    """One durable event that could not be replayed safely."""

    event_id: str
    error_type: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Observable result of one local recovery attempt."""

    project_id: str
    start_version: int
    end_version: int
    pending_events_found: int
    events_applied: int
    events_reconciled: int
    dry_run: bool
    state: ProjectState
    pending_event_ids: tuple[str, ...] = field(default_factory=tuple)
    failures: tuple[RecoveryFailure, ...] = field(default_factory=tuple)

    @property
    def completed(self) -> bool:
        return not self.failures and (
            self.dry_run
            or self.events_applied + self.events_reconciled
            == self.pending_events_found
        )

    def to_dict(self, *, include_state: bool = False) -> dict[str, Any]:
        document: dict[str, Any] = {
            "project_id": self.project_id,
            "start_version": self.start_version,
            "end_version": self.end_version,
            "pending_events_found": self.pending_events_found,
            "pending_event_ids": list(self.pending_event_ids),
            "events_applied": self.events_applied,
            "events_reconciled": self.events_reconciled,
            "dry_run": self.dry_run,
            "completed": self.completed,
            "failures": [failure.to_dict() for failure in self.failures],
            "model_calls_restarted": False,
            "external_actions_replayed": False,
            "safety_note": (
                "Recovery replays only already-durable semantic events. It does not "
                "restart model calls, charge providers, or repeat external actions."
            ),
            "state_summary": {
                "version": self.state.version,
                "status": self.state.status,
                "goal_satisfied": self.state.goal_satisfied,
                "unresolved_disagreement_count": len(
                    self.state.unresolved_disagreements
                ),
                "api_cost_usd": self.state.cost_state.api_cost_usd,
                "local_compute_seconds": (
                    self.state.cost_state.local_compute_seconds
                ),
            },
        }
        if include_state:
            document["state"] = self.state.to_dict()
        return document


class ProjectNotFoundError(KeyError):
    """Raised when a recovery target has no persisted snapshot."""


def recover_project(
    store: SQLiteStore,
    project_id: str,
    *,
    dry_run: bool = False,
) -> RecoveryReport:
    """Restore a snapshot and replay its pending events in durable order.

    The operation is deliberately local and narrow. Each successful replay is
    persisted atomically by :class:`StateManager`. Replay stops at the first
    invalid event so later events cannot overtake a failed predecessor.
    """

    if not project_id.strip():
        raise ValueError("project_id must not be blank")
    resumed = store.resume(project_id)
    if resumed is None:
        raise ProjectNotFoundError(f"project {project_id!r} was not found")
    state, pending_events = resumed
    start_version = state.version
    if dry_run:
        return RecoveryReport(
            project_id=project_id,
            start_version=start_version,
            end_version=start_version,
            pending_events_found=len(pending_events),
            events_applied=0,
            events_reconciled=0,
            dry_run=True,
            state=state,
            pending_event_ids=tuple(event.id for event in pending_events),
        )

    manager = StateManager(state, store=store)
    applied = 0
    reconciled = 0
    failures: list[RecoveryFailure] = []
    for event in pending_events:
        prior_version = manager.version
        try:
            manager.apply(event)
            if manager.version == prior_version:
                # The snapshot already contains the identical event, but the
                # queue marker was not committed before a crash.
                store.mark_event_applied(event.id, manager.version)
                reconciled += 1
            else:
                applied += 1
        except Exception as exc:
            failures.append(
                RecoveryFailure(
                    event_id=event.id,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            break

    return RecoveryReport(
        project_id=project_id,
        start_version=start_version,
        end_version=manager.version,
        pending_events_found=len(pending_events),
        events_applied=applied,
        events_reconciled=reconciled,
        dry_run=False,
        state=manager.snapshot(),
        pending_event_ids=tuple(event.id for event in pending_events),
        failures=tuple(failures),
    )


__all__ = [
    "ProjectNotFoundError",
    "RecoveryFailure",
    "RecoveryReport",
    "recover_project",
]
