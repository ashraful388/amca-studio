"""AMCA Studio v0.8.0 Adaptive Coordination Intelligence Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-ADAPTIVE-COORDINATION-INTELLIGENCE-FOUNDATION-01

Core Principles:
1. Recorded facts only (strictly NO inferred complexity or hidden analysis).
2. Proposal != Execution (recommendations are proposals requiring explicit human review).
3. Human Approval Boundary (strictly NO automatic application or silent changes).
4. Full Evidence Integration (audit references linked; zero chain-of-thought).
5. 100% decoupled from Model Router, Agent Fabric, and Cognitive Workflow Orchestrator decision logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Sequence
from uuid import uuid4

from amca.cognitive_runtime_bridge import (
    FORBIDDEN_REASONING_KEYS,
    sanitize_evidence_facts,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


ProposalType = Literal[
    "ADD_REVIEWER_ROLE",
    "SWITCH_COLLABORATION_MODE",
    "ATTACH_KNOWLEDGE_SOURCE",
    "REQUEST_MODEL_UPGRADE",
    "REVISE_EXECUTION_STRATEGY",
]

ProposalStatus = Literal[
    "PROPOSED",
    "APPROVED",
    "REJECTED",
    "APPLIED",
    "SUPERSEDED",
]


@dataclass(frozen=True, slots=True)
class AdaptiveCoordinationContext:
    context_id: str
    workflow_id: str
    workspace_id: str
    task_domain: str
    detected_constraints: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    active_participants: tuple[str, ...]
    runtime_session_id: str | None
    recorded_facts: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "task_domain": self.task_domain,
            "detected_constraints": list(self.detected_constraints),
            "required_capabilities": list(self.required_capabilities),
            "active_participants": list(self.active_participants),
            "runtime_session_id": self.runtime_session_id,
            "recorded_facts": dict(self.recorded_facts),
            "created_at": self.created_at,
        }


@dataclass
class CoordinationProposalRecord:
    proposal_id: str
    context_id: str
    workflow_id: str
    workspace_id: str
    proposal_type: ProposalType
    rationale: str
    proposed_changes: dict[str, Any]
    evidence_reference_ids: tuple[str, ...]
    status: ProposalStatus = "PROPOSED"
    reviewer_notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    applied_at: str | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "context_id": self.context_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "proposal_type": self.proposal_type,
            "rationale": self.rationale,
            "proposed_changes": dict(self.proposed_changes),
            "evidence_reference_ids": list(self.evidence_reference_ids),
            "status": self.status,
            "reviewer_notes": self.reviewer_notes,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "applied_at": self.applied_at,
            "created_at": self.created_at,
        }


class AdaptiveCoordinationStore:
    """Local SQLite persistence store for adaptive contexts and coordination proposals."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS adaptive_coordination_contexts (
                    context_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    task_domain TEXT NOT NULL,
                    detected_constraints_json TEXT NOT NULL,
                    required_capabilities_json TEXT NOT NULL,
                    active_participants_json TEXT NOT NULL,
                    runtime_session_id TEXT,
                    recorded_facts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS coordination_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    context_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    proposal_type TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    proposed_changes_json TEXT NOT NULL,
                    evidence_reference_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reviewer_notes TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    applied_at TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contexts_ws_wf
                ON adaptive_coordination_contexts (workspace_id, workflow_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_proposals_ws_status
                ON coordination_proposals (workspace_id, status);
                """
            )

    def save_context(self, context: AdaptiveCoordinationContext) -> None:
        sanitized = sanitize_evidence_facts(context.recorded_facts)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO adaptive_coordination_contexts
                (context_id, workflow_id, workspace_id, task_domain, detected_constraints_json, required_capabilities_json, active_participants_json, runtime_session_id, recorded_facts_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.context_id,
                    context.workflow_id,
                    context.workspace_id,
                    context.task_domain,
                    json.dumps(list(context.detected_constraints)),
                    json.dumps(list(context.required_capabilities)),
                    json.dumps(list(context.active_participants)),
                    context.runtime_session_id,
                    json.dumps(sanitized),
                    context.created_at,
                ),
            )

    def get_context(self, *, context_id: str, workspace_id: str) -> AdaptiveCoordinationContext | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM adaptive_coordination_contexts WHERE context_id = ? AND workspace_id = ?",
                (str(context_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return AdaptiveCoordinationContext(
                context_id=str(row["context_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                task_domain=str(row["task_domain"]),
                detected_constraints=tuple(json.loads(row["detected_constraints_json"])),
                required_capabilities=tuple(json.loads(row["required_capabilities_json"])),
                active_participants=tuple(json.loads(row["active_participants_json"])),
                runtime_session_id=str(row["runtime_session_id"]) if row["runtime_session_id"] else None,
                recorded_facts=dict(json.loads(row["recorded_facts_json"])),
                created_at=str(row["created_at"]),
            )

    def save_proposal(self, proposal: CoordinationProposalRecord) -> None:
        sanitized = sanitize_evidence_facts(proposal.proposed_changes)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO coordination_proposals
                (proposal_id, context_id, workflow_id, workspace_id, proposal_type, rationale, proposed_changes_json, evidence_reference_ids_json, status, reviewer_notes, reviewed_by, reviewed_at, applied_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.context_id,
                    proposal.workflow_id,
                    proposal.workspace_id,
                    proposal.proposal_type,
                    proposal.rationale,
                    json.dumps(sanitized),
                    json.dumps(list(proposal.evidence_reference_ids)),
                    proposal.status,
                    proposal.reviewer_notes,
                    proposal.reviewed_by,
                    proposal.reviewed_at,
                    proposal.applied_at,
                    proposal.created_at,
                ),
            )

    def get_proposal(self, *, proposal_id: str, workspace_id: str) -> CoordinationProposalRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM coordination_proposals WHERE proposal_id = ? AND workspace_id = ?",
                (str(proposal_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return CoordinationProposalRecord(
                proposal_id=str(row["proposal_id"]),
                context_id=str(row["context_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                proposal_type=str(row["proposal_type"]),  # type: ignore[arg-type]
                rationale=str(row["rationale"]),
                proposed_changes=dict(json.loads(row["proposed_changes_json"])),
                evidence_reference_ids=tuple(json.loads(row["evidence_reference_ids_json"])),
                status=str(row["status"]),  # type: ignore[arg-type]
                reviewer_notes=row["reviewer_notes"],
                reviewed_by=row["reviewed_by"],
                reviewed_at=row["reviewed_at"],
                applied_at=row["applied_at"],
                created_at=str(row["created_at"]),
            )

    def list_proposals(
        self,
        *,
        workspace_id: str,
        workflow_id: str | None = None,
        status: ProposalStatus | None = None,
    ) -> list[CoordinationProposalRecord]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            query = "SELECT * FROM coordination_proposals WHERE workspace_id = ?"
            params: list[Any] = [str(workspace_id).strip()]
            if workflow_id:
                query += " AND workflow_id = ?"
                params.append(str(workflow_id).strip())
            if status:
                query += " AND status = ?"
                params.append(str(status).strip())
            query += " ORDER BY created_at ASC"

            rows = db.execute(query, params).fetchall()
            return [
                CoordinationProposalRecord(
                    proposal_id=str(r["proposal_id"]),
                    context_id=str(r["context_id"]),
                    workflow_id=str(r["workflow_id"]),
                    workspace_id=str(r["workspace_id"]),
                    proposal_type=str(r["proposal_type"]),  # type: ignore[arg-type]
                    rationale=str(r["rationale"]),
                    proposed_changes=dict(json.loads(r["proposed_changes_json"])),
                    evidence_reference_ids=tuple(json.loads(r["evidence_reference_ids_json"])),
                    status=str(r["status"]),  # type: ignore[arg-type]
                    reviewer_notes=r["reviewer_notes"],
                    reviewed_by=r["reviewed_by"],
                    reviewed_at=r["reviewed_at"],
                    applied_at=r["applied_at"],
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]


class AdaptiveCoordinationEngine:
    """Manages adaptive coordination context capture and governed proposal workflows."""

    def __init__(self, store: AdaptiveCoordinationStore) -> None:
        self.store = store

    def capture_coordination_context(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        task_domain: str,
        detected_constraints: Sequence[str] = (),
        required_capabilities: Sequence[str] = (),
        active_participants: Sequence[str] = (),
        runtime_session_id: str | None = None,
        recorded_facts: dict[str, Any] | None = None,
    ) -> AdaptiveCoordinationContext:
        """Captures an explicit adaptive coordination context containing recorded facts only."""
        clean_facts = sanitize_evidence_facts(recorded_facts or {})
        context = AdaptiveCoordinationContext(
            context_id=f"ctx-{uuid4().hex[:16]}",
            workflow_id=str(workflow_id).strip(),
            workspace_id=str(workspace_id).strip(),
            task_domain=str(task_domain).strip(),
            detected_constraints=tuple(detected_constraints),
            required_capabilities=tuple(required_capabilities),
            active_participants=tuple(active_participants),
            runtime_session_id=str(runtime_session_id).strip() if runtime_session_id else None,
            recorded_facts=clean_facts,
            created_at=_now(),
        )
        self.store.save_context(context)
        return context

    def create_proposal(
        self,
        *,
        context_id: str,
        workspace_id: str,
        proposal_type: ProposalType,
        rationale: str,
        proposed_changes: dict[str, Any],
        evidence_reference_ids: Sequence[str] = (),
    ) -> CoordinationProposalRecord:
        """Generates an explicit proposal for human review. Does NOT execute or apply changes."""
        context = self.store.get_context(context_id=context_id, workspace_id=workspace_id)
        if context is None:
            raise KeyError(f"Context '{context_id}' not found in workspace '{workspace_id}'")

        clean_changes = sanitize_evidence_facts(proposed_changes)
        proposal = CoordinationProposalRecord(
            proposal_id=f"prop-{uuid4().hex[:16]}",
            context_id=context_id,
            workflow_id=context.workflow_id,
            workspace_id=workspace_id,
            proposal_type=proposal_type,
            rationale=str(rationale).strip(),
            proposed_changes=clean_changes,
            evidence_reference_ids=tuple(evidence_reference_ids),
            status="PROPOSED",
            created_at=_now(),
        )
        self.store.save_proposal(proposal)
        return proposal

    def approve_proposal(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
        reviewed_by: str,
        reviewer_notes: str = "",
    ) -> CoordinationProposalRecord:
        """Records explicit human approval of a proposed coordination change."""
        proposal = self._require_proposal(proposal_id, workspace_id)
        if proposal.status != "PROPOSED":
            raise ValueError(f"Cannot approve proposal in status '{proposal.status}'")

        proposal.status = "APPROVED"
        proposal.reviewed_by = str(reviewed_by).strip()
        proposal.reviewer_notes = str(reviewer_notes).strip()
        proposal.reviewed_at = _now()
        self.store.save_proposal(proposal)
        return proposal

    def reject_proposal(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
        reviewed_by: str,
        reviewer_notes: str = "",
    ) -> CoordinationProposalRecord:
        """Records explicit rejection of a proposal."""
        proposal = self._require_proposal(proposal_id, workspace_id)
        if proposal.status != "PROPOSED":
            raise ValueError(f"Cannot reject proposal in status '{proposal.status}'")

        proposal.status = "REJECTED"
        proposal.reviewed_by = str(reviewed_by).strip()
        proposal.reviewer_notes = str(reviewer_notes).strip()
        proposal.reviewed_at = _now()
        self.store.save_proposal(proposal)
        return proposal

    def apply_approved_proposal(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
    ) -> CoordinationProposalRecord:
        """Transitions an approved proposal to APPLIED. Strictly blocks unapproved proposals."""
        proposal = self._require_proposal(proposal_id, workspace_id)
        if proposal.status != "APPROVED":
            raise RuntimeError(f"Proposal '{proposal_id}' cannot be applied in status '{proposal.status}'; must be APPROVED")

        proposal.status = "APPLIED"
        proposal.applied_at = _now()
        self.store.save_proposal(proposal)
        return proposal

    def supersede_proposal(
        self,
        *,
        proposal_id: str,
        workspace_id: str,
        reason: str = "Superseded by updated workflow context",
    ) -> CoordinationProposalRecord:
        """Marks a proposal as superseded."""
        proposal = self._require_proposal(proposal_id, workspace_id)
        proposal.status = "SUPERSEDED"
        proposal.reviewer_notes = reason
        proposal.reviewed_at = _now()
        self.store.save_proposal(proposal)
        return proposal

    def _require_proposal(self, proposal_id: str, workspace_id: str) -> CoordinationProposalRecord:
        proposal = self.store.get_proposal(proposal_id=proposal_id, workspace_id=workspace_id)
        if proposal is None:
            raise KeyError(f"Proposal '{proposal_id}' not found in workspace '{workspace_id}'")
        return proposal

