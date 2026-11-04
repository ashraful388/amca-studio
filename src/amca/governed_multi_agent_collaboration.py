"""AMCA Studio v0.8.0 Governed Multi-Agent Collaboration Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-GOVERNED-MULTI-AGENT-COLLABORATION-FOUNDATION-01

Core Principles:
1. Coordination Infrastructure only (strictly NO autonomous agent spawning or delegation).
2. Explicit Role Assignments bound to verified capability compatibility.
3. Strict Governance Gate checks (contract state, permissions, locality, provider approvals).
4. Auditable Collaboration Communication Boundary (factual metadata; zero chain-of-thought).
5. 100% decoupled from Agent Fabric internal execution engine and Model Router algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Sequence
from uuid import uuid4

from amca.agent_capability_governance import (
    AgentCapabilityGovernanceEngine,
    AgentCapabilityGovernanceStore,
    CollaborationContractRecord,
)
from amca.cognitive_runtime_bridge import (
    FORBIDDEN_REASONING_KEYS,
    sanitize_evidence_facts,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


CollaborationRole = Literal[
    "PRIMARY_AUTHOR",
    "TECHNICAL_REVIEWER",
    "RESEARCH_SYNTHESIZER",
    "GOVERNANCE_AUDITOR",
    "COORDINATOR",
]

CollaborationSessionStatus = Literal[
    "INITIALIZED",
    "GOVERNANCE_VERIFIED",
    "ACTIVE",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "TERMINATED",
]


@dataclass(frozen=True, slots=True)
class AgentRoleAssignment:
    assignment_id: str
    session_id: str
    agent_id: str
    role: CollaborationRole
    required_capabilities: tuple[str, ...]
    compatibility_verified: bool
    assigned_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "required_capabilities": list(self.required_capabilities),
            "compatibility_verified": self.compatibility_verified,
            "assigned_at": self.assigned_at,
        }


@dataclass(frozen=True, slots=True)
class CollaborationMessageRecord:
    message_id: str
    session_id: str
    sender_id: str
    recipient_id: str
    message_type: str
    content_reference: str
    metadata: dict[str, Any]
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "session_id": self.session_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "message_type": self.message_type,
            "content_reference": self.content_reference,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp,
        }


@dataclass
class GovernedCollaborationSessionRecord:
    session_id: str
    contract_id: str
    workflow_id: str
    workspace_id: str
    lead_agent_id: str
    status: CollaborationSessionStatus = "INITIALIZED"
    governance_verified: bool = False
    governance_check_details: dict[str, Any] = field(default_factory=dict)
    message_count: int = 0
    error_message: str | None = None
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "contract_id": self.contract_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "lead_agent_id": self.lead_agent_id,
            "status": self.status,
            "governance_verified": self.governance_verified,
            "governance_check_details": dict(self.governance_check_details),
            "message_count": self.message_count,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class GovernedCollaborationStore:
    """Local SQLite persistence store for multi-agent collaboration sessions, roles, and message records."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_sessions (
                    session_id TEXT PRIMARY KEY,
                    contract_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    lead_agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    governance_verified INTEGER NOT NULL,
                    governance_details_json TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_role_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    required_capabilities_json TEXT NOT NULL,
                    compatibility_verified INTEGER NOT NULL,
                    assigned_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content_reference TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_collab_sess_ws 
                ON collaboration_sessions (workspace_id, session_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_collab_roles_sess 
                ON collaboration_role_assignments (session_id, agent_id);
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_collab_msg_sess 
                ON collaboration_messages (session_id, timestamp);
                """
            )

    def save_session(self, session: GovernedCollaborationSessionRecord) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO collaboration_sessions
                (session_id, contract_id, workflow_id, workspace_id, lead_agent_id, status, governance_verified, governance_details_json, message_count, error_message, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.contract_id,
                    session.workflow_id,
                    session.workspace_id,
                    session.lead_agent_id,
                    session.status,
                    1 if session.governance_verified else 0,
                    json.dumps(session.governance_check_details),
                    session.message_count,
                    session.error_message,
                    session.started_at,
                    session.finished_at,
                ),
            )

    def get_session(self, *, session_id: str, workspace_id: str) -> GovernedCollaborationSessionRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM collaboration_sessions WHERE session_id = ? AND workspace_id = ?",
                (str(session_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return GovernedCollaborationSessionRecord(
                session_id=str(row["session_id"]),
                contract_id=str(row["contract_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                lead_agent_id=str(row["lead_agent_id"]),
                status=str(row["status"]),  # type: ignore[arg-type]
                governance_verified=bool(row["governance_verified"]),
                governance_check_details=dict(json.loads(row["governance_details_json"])),
                message_count=int(row["message_count"]),
                error_message=row["error_message"],
                started_at=str(row["started_at"]),
                finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            )

    def save_role_assignment(self, assignment: AgentRoleAssignment) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO collaboration_role_assignments
                (assignment_id, session_id, agent_id, role, required_capabilities_json, compatibility_verified, assigned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment.assignment_id,
                    assignment.session_id,
                    assignment.agent_id,
                    assignment.role,
                    json.dumps(list(assignment.required_capabilities)),
                    1 if assignment.compatibility_verified else 0,
                    assignment.assigned_at,
                ),
            )

    def list_role_assignments(self, session_id: str) -> list[AgentRoleAssignment]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM collaboration_role_assignments WHERE session_id = ? ORDER BY assigned_at ASC",
                (str(session_id).strip(),),
            ).fetchall()
            return [
                AgentRoleAssignment(
                    assignment_id=str(r["assignment_id"]),
                    session_id=str(r["session_id"]),
                    agent_id=str(r["agent_id"]),
                    role=str(r["role"]),  # type: ignore[arg-type]
                    required_capabilities=tuple(json.loads(r["required_capabilities_json"])),
                    compatibility_verified=bool(r["compatibility_verified"]),
                    assigned_at=str(r["assigned_at"]),
                )
                for r in rows
            ]

    def record_message(self, message: CollaborationMessageRecord) -> None:
        sanitized = sanitize_evidence_facts(message.metadata)
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT INTO collaboration_messages
                (message_id, session_id, sender_id, recipient_id, message_type, content_reference, metadata_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.session_id,
                    message.sender_id,
                    message.recipient_id,
                    message.message_type,
                    message.content_reference,
                    json.dumps(sanitized),
                    message.timestamp,
                ),
            )
            # Increment session message count
            db.execute(
                "UPDATE collaboration_sessions SET message_count = message_count + 1 WHERE session_id = ?",
                (message.session_id,),
            )

    def list_messages(self, session_id: str) -> list[CollaborationMessageRecord]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM collaboration_messages WHERE session_id = ? ORDER BY timestamp ASC",
                (str(session_id).strip(),),
            ).fetchall()
            return [
                CollaborationMessageRecord(
                    message_id=str(r["message_id"]),
                    session_id=str(r["session_id"]),
                    sender_id=str(r["sender_id"]),
                    recipient_id=str(r["recipient_id"]),
                    message_type=str(r["message_type"]),
                    content_reference=str(r["content_reference"]),
                    metadata=dict(json.loads(r["metadata_json"])),
                    timestamp=str(r["timestamp"]),
                )
                for r in rows
            ]


class GovernedCollaborationCoordinator:
    """Coordinates multi-agent collaboration under explicit human-approved governance contracts."""

    def __init__(
        self,
        *,
        store: GovernedCollaborationStore,
        governance_engine: AgentCapabilityGovernanceEngine,
    ) -> None:
        self.store = store
        self.governance_engine = governance_engine

    def create_collaboration_session(
        self,
        *,
        contract_id: str,
        workspace_id: str,
    ) -> GovernedCollaborationSessionRecord:
        """Initializes a governed collaboration session strictly bound to an existing contract."""
        contract = self.governance_engine.store.get_contract(
            contract_id=contract_id,
            workspace_id=workspace_id,
        )
        if contract is None:
            raise KeyError(f"Collaboration contract '{contract_id}' not found in workspace '{workspace_id}'")

        if contract.status not in ("AGREED", "ACTIVE"):
            raise ValueError(f"Cannot initialize session for contract in '{contract.status}' status")

        session_id = f"gcs-{uuid4().hex[:16]}"
        now_ts = _now()
        session = GovernedCollaborationSessionRecord(
            session_id=session_id,
            contract_id=contract_id,
            workflow_id=contract.workflow_id,
            workspace_id=workspace_id,
            lead_agent_id=contract.lead_agent_id,
            status="INITIALIZED",
            governance_verified=False,
            started_at=now_ts,
        )
        self.store.save_session(session)
        return session

    def assign_agent_role(
        self,
        *,
        session_id: str,
        workspace_id: str,
        agent_id: str,
        role: CollaborationRole,
        required_capabilities: Sequence[str],
    ) -> AgentRoleAssignment:
        """Assigns an explicit collaboration role after verifying declared capability compatibility."""
        session = self._require_session(session_id, workspace_id)
        if session.status != "INITIALIZED":
            raise ValueError(f"Cannot assign roles to session in status '{session.status}'")

        agent_profile = self.governance_engine.store.get_agent_profile(agent_id)
        if agent_profile is None:
            raise KeyError(f"Agent '{agent_id}' is not registered in capability registry")

        # Verify declared capabilities contain all required capabilities
        req_set = {str(c).strip().lower() for c in required_capabilities if str(c).strip()}
        agent_caps = {str(c).strip().lower() for c in agent_profile.declared_capabilities}
        compatibility = req_set.issubset(agent_caps)

        assignment = AgentRoleAssignment(
            assignment_id=f"asgn-{uuid4().hex[:12]}",
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            required_capabilities=tuple(required_capabilities),
            compatibility_verified=compatibility,
            assigned_at=_now(),
        )
        self.store.save_role_assignment(assignment)
        return assignment

    def verify_collaboration_governance(
        self,
        *,
        session_id: str,
        workspace_id: str,
        provider_approvals_verified: bool,
        locality_requirements_passed: bool = True,
    ) -> GovernedCollaborationSessionRecord:
        """Verifies that all participants, roles, and constraints pass governance gates before activation."""
        session = self._require_session(session_id, workspace_id)
        assignments = self.store.list_role_assignments(session_id)

        # Invariant: At least one assignment and all assignments must have compatibility_verified=True
        all_roles_compatible = len(assignments) > 0 and all(a.compatibility_verified for a in assignments)
        passed = (
            all_roles_compatible
            and provider_approvals_verified
            and locality_requirements_passed
        )

        details = {
            "all_roles_compatible": all_roles_compatible,
            "provider_approvals_verified": provider_approvals_verified,
            "locality_requirements_passed": locality_requirements_passed,
            "assigned_roles_count": len(assignments),
            "gate_passed": passed,
        }

        session.governance_verified = passed
        session.governance_check_details = details
        session.status = "GOVERNANCE_VERIFIED" if passed else "FAILED"
        if not passed:
            session.error_message = "Collaboration governance verification failed"
            session.finished_at = _now()

        self.store.save_session(session)
        return session

    def activate_collaboration(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> GovernedCollaborationSessionRecord:
        """Activates a verified collaboration session."""
        session = self._require_session(session_id, workspace_id)
        if session.status != "GOVERNANCE_VERIFIED" or not session.governance_verified:
            raise RuntimeError(f"Collaboration session '{session_id}' has not passed governance verification")

        session.status = "ACTIVE"
        self.store.save_session(session)
        return session

    def post_collaboration_message(
        self,
        *,
        session_id: str,
        workspace_id: str,
        sender_id: str,
        recipient_id: str,
        message_type: str,
        content_reference: str,
        metadata: dict[str, Any] | None = None,
    ) -> CollaborationMessageRecord:
        """Records an auditable message exchange between collaborating participants."""
        session = self._require_session(session_id, workspace_id)
        if session.status != "ACTIVE":
            raise RuntimeError(f"Cannot post message to session in '{session.status}' status")

        clean_meta = sanitize_evidence_facts(metadata or {})
        msg = CollaborationMessageRecord(
            message_id=f"cmsg-{uuid4().hex[:12]}",
            session_id=session_id,
            sender_id=str(sender_id).strip(),
            recipient_id=str(recipient_id).strip(),
            message_type=str(message_type).strip(),
            content_reference=str(content_reference).strip(),
            metadata=clean_meta,
            timestamp=_now(),
        )
        self.store.record_message(msg)
        return msg

    def complete_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> GovernedCollaborationSessionRecord:
        """Marks an active collaboration session as completed."""
        session = self._require_session(session_id, workspace_id)
        session.status = "COMPLETED"
        session.finished_at = _now()
        self.store.save_session(session)
        return session

    def terminate_session(
        self,
        *,
        session_id: str,
        workspace_id: str,
        reason: str = "Operator terminated collaboration",
    ) -> GovernedCollaborationSessionRecord:
        """Terminates a collaboration session upon operator request or error."""
        session = self._require_session(session_id, workspace_id)
        session.status = "TERMINATED"
        session.error_message = reason
        session.finished_at = _now()
        self.store.save_session(session)
        return session

    def _require_session(self, session_id: str, workspace_id: str) -> GovernedCollaborationSessionRecord:
        session = self.store.get_session(session_id=session_id, workspace_id=workspace_id)
        if session is None:
            raise KeyError(f"Collaboration session '{session_id}' not found in workspace '{workspace_id}'")
        return session

