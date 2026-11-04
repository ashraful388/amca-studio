"""AMCA Studio v0.8.0 Cognitive Runtime Observability & Recovery Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-RUNTIME-OBSERVABILITY-AND-RECOVERY-FOUNDATION-01

Features:
1. Append-only, auditable Runtime Event Stream.
2. High-fidelity Runtime Session Snapshots (facts only, zero subjective rankings).
3. Explicit Recovery State tracking (HEALTHY, DEGRADED, FAILED, AWAITING_RECOVERY, RECOVERED).
4. Component Failure Boundary (records facts and component metadata; strictly no cause prediction or auto-repair).
5. 100% decoupled from Model Router, Agent Fabric, and Cognitive Workflow Orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import uuid4

from amca.cognitive_runtime_bridge import (
    CognitiveRuntimeBridgeStore,
    FORBIDDEN_REASONING_KEYS,
    RuntimeSessionRecord,
    sanitize_evidence_facts,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


RuntimeRecoveryState = Literal[
    "HEALTHY",
    "DEGRADED",
    "FAILED",
    "AWAITING_RECOVERY",
    "RECOVERED",
]


@dataclass(frozen=True, slots=True)
class RuntimeEventStreamItem:
    event_id: str
    session_id: str
    workflow_id: str
    workspace_id: str
    seq_num: int
    event_type: str
    payload: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "seq_num": self.seq_num,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class RuntimeFailureRecord:
    failure_id: str
    session_id: str
    workflow_id: str
    workspace_id: str
    failed_component: str
    error_code: str
    error_message: str
    error_metadata: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "failed_component": self.failed_component,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_metadata": dict(self.error_metadata),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSessionSnapshot:
    session_id: str
    workflow_id: str
    workspace_id: str
    execution_mode: str
    status: str
    recovery_state: RuntimeRecoveryState
    participating_models: tuple[str, ...]
    participating_agents: tuple[str, ...]
    governance_passed: bool
    event_count: int
    failure_count: int
    latest_event_type: str | None
    artifacts: tuple[str, ...]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "execution_mode": self.execution_mode,
            "status": self.status,
            "recovery_state": self.recovery_state,
            "participating_models": list(self.participating_models),
            "participating_agents": list(self.participating_agents),
            "governance_passed": self.governance_passed,
            "event_count": self.event_count,
            "failure_count": self.failure_count,
            "latest_event_type": self.latest_event_type,
            "artifacts": list(self.artifacts),
            "timestamp": self.timestamp,
        }


class CognitiveRuntimeObservabilityStore:
    """Local SQLite persistence store for the runtime event stream, failure records, and recovery states."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_event_stream (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    seq_num INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_failures (
                    failure_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    failed_component TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    error_metadata_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_recovery_states (
                    session_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    recovery_state TEXT NOT NULL,
                    transition_reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_event_stream_sess_seq
                ON runtime_event_stream (session_id, seq_num);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_failures_sess
                ON runtime_failures (session_id, timestamp);
                """
            )

    def append_event(self, event: RuntimeEventStreamItem) -> None:
        sanitized = sanitize_evidence_facts(event.payload)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT INTO runtime_event_stream
                (event_id, session_id, workflow_id, workspace_id, seq_num, event_type, payload_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.workflow_id,
                    event.workspace_id,
                    event.seq_num,
                    event.event_type,
                    json.dumps(sanitized),
                    event.timestamp,
                ),
            )

    def list_events(self, *, session_id: str, workspace_id: str) -> list[RuntimeEventStreamItem]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM runtime_event_stream
                WHERE session_id = ? AND workspace_id = ?
                ORDER BY seq_num ASC
                """,
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchall()
            return [
                RuntimeEventStreamItem(
                    event_id=str(r["event_id"]),
                    session_id=str(r["session_id"]),
                    workflow_id=str(r["workflow_id"]),
                    workspace_id=str(r["workspace_id"]),
                    seq_num=int(r["seq_num"]),
                    event_type=str(r["event_type"]),
                    payload=dict(json.loads(r["payload_json"])),
                    timestamp=str(r["timestamp"]),
                )
                for r in rows
            ]

    def record_failure(self, failure: RuntimeFailureRecord) -> None:
        sanitized = sanitize_evidence_facts(failure.error_metadata)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT INTO runtime_failures
                (failure_id, session_id, workflow_id, workspace_id, failed_component, error_code, error_message, error_metadata_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    failure.failure_id,
                    failure.session_id,
                    failure.workflow_id,
                    failure.workspace_id,
                    failure.failed_component,
                    failure.error_code,
                    failure.error_message,
                    json.dumps(sanitized),
                    failure.timestamp,
                ),
            )

    def list_failures(self, *, session_id: str, workspace_id: str) -> list[RuntimeFailureRecord]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM runtime_failures
                WHERE session_id = ? AND workspace_id = ?
                ORDER BY timestamp ASC
                """,
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchall()
            return [
                RuntimeFailureRecord(
                    failure_id=str(r["failure_id"]),
                    session_id=str(r["session_id"]),
                    workflow_id=str(r["workflow_id"]),
                    workspace_id=str(r["workspace_id"]),
                    failed_component=str(r["failed_component"]),
                    error_code=str(r["error_code"]),
                    error_message=str(r["error_message"]),
                    error_metadata=dict(json.loads(r["error_metadata_json"])),
                    timestamp=str(r["timestamp"]),
                )
                for r in rows
            ]

    def set_recovery_state(
        self,
        *,
        session_id: str,
        workspace_id: str,
        recovery_state: RuntimeRecoveryState,
        reason: str,
    ) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO runtime_recovery_states
                (session_id, workspace_id, recovery_state, transition_reason, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(session_id).strip(),
                    str(workspace_id).strip(),
                    recovery_state,
                    reason,
                    _now(),
                ),
            )

    def get_recovery_state(self, *, session_id: str, workspace_id: str) -> RuntimeRecoveryState:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute(
                """
                SELECT recovery_state FROM runtime_recovery_states
                WHERE session_id = ? AND workspace_id = ?
                """,
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return "HEALTHY"
            return str(row[0])  # type: ignore[return-value]


class CognitiveRuntimeObserver:
    """Manages runtime session observation, event streams, failure boundary capture, and recovery states."""

    def __init__(
        self,
        *,
        store: CognitiveRuntimeObservabilityStore,
        bridge_store: CognitiveRuntimeBridgeStore,
    ) -> None:
        self.store = store
        self.bridge_store = bridge_store

    def emit_event(
        self,
        *,
        session_id: str,
        workspace_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEventStreamItem:
        """Appends a timestamped, ordered event to the session event stream."""
        session = self._require_session(session_id, workspace_id)
        existing_events = self.store.list_events(session_id=session_id, workspace_id=workspace_id)
        next_seq = len(existing_events)

        event = RuntimeEventStreamItem(
            event_id=f"evs-{uuid4().hex[:12]}",
            session_id=session_id,
            workflow_id=session.workflow_id,
            workspace_id=workspace_id,
            seq_num=next_seq,
            event_type=str(event_type).strip(),
            payload=payload or {},
            timestamp=_now(),
        )
        self.store.append_event(event)
        return event

    def record_component_failure(
        self,
        *,
        session_id: str,
        workspace_id: str,
        failed_component: str,
        error_code: str,
        error_message: str,
        error_metadata: dict[str, Any] | None = None,
    ) -> RuntimeFailureRecord:
        """Records a component failure occurrence without attempting autonomous repair."""
        session = self._require_session(session_id, workspace_id)
        failure = RuntimeFailureRecord(
            failure_id=f"fail-{uuid4().hex[:12]}",
            session_id=session_id,
            workflow_id=session.workflow_id,
            workspace_id=workspace_id,
            failed_component=str(failed_component).strip(),
            error_code=str(error_code).strip(),
            error_message=str(error_message).strip(),
            error_metadata=error_metadata or {},
            timestamp=_now(),
        )
        self.store.record_failure(failure)

        # Transition recovery state to DEGRADED or FAILED
        self.transition_recovery_state(
            session_id=session_id,
            workspace_id=workspace_id,
            target_state="FAILED",
            reason=f"Component '{failed_component}' failed: {error_code}",
        )

        # Also emit a runtime event for audit trail
        self.emit_event(
            session_id=session_id,
            workspace_id=workspace_id,
            event_type="COMPONENT_FAILURE_DETECTED",
            payload={
                "failed_component": failed_component,
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        return failure

    def transition_recovery_state(
        self,
        *,
        session_id: str,
        workspace_id: str,
        target_state: RuntimeRecoveryState,
        reason: str,
    ) -> None:
        """Transitions the recovery state tracking for a session."""
        self._require_session(session_id, workspace_id)
        self.store.set_recovery_state(
            session_id=session_id,
            workspace_id=workspace_id,
            recovery_state=target_state,
            reason=reason,
        )

    def capture_session_snapshot(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> RuntimeSessionSnapshot:
        """Captures a comprehensive, facts-only snapshot of runtime session state."""
        session = self._require_session(session_id, workspace_id)
        recovery_state = self.store.get_recovery_state(
            session_id=session_id, workspace_id=workspace_id
        )
        events = self.store.list_events(session_id=session_id, workspace_id=workspace_id)
        failures = self.store.list_failures(session_id=session_id, workspace_id=workspace_id)

        latest_event = events[-1].event_type if events else None

        return RuntimeSessionSnapshot(
            session_id=session.session_id,
            workflow_id=session.workflow_id,
            workspace_id=session.workspace_id,
            execution_mode=session.execution_mode,
            status=session.status,
            recovery_state=recovery_state,
            participating_models=session.selected_models,
            participating_agents=session.participating_agents,
            governance_passed=session.governance_check_passed,
            event_count=len(events),
            failure_count=len(failures),
            latest_event_type=latest_event,
            artifacts=session.artifacts_produced,
            timestamp=_now(),
        )

    def _require_session(self, session_id: str, workspace_id: str) -> RuntimeSessionRecord:
        session = self.bridge_store.get_session(session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found in workspace '{workspace_id}'")
        return session

