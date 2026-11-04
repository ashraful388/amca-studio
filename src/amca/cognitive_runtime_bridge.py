"""AMCA Studio v0.8.0 Cognitive Workflow Runtime Bridge (AG-AMCA-STUDIO-V0.8.0).

Binds the Cognitive Workflow Loop to controlled runtime execution pathways:
1. Coordinates runtime execution sessions for multi-model collaboration architectures.
2. Captures structured, evidence-only runtime records with zero hidden reasoning.
3. Enforces governance gate verification before execution dispatch.
4. Preserves 100% decoupling from Model Router algorithms and Agent Fabric boundaries.
5. 100% local SQLite persistence, workspace-isolated, offline-first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Literal, Sequence
from uuid import uuid4

from amca.cognitive_loop_orchestrator import (
    CognitiveWorkflowOrchestrator,
    CognitiveWorkflowRecord,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


FORBIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "internal_thoughts",
    "raw_thoughts",
    "private_deliberation",
    "cot",
}


def sanitize_evidence_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Ensures no hidden reasoning or private chain-of-thought keys are persisted."""
    clean: dict[str, Any] = {}
    for k, v in facts.items():
        if k.lower() in FORBIDDEN_REASONING_KEYS:
            continue
        clean[k] = v
    return clean


RuntimeSessionStatus = Literal[
    "INITIALIZED",
    "GOVERNANCE_VERIFIED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceRecord:
    evidence_id: str
    session_id: str
    workflow_id: str
    workspace_id: str
    step_index: int
    event_type: str
    recorded_facts: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "step_index": self.step_index,
            "event_type": self.event_type,
            "recorded_facts": dict(self.recorded_facts),
            "timestamp": self.timestamp,
        }


@dataclass
class RuntimeSessionRecord:
    session_id: str
    workflow_id: str
    workspace_id: str
    execution_mode: str
    selected_models: tuple[str, ...]
    participating_agents: tuple[str, ...]
    governance_check_passed: bool = False
    governance_status_snapshot: dict[str, Any] = field(default_factory=dict)
    status: RuntimeSessionStatus = "INITIALIZED"
    artifacts_produced: tuple[str, ...] = ()
    error_message: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "execution_mode": self.execution_mode,
            "selected_models": list(self.selected_models),
            "participating_agents": list(self.participating_agents),
            "governance_check_passed": self.governance_check_passed,
            "governance_status_snapshot": dict(self.governance_status_snapshot),
            "status": self.status,
            "artifacts_produced": list(self.artifacts_produced),
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class CognitiveRuntimeBridgeStore:
    """Local SQLite persistence store for runtime execution sessions and evidence facts."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    execution_mode TEXT NOT NULL,
                    selected_models_json TEXT NOT NULL,
                    participating_agents_json TEXT NOT NULL,
                    governance_check_passed INTEGER NOT NULL,
                    governance_snapshot_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    recorded_facts_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_runtime_ws_session 
                ON runtime_sessions (workspace_id, session_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_runtime_evidence_sess 
                ON runtime_evidence (session_id, step_index);
                """
            )

    def save_session(self, session: RuntimeSessionRecord) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO runtime_sessions
                (session_id, workflow_id, workspace_id, execution_mode, selected_models_json, participating_agents_json, governance_check_passed, governance_snapshot_json, status, artifacts_json, error_message, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.workflow_id,
                    session.workspace_id,
                    session.execution_mode,
                    json.dumps(list(session.selected_models)),
                    json.dumps(list(session.participating_agents)),
                    1 if session.governance_check_passed else 0,
                    json.dumps(session.governance_status_snapshot),
                    session.status,
                    json.dumps(list(session.artifacts_produced)),
                    session.error_message,
                    session.started_at,
                    session.finished_at,
                ),
            )

    def get_session(self, *, session_id: str, workspace_id: str) -> RuntimeSessionRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT * FROM runtime_sessions
                WHERE session_id = ? AND workspace_id = ?
                """,
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None

            return RuntimeSessionRecord(
                session_id=str(row["session_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                execution_mode=str(row["execution_mode"]),
                selected_models=tuple(json.loads(row["selected_models_json"])),
                participating_agents=tuple(json.loads(row["participating_agents_json"])),
                governance_check_passed=bool(row["governance_check_passed"]),
                governance_status_snapshot=dict(json.loads(row["governance_snapshot_json"])),
                status=str(row["status"]),  # type: ignore[arg-type]
                artifacts_produced=tuple(json.loads(row["artifacts_json"])),
                error_message=row["error_message"],
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            )

    def record_evidence(self, evidence: RuntimeEvidenceRecord) -> None:
        sanitized = sanitize_evidence_facts(evidence.recorded_facts)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO runtime_evidence
                (evidence_id, session_id, workflow_id, workspace_id, step_index, event_type, recorded_facts_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.session_id,
                    evidence.workflow_id,
                    evidence.workspace_id,
                    evidence.step_index,
                    evidence.event_type,
                    json.dumps(sanitized),
                    evidence.timestamp,
                ),
            )

    def list_evidence(self, *, session_id: str, workspace_id: str) -> list[RuntimeEvidenceRecord]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT * FROM runtime_evidence
                WHERE session_id = ? AND workspace_id = ?
                ORDER BY step_index ASC
                """,
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchall()
            return [
                RuntimeEvidenceRecord(
                    evidence_id=str(r["evidence_id"]),
                    session_id=str(r["session_id"]),
                    workflow_id=str(r["workflow_id"]),
                    workspace_id=str(r["workspace_id"]),
                    step_index=int(r["step_index"]),
                    event_type=str(r["event_type"]),
                    recorded_facts=dict(json.loads(r["recorded_facts_json"])),
                    timestamp=str(r["timestamp"]),
                )
                for r in rows
            ]


