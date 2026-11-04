"""AMCA Studio v0.8.0 Governed Execution Plan Bridge Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-GOVERNED-EXECUTION-PLAN-BRIDGE-FOUNDATION-01

Core Principles:
1. Approved Plans Only (only APPROVED / READY_FOR_EXECUTION plans accepted; unapproved/rejected strictly blocked).
2. Execution Safety Gates (pre-handoff verification of approvals, resources, capabilities, locality, and provider health).
3. Handoff != Execution (coordination and audit handoff records; strictly zero autonomous execution).
4. Full Evidence Integration (audit references linked; zero chain-of-thought).
5. 100% decoupled from Model Router algorithms, Agent Fabric engine, and automatic execution policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import uuid4

from amca.adaptive_execution_planning import (
    AdaptiveExecutionPlanRecord,
    AdaptiveExecutionPlanningStore,
)
from amca.cognitive_runtime_bridge import (
    FORBIDDEN_REASONING_KEYS,
    sanitize_evidence_facts,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


HandoffStatus = Literal[
    "REQUESTED",
    "ACCEPTED",
    "REJECTED",
    "DISPATCHED",
    "CANCELLED",
    "FAILED",
]


@dataclass
class PlanExecutionHandoffRecord:
    handoff_id: str
    plan_id: str
    runtime_session_id: str
    workflow_id: str
    workspace_id: str
    requested_by: str
    governance_checks: dict[str, Any]
    status: HandoffStatus = "REQUESTED"
    rejection_reason: str | None = None
    cancellation_reason: str | None = None
    dispatched_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "plan_id": self.plan_id,
            "runtime_session_id": self.runtime_session_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "requested_by": self.requested_by,
            "governance_checks": dict(self.governance_checks),
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "cancellation_reason": self.cancellation_reason,
            "dispatched_at": self.dispatched_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GovernedExecutionPlanBridgeStore:
    """Local SQLite persistence store for plan execution handoff records."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_execution_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    runtime_session_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    governance_checks_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    cancellation_reason TEXT,
                    dispatched_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_handoffs_ws_plan 
                ON plan_execution_handoffs (workspace_id, plan_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_handoffs_sess 
                ON plan_execution_handoffs (runtime_session_id);
                """
            )

    def save_handoff(self, record: PlanExecutionHandoffRecord) -> None:
        sanitized = sanitize_evidence_facts(record.governance_checks)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO plan_execution_handoffs
                (handoff_id, plan_id, runtime_session_id, workflow_id, workspace_id, requested_by, governance_checks_json, status, rejection_reason, cancellation_reason, dispatched_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.handoff_id,
                    record.plan_id,
                    record.runtime_session_id,
                    record.workflow_id,
                    record.workspace_id,
                    record.requested_by,
                    json.dumps(sanitized),
                    record.status,
                    record.rejection_reason,
                    record.cancellation_reason,
                    record.dispatched_at,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get_handoff(self, *, handoff_id: str, workspace_id: str) -> PlanExecutionHandoffRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM plan_execution_handoffs WHERE handoff_id = ? AND workspace_id = ?",
                (str(handoff_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return PlanExecutionHandoffRecord(
                handoff_id=str(row["handoff_id"]),
                plan_id=str(row["plan_id"]),
                runtime_session_id=str(row["runtime_session_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                requested_by=str(row["requested_by"]),
                governance_checks=dict(json.loads(row["governance_checks_json"])),
                status=str(row["status"]),  # type: ignore[arg-type]
                rejection_reason=row["rejection_reason"],
                cancellation_reason=row["cancellation_reason"],
                dispatched_at=row["dispatched_at"],
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )


class GovernedExecutionPlanBridge:
    """Manages governed handoffs between approved execution plans and runtime execution sessions."""

    def __init__(
        self,
        *,
        store: GovernedExecutionPlanBridgeStore,
        planning_store: AdaptiveExecutionPlanningStore,
    ) -> None:
        self.store = store
        self.planning_store = planning_store

    def request_plan_execution_handoff(
        self,
        *,
        plan_id: str,
        workspace_id: str,
        requested_by: str,
        runtime_session_id: str,
        resources_valid: bool = True,
        capabilities_satisfied: bool = True,
        locality_satisfied: bool = True,
        provider_approvals_satisfied: bool = True,
    ) -> PlanExecutionHandoffRecord:
        """Evaluates execution safety gates and initiates a governed plan handoff."""
        plan = self.planning_store.get_plan(plan_id=plan_id, workspace_id=workspace_id)
        if plan is None:
            raise KeyError(f"Plan '{plan_id}' not found in workspace '{workspace_id}'")

        if plan.status not in ("APPROVED", "READY_FOR_EXECUTION"):
            raise RuntimeError(
                f"Cannot create execution handoff for plan in '{plan.status}' status; plan must be APPROVED or READY_FOR_EXECUTION"
            )

        # Evaluate safety gates
        approval_reference_exists = bool(plan.reviewed_by and plan.reviewed_at)
        all_passed = (
            approval_reference_exists
            and resources_valid
            and capabilities_satisfied
            and locality_satisfied
            and provider_approvals_satisfied
        )

        checks = {
            "approval_reference_exists": approval_reference_exists,
            "resources_valid": resources_valid,
            "capabilities_satisfied": capabilities_satisfied,
            "locality_satisfied": locality_satisfied,
            "provider_approvals_satisfied": provider_approvals_satisfied,
            "gate_passed": all_passed,
        }

        handoff_id = f"hndf-{uuid4().hex[:16]}"
        now_ts = _now()
        status: HandoffStatus = "ACCEPTED" if all_passed else "REJECTED"
        rejection_reason = None if all_passed else "One or more execution safety gates failed"

        record = PlanExecutionHandoffRecord(
            handoff_id=handoff_id,
            plan_id=plan_id,
            runtime_session_id=str(runtime_session_id).strip(),
            workflow_id=plan.workflow_id,
            workspace_id=workspace_id,
            requested_by=str(requested_by).strip(),
            governance_checks=checks,
            status=status,
            rejection_reason=rejection_reason,
            created_at=now_ts,
            updated_at=now_ts,
        )
        self.store.save_handoff(record)
        return record

    def dispatch_plan_handoff(
        self,
        *,
        handoff_id: str,
        workspace_id: str,
    ) -> PlanExecutionHandoffRecord:
        """Transitions an ACCEPTED handoff to DISPATCHED."""
        handoff = self._require_handoff(handoff_id, workspace_id)
        if handoff.status != "ACCEPTED":
            raise RuntimeError(
                f"Cannot dispatch handoff in '{handoff.status}' status; handoff must be ACCEPTED"
            )

        handoff.status = "DISPATCHED"
        handoff.dispatched_at = _now()
        handoff.updated_at = _now()
        self.store.save_handoff(handoff)
        return handoff

    def cancel_plan_handoff(
        self,
        *,
        handoff_id: str,
        workspace_id: str,
        reason: str = "Operator cancelled handoff",
    ) -> PlanExecutionHandoffRecord:
        """Records operator cancellation of a handoff."""
        handoff = self._require_handoff(handoff_id, workspace_id)
        handoff.status = "CANCELLED"
        handoff.cancellation_reason = reason
        handoff.updated_at = _now()
        self.store.save_handoff(handoff)
        return handoff

    def record_handoff_failure(
        self,
        *,
        handoff_id: str,
        workspace_id: str,
        failure_reason: str,
    ) -> PlanExecutionHandoffRecord:
        """Records execution handoff failure."""
        handoff = self._require_handoff(handoff_id, workspace_id)
        handoff.status = "FAILED"
        handoff.rejection_reason = failure_reason
        handoff.updated_at = _now()
        self.store.save_handoff(handoff)
        return handoff

    def _require_handoff(self, handoff_id: str, workspace_id: str) -> PlanExecutionHandoffRecord:
        handoff = self.store.get_handoff(handoff_id=handoff_id, workspace_id=workspace_id)
        if handoff is None:
            raise KeyError(f"Handoff '{handoff_id}' not found in workspace '{workspace_id}'")
        return handoff

