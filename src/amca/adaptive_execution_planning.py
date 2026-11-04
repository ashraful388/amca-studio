"""AMCA Studio v0.8.0 Adaptive Execution Planning Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-ADAPTIVE-EXECUTION-PLANNING-FOUNDATION-01

Core Principles:
1. Planning Artifacts only (strictly NO automatic or autonomous plan execution).
2. Approved Proposals Only (unapproved or rejected proposals cannot produce execution plans).
3. Human Plan Review Boundary (plans require explicit operator review and approval).
4. Evidence Traceability (every step references evidence origin IDs; zero hidden reasoning).
5. 100% decoupled from Model Router, Agent Fabric, and Runtime execution engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Sequence
from uuid import uuid4

from amca.adaptive_coordination_intelligence import (
    AdaptiveCoordinationStore,
    CoordinationProposalRecord,
)
from amca.cognitive_runtime_bridge import (
    FORBIDDEN_REASONING_KEYS,
    sanitize_evidence_facts,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


PlanStepStatus = Literal["PENDING", "IN_PROGRESS", "COMPLETED", "SKIPPED", "FAILED"]

PlanStatus = Literal[
    "DRAFT",
    "UNDER_REVIEW",
    "APPROVED",
    "REJECTED",
    "READY_FOR_EXECUTION",
    "SUPERSEDED",
]


@dataclass(frozen=True, slots=True)
class AdaptivePlanStep:
    step_id: str
    step_index: int
    title: str
    description: str
    assigned_agent_id: str | None
    assigned_model_id: str | None
    required_capabilities: tuple[str, ...]
    evidence_origin_ids: tuple[str, ...]
    status: PlanStepStatus = "PENDING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "title": self.title,
            "description": self.description,
            "assigned_agent_id": self.assigned_agent_id,
            "assigned_model_id": self.assigned_model_id,
            "required_capabilities": list(self.required_capabilities),
            "evidence_origin_ids": list(self.evidence_origin_ids),
            "status": self.status,
        }


@dataclass
class AdaptiveExecutionPlanRecord:
    plan_id: str
    proposal_id: str
    workflow_id: str
    workspace_id: str
    goal: str
    steps: tuple[AdaptivePlanStep, ...]
    status: PlanStatus = "DRAFT"
    review_notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "proposal_id": self.proposal_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "review_notes": self.review_notes,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AdaptiveExecutionPlanningStore:
    """Local SQLite persistence store for adaptive execution plans and steps."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS adaptive_execution_plans (
                    plan_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    review_notes TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS adaptive_execution_plan_steps (
                    step_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    assigned_agent_id TEXT,
                    assigned_model_id TEXT,
                    required_capabilities_json TEXT NOT NULL,
                    evidence_origin_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plans_ws_wf 
                ON adaptive_execution_plans (workspace_id, workflow_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_plan_steps_plan 
                ON adaptive_execution_plan_steps (plan_id, step_index);
                """
            )

    def save_plan(self, plan: AdaptiveExecutionPlanRecord) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO adaptive_execution_plans
                (plan_id, proposal_id, workflow_id, workspace_id, goal, status, review_notes, reviewed_by, reviewed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.proposal_id,
                    plan.workflow_id,
                    plan.workspace_id,
                    plan.goal,
                    plan.status,
                    plan.review_notes,
                    plan.reviewed_by,
                    plan.reviewed_at,
                    plan.created_at,
                    plan.updated_at,
                ),
            )
            # Replace steps
            db.execute("DELETE FROM adaptive_execution_plan_steps WHERE plan_id = ?", (plan.plan_id,))
            for step in plan.steps:
                db.execute(
                    """
                    INSERT INTO adaptive_execution_plan_steps
                    (step_id, plan_id, step_index, title, description, assigned_agent_id, assigned_model_id, required_capabilities_json, evidence_origin_ids_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        step.step_id,
                        plan.plan_id,
                        step.step_index,
                        step.title,
                        step.description,
                        step.assigned_agent_id,
                        step.assigned_model_id,
                        json.dumps(list(step.required_capabilities)),
                        json.dumps(list(step.evidence_origin_ids)),
                        step.status,
                    ),
                )

    def get_plan(self, *, plan_id: str, workspace_id: str) -> AdaptiveExecutionPlanRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM adaptive_execution_plans WHERE plan_id = ? AND workspace_id = ?",
                (str(plan_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None

            steps_rows = db.execute(
                "SELECT * FROM adaptive_execution_plan_steps WHERE plan_id = ? ORDER BY step_index ASC",
                (str(plan_id).strip(),),
            ).fetchall()

            steps = tuple(
                AdaptivePlanStep(
                    step_id=str(s["step_id"]),
                    step_index=int(s["step_index"]),
                    title=str(s["title"]),
                    description=str(s["description"]),
                    assigned_agent_id=str(s["assigned_agent_id"]) if s["assigned_agent_id"] else None,
                    assigned_model_id=str(s["assigned_model_id"]) if s["assigned_model_id"] else None,
                    required_capabilities=tuple(json.loads(s["required_capabilities_json"])),
                    evidence_origin_ids=tuple(json.loads(s["evidence_origin_ids_json"])),
                    status=str(s["status"]),  # type: ignore[arg-type]
                )
                for s in steps_rows
            )

            return AdaptiveExecutionPlanRecord(
                plan_id=str(row["plan_id"]),
                proposal_id=str(row["proposal_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                goal=str(row["goal"]),
                steps=steps,
                status=str(row["status"]),  # type: ignore[arg-type]
                review_notes=row["review_notes"],
                reviewed_by=row["reviewed_by"],
                reviewed_at=row["reviewed_at"],
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )


class AdaptiveExecutionPlanningEngine:
    """Provides deterministic proposal-to-plan conversion and human plan review governance."""

    def __init__(
        self,
        *,
        store: AdaptiveExecutionPlanningStore,
        coordination_store: AdaptiveCoordinationStore,
    ) -> None:
        self.store = store
        self.coordination_store = coordination_store

    def convert_proposal_to_plan(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
        goal: str,
        steps_spec: Sequence[dict[str, Any]],
    ) -> AdaptiveExecutionPlanRecord:
        """Converts an APPROVED proposal into an explicit execution plan. Rejects unapproved proposals."""
        proposal = self.coordination_store.get_proposal(
            proposal_id=proposal_id,
            workspace_id=workspace_id,
        )
        if proposal is None:
            raise KeyError(f"Proposal '{proposal_id}' not found in workspace '{workspace_id}'")

        if proposal.status != "APPROVED":
            raise RuntimeError(
                f"Cannot create execution plan from proposal in '{proposal.status}' status; proposal must be APPROVED"
            )

        plan_id = f"plan-{uuid4().hex[:16]}"
        now_ts = _now()

        parsed_steps: list[AdaptivePlanStep] = []
        for idx, spec in enumerate(steps_spec):
            step = AdaptivePlanStep(
                step_id=f"step-{uuid4().hex[:12]}",
                step_index=idx,
                title=str(spec.get("title", f"Step {idx + 1}")).strip(),
                description=str(spec.get("description", "")).strip(),
                assigned_agent_id=spec.get("assigned_agent_id"),
                assigned_model_id=spec.get("assigned_model_id"),
                required_capabilities=tuple(spec.get("required_capabilities", ())),
                evidence_origin_ids=tuple(spec.get("evidence_origin_ids", (proposal.proposal_id,))),
                status="PENDING",
            )
            parsed_steps.append(step)

        plan = AdaptiveExecutionPlanRecord(
            plan_id=plan_id,
            proposal_id=proposal_id,
            workflow_id=proposal.workflow_id,
            workspace_id=workspace_id,
            goal=str(goal).strip(),
            steps=tuple(parsed_steps),
            status="DRAFT",
            created_at=now_ts,
            updated_at=now_ts,
        )
        self.store.save_plan(plan)
        return plan

    def submit_plan_for_review(self, *, plan_id: str, workspace_id: str) -> AdaptiveExecutionPlanRecord:
        plan = self._require_plan(plan_id, workspace_id)
        if plan.status != "DRAFT":
            raise ValueError(f"Cannot submit plan in status '{plan.status}' for review")

        plan.status = "UNDER_REVIEW"
        plan.updated_at = _now()
        self.store.save_plan(plan)
        return plan

    def approve_plan(
        self,
        *,
        plan_id: str,
        workspace_id: str,
        reviewed_by: str,
        review_notes: str = "",
    ) -> AdaptiveExecutionPlanRecord:
        plan = self._require_plan(plan_id, workspace_id)
        if plan.status not in ("DRAFT", "UNDER_REVIEW"):
            raise ValueError(f"Cannot approve plan in status '{plan.status}'")

        plan.status = "APPROVED"
        plan.reviewed_by = str(reviewed_by).strip()
        plan.review_notes = str(review_notes).strip()
        plan.reviewed_at = _now()
        plan.updated_at = _now()
        self.store.save_plan(plan)
        return plan

    def reject_plan(
        self,
        *,
        plan_id: str,
        workspace_id: str,
        reviewed_by: str,
        review_notes: str = "",
    ) -> AdaptiveExecutionPlanRecord:
        plan = self._require_plan(plan_id, workspace_id)
        if plan.status not in ("DRAFT", "UNDER_REVIEW"):
            raise ValueError(f"Cannot reject plan in status '{plan.status}'")

        plan.status = "REJECTED"
        plan.reviewed_by = str(reviewed_by).strip()
        plan.review_notes = str(review_notes).strip()
        plan.reviewed_at = _now()
        plan.updated_at = _now()
        self.store.save_plan(plan)
        return plan

    def prepare_for_execution(self, *, plan_id: str, workspace_id: str) -> AdaptiveExecutionPlanRecord:
        """Transitions an APPROVED plan to READY_FOR_EXECUTION. Strictly requires human approval."""
        plan = self._require_plan(plan_id, workspace_id)
        if plan.status != "APPROVED":
            raise RuntimeError(f"Plan '{plan_id}' must be APPROVED before execution preparation (current: {plan.status})")

        plan.status = "READY_FOR_EXECUTION"
        plan.updated_at = _now()
        self.store.save_plan(plan)
        return plan

    def update_step_status(
        self,
        *,
        plan_id: str,
        workspace_id: str,
        step_id: str,
        status: PlanStepStatus,
    ) -> AdaptiveExecutionPlanRecord:
        """Records an explicit audit status transition for a specific plan step."""
        plan = self._require_plan(plan_id, workspace_id)
        updated_steps: list[AdaptivePlanStep] = []
        found = False

        for step in plan.steps:
            if step.step_id == step_id:
                found = True
                updated_steps.append(
                    AdaptivePlanStep(
                        step_id=step.step_id,
                        step_index=step.step_index,
                        title=step.title,
                        description=step.description,
                        assigned_agent_id=step.assigned_agent_id,
                        assigned_model_id=step.assigned_model_id,
                        required_capabilities=step.required_capabilities,
                        evidence_origin_ids=step.evidence_origin_ids,
                        status=status,
                    )
                )
            else:
                updated_steps.append(step)

        if not found:
            raise KeyError(f"Step '{step_id}' not found in plan '{plan_id}'")

        plan.steps = tuple(updated_steps)
        plan.updated_at = _now()
        self.store.save_plan(plan)
        return plan

    def _require_plan(self, plan_id: str, workspace_id: str) -> AdaptiveExecutionPlanRecord:
        plan = self.store.get_plan(plan_id=plan_id, workspace_id=workspace_id)
        if plan is None:
            raise KeyError(f"Execution plan '{plan_id}' not found in workspace '{workspace_id}'")
        return plan