class CognitiveRuntimeBridge:
    """Binds CognitiveWorkflowOrchestrator workflows to controlled multi-model execution sessions."""

    def __init__(
        self,
        *,
        store: CognitiveRuntimeBridgeStore,
        orchestrator: CognitiveWorkflowOrchestrator,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator

    def create_runtime_session(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        execution_mode: str = "adaptive_hybrid",
    ) -> RuntimeSessionRecord:
        """Initializes a runtime execution session bound to a cognitive workflow."""
        wf = self.orchestrator.store.get_workflow(workflow_id=workflow_id, workspace_id=workspace_id)
        if wf is None:
            raise KeyError(f"Workflow '{workflow_id}' not found in workspace '{workspace_id}'")

        models = wf.selection.selected_models if wf.selection else ()
        agents = wf.selection.selected_agents if wf.selection else ()

        session_id = f"rts-{uuid4().hex[:16]}"
        now_ts = _now()
        session = RuntimeSessionRecord(
            session_id=session_id,
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            execution_mode=str(execution_mode).strip(),
            selected_models=models,
            participating_agents=agents,
            governance_check_passed=False,
            status="INITIALIZED",
            started_at=now_ts,
        )
        self.store.save_session(session)

        # Log session start evidence
        self.store.record_evidence(
            RuntimeEvidenceRecord(
                evidence_id=f"ev-{uuid4().hex[:12]}",
                session_id=session_id,
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                step_index=0,
                event_type="SESSION_START",
                recorded_facts={
                    "execution_mode": execution_mode,
                    "models_count": len(models),
                    "agents_count": len(agents),
                },
                timestamp=now_ts,
            )
        )
        return session

    def verify_governance_gates(
        self,
        *,
        session_id: str,
        workspace_id: str,
        approvals_verified: bool,
        provider_health_status: str = "HEALTHY",
        knowledge_permission_outcome: str = "AUTHORIZED",
    ) -> RuntimeSessionRecord:
        """Verifies governance gates prior to execution dispatch."""
        session = self._require_session(session_id, workspace_id)
        if session.status != "INITIALIZED":
            raise ValueError(f"Cannot verify governance for session in status '{session.status}'")

        passed = (
            approvals_verified
            and provider_health_status in ("HEALTHY", "LOCAL_OFFLINE")
            and knowledge_permission_outcome in ("AUTHORIZED", "NOT_REQUESTED")
        )

        snapshot = {
            "approvals_verified": approvals_verified,
            "provider_health_status": provider_health_status,
            "knowledge_permission_outcome": knowledge_permission_outcome,
            "gate_passed": passed,
        }

        session.governance_check_passed = passed
        session.governance_status_snapshot = snapshot
        session.status = "GOVERNANCE_VERIFIED" if passed else "FAILED"
        if not passed:
            session.error_message = "Governance gate verification failed"
            session.finished_at = _now()

        self.store.save_session(session)

        # Record evidence fact
        self.store.record_evidence(
            RuntimeEvidenceRecord(
                evidence_id=f"ev-{uuid4().hex[:12]}",
                session_id=session_id,
                workflow_id=session.workflow_id,
                workspace_id=workspace_id,
                step_index=1,
                event_type="GOVERNANCE_GATE_VERIFIED",
                recorded_facts=snapshot,
                timestamp=_now(),
            )
        )
        return session

    def dispatch_execution(
        self,
        *,
        session_id: str,
        workspace_id: str,
        worker_callable: Callable[[], tuple[bool, tuple[str, ...], str | None]],
    ) -> RuntimeSessionRecord:
        """Dispatches execution across participating models and agents if governance passed."""
        session = self._require_session(session_id, workspace_id)
        if session.status != "GOVERNANCE_VERIFIED" or not session.governance_check_passed:
            raise RuntimeError(f"Session '{session_id}' has not passed governance gates")

        session.status = "RUNNING"
        self.store.save_session(session)

        self.store.record_evidence(
            RuntimeEvidenceRecord(
                evidence_id=f"ev-{uuid4().hex[:12]}",
                session_id=session_id,
                workflow_id=session.workflow_id,
                workspace_id=workspace_id,
                step_index=2,
                event_type="DISPATCH",
                recorded_facts={"execution_state": "DISPATCHED"},
                timestamp=_now(),
            )
        )

        try:
            success, artifacts, err = worker_callable()
            now_ts = _now()
            if success:
                session.status = "COMPLETED"
                session.artifacts_produced = tuple(artifacts)
                session.finished_at = now_ts
                self.store.save_session(session)

                self.store.record_evidence(
                    RuntimeEvidenceRecord(
                        evidence_id=f"ev-{uuid4().hex[:12]}",
                        session_id=session_id,
                        workflow_id=session.workflow_id,
                        workspace_id=workspace_id,
                        step_index=3,
                        event_type="SESSION_COMPLETE",
                        recorded_facts={
                            "success": True,
                            "artifacts_count": len(artifacts),
                            "artifacts": list(artifacts),
                        },
                        timestamp=now_ts,
                    )
                )
            else:
                session.status = "FAILED"
                session.error_message = err or "Execution failed"
                session.finished_at = now_ts
                self.store.save_session(session)

                self.store.record_evidence(
                    RuntimeEvidenceRecord(
                        evidence_id=f"ev-{uuid4().hex[:12]}",
                        session_id=session_id,
                        workflow_id=session.workflow_id,
                        workspace_id=workspace_id,
                        step_index=3,
                        event_type="SESSION_FAILED",
                        recorded_facts={"success": False, "error": err or "Execution failed"},
                        timestamp=now_ts,
                    )
                )
        except Exception as ex:
            now_ts = _now()
            session.status = "FAILED"
            session.error_message = str(ex)
            session.finished_at = now_ts
            self.store.save_session(session)

            self.store.record_evidence(
                RuntimeEvidenceRecord(
                    evidence_id=f"ev-{uuid4().hex[:12]}",
                    session_id=session_id,
                    workflow_id=session.workflow_id,
                    workspace_id=workspace_id,
                    step_index=3,
                    event_type="SESSION_FAILED",
                    recorded_facts={"success": False, "exception": str(ex)},
                    timestamp=now_ts,
                )
            )

        return session

    def cancel_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
        reason: str = "Operator cancelled session",
    ) -> RuntimeSessionRecord:
        """Cancels an active runtime session."""
        session = self._require_session(session_id, workspace_id)
        session.status = "CANCELLED"
        session.error_message = reason
        session.finished_at = _now()
        self.store.save_session(session)

        self.store.record_evidence(
            RuntimeEvidenceRecord(
                evidence_id=f"ev-{uuid4().hex[:12]}",
                session_id=session_id,
                workflow_id=session.workflow_id,
                workspace_id=workspace_id,
                step_index=99,
                event_type="SESSION_CANCELLED",
                recorded_facts={"reason": reason},
                timestamp=_now(),
            )
        )
        return session

    def _require_session(self, session_id: str, workspace_id: str) -> RuntimeSessionRecord:
        session = self.store.get_session(session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found in workspace '{workspace_id}'")
        return session

