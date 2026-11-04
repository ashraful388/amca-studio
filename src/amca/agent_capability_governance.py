"""AMCA Studio v0.8.0 Agent Capability Negotiation & Governance Foundation.

Milestone: AG-AMCA-STUDIO-V0.8.0-AGENT-CAPABILITY-NEGOTIATION-AND-GOVERNANCE-FOUNDATION-01

Core Invariants:
1. Recorded capabilities only (strictly NO inferred or assumed abilities).
2. Deterministic matching support (exact capability subset/intersection; strictly NO probabilistic ranking or scoring).
3. Explicit Collaboration Contracts (preserves human governance; strictly NO autonomous agent spawning or delegation).
4. 100% decoupling from Model Router algorithms and Agent Fabric internal runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


LocalityAffinity = Literal["LOCAL_ONLY", "HYBRID", "CLOUD_ALLOWED"]
ContractStatus = Literal["DRAFT", "AGREED", "ACTIVE", "COMPLETED", "TERMINATED"]


@dataclass(frozen=True, slots=True)
class AgentCapabilityProfile:
    agent_id: str
    name: str
    role: str
    declared_capabilities: tuple[str, ...]
    permission_scopes: tuple[str, ...]
    locality_affinity: LocalityAffinity = "HYBRID"
    enabled: bool = True
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "declared_capabilities": list(self.declared_capabilities),
            "permission_scopes": list(self.permission_scopes),
            "locality_affinity": self.locality_affinity,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ModelCapabilityProfile:
    model_id: str
    provider: str
    locality: str
    supported_capabilities: tuple[str, ...]
    context_window: int
    governance_requirements: tuple[str, ...]
    enabled: bool = True
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "locality": self.locality,
            "supported_capabilities": list(self.supported_capabilities),
            "context_window": self.context_window,
            "governance_requirements": list(self.governance_requirements),
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class DeterministicMatchCandidate:
    candidate_id: str
    candidate_type: Literal["AGENT", "MODEL"]
    matched_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    exact_match: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "matched_capabilities": list(self.matched_capabilities),
            "missing_capabilities": list(self.missing_capabilities),
            "exact_match": self.exact_match,
        }


@dataclass
class CollaborationContractRecord:
    contract_id: str
    workflow_id: str
    workspace_id: str
    lead_agent_id: str
    participant_agent_ids: tuple[str, ...]
    assigned_model_ids: tuple[str, ...]
    agreed_capabilities: tuple[str, ...]
    governance_constraints: tuple[str, ...]
    status: ContractStatus = "DRAFT"
    termination_reason: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "workflow_id": self.workflow_id,
            "workspace_id": self.workspace_id,
            "lead_agent_id": self.lead_agent_id,
            "participant_agent_ids": list(self.participant_agent_ids),
            "assigned_model_ids": list(self.assigned_model_ids),
            "agreed_capabilities": list(self.agreed_capabilities),
            "governance_constraints": list(self.governance_constraints),
            "status": self.status,
            "termination_reason": self.termination_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AgentCapabilityGovernanceStore:
    """Local SQLite persistence store for agent capabilities, model capabilities, and collaboration contracts."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_capabilities (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    declared_capabilities_json TEXT NOT NULL,
                    permission_scopes_json TEXT NOT NULL,
                    locality_affinity TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS model_capabilities (
                    model_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    locality TEXT NOT NULL,
                    supported_capabilities_json TEXT NOT NULL,
                    context_window INTEGER NOT NULL,
                    governance_requirements_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_contracts (
                    contract_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    lead_agent_id TEXT NOT NULL,
                    participant_agents_json TEXT NOT NULL,
                    assigned_models_json TEXT NOT NULL,
                    agreed_capabilities_json TEXT NOT NULL,
                    governance_constraints_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    termination_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_contracts_ws_wf
                ON collaboration_contracts (workspace_id, workflow_id);
                """
            )

    def save_agent_profile(self, profile: AgentCapabilityProfile) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO agent_capabilities
                (agent_id, name, role, declared_capabilities_json, permission_scopes_json, locality_affinity, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.agent_id,
                    profile.name,
                    profile.role,
                    json.dumps(list(profile.declared_capabilities)),
                    json.dumps(list(profile.permission_scopes)),
                    profile.locality_affinity,
                    1 if profile.enabled else 0,
                    profile.created_at,
                ),
            )

    def get_agent_profile(self, agent_id: str) -> AgentCapabilityProfile | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM agent_capabilities WHERE agent_id = ?",
                (str(agent_id).strip(),),
            ).fetchone()
            if row is None:
                return None
            return AgentCapabilityProfile(
                agent_id=str(row["agent_id"]),
                name=str(row["name"]),
                role=str(row["role"]),
                declared_capabilities=tuple(json.loads(row["declared_capabilities_json"])),
                permission_scopes=tuple(json.loads(row["permission_scopes_json"])),
                locality_affinity=str(row["locality_affinity"]),  # type: ignore[arg-type]
                enabled=bool(row["enabled"]),
                created_at=str(row["created_at"]),
            )

    def list_agent_profiles(self, *, enabled_only: bool = True) -> list[AgentCapabilityProfile]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            query = "SELECT * FROM agent_capabilities"
            params: tuple[Any, ...] = ()
            if enabled_only:
                query += " WHERE enabled = 1"
            query += " ORDER BY agent_id ASC"
            rows = db.execute(query, params).fetchall()
            return [
                AgentCapabilityProfile(
                    agent_id=str(r["agent_id"]),
                    name=str(r["name"]),
                    role=str(r["role"]),
                    declared_capabilities=tuple(json.loads(r["declared_capabilities_json"])),
                    permission_scopes=tuple(json.loads(r["permission_scopes_json"])),
                    locality_affinity=str(r["locality_affinity"]),  # type: ignore[arg-type]
                    enabled=bool(r["enabled"]),
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]

    def save_model_profile(self, profile: ModelCapabilityProfile) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO model_capabilities
                (model_id, provider, locality, supported_capabilities_json, context_window, governance_requirements_json, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.model_id,
                    profile.provider,
                    profile.locality,
                    json.dumps(list(profile.supported_capabilities)),
                    profile.context_window,
                    json.dumps(list(profile.governance_requirements)),
                    1 if profile.enabled else 0,
                    profile.created_at,
                ),
            )

    def get_model_profile(self, model_id: str) -> ModelCapabilityProfile | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM model_capabilities WHERE model_id = ?",
                (str(model_id).strip(),),
            ).fetchone()
            if row is None:
                return None
            return ModelCapabilityProfile(
                model_id=str(row["model_id"]),
                provider=str(row["provider"]),
                locality=str(row["locality"]),
                supported_capabilities=tuple(json.loads(row["supported_capabilities_json"])),
                context_window=int(row["context_window"]),
                governance_requirements=tuple(json.loads(row["governance_requirements_json"])),
                enabled=bool(row["enabled"]),
                created_at=str(row["created_at"]),
            )

    def list_model_profiles(self, *, enabled_only: bool = True) -> list[ModelCapabilityProfile]:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            query = "SELECT * FROM model_capabilities"
            params: tuple[Any, ...] = ()
            if enabled_only:
                query += " WHERE enabled = 1"
            query += " ORDER BY model_id ASC"
            rows = db.execute(query, params).fetchall()
            return [
                ModelCapabilityProfile(
                    model_id=str(r["model_id"]),
                    provider=str(r["provider"]),
                    locality=str(r["locality"]),
                    supported_capabilities=tuple(json.loads(r["supported_capabilities_json"])),
                    context_window=int(r["context_window"]),
                    governance_requirements=tuple(json.loads(r["governance_requirements_json"])),
                    enabled=bool(r["enabled"]),
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]

    def save_contract(self, contract: CollaborationContractRecord) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO collaboration_contracts
                (contract_id, workflow_id, workspace_id, lead_agent_id, participant_agents_json, assigned_models_json, agreed_capabilities_json, governance_constraints_json, status, termination_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract.contract_id,
                    contract.workflow_id,
                    contract.workspace_id,
                    contract.lead_agent_id,
                    json.dumps(list(contract.participant_agent_ids)),
                    json.dumps(list(contract.assigned_model_ids)),
                    json.dumps(list(contract.agreed_capabilities)),
                    json.dumps(list(contract.governance_constraints)),
                    contract.status,
                    contract.termination_reason,
                    contract.created_at,
                    contract.updated_at,
                ),
            )

    def get_contract(self, *, contract_id: str, workspace_id: str) -> CollaborationContractRecord | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM collaboration_contracts WHERE contract_id = ? AND workspace_id = ?",
                (str(contract_id).strip(), str(workspace_id).strip()),
            ).fetchone()
            if row is None:
                return None
            return CollaborationContractRecord(
                contract_id=str(row["contract_id"]),
                workflow_id=str(row["workflow_id"]),
                workspace_id=str(row["workspace_id"]),
                lead_agent_id=str(row["lead_agent_id"]),
                participant_agent_ids=tuple(json.loads(row["participant_agents_json"])),
                assigned_model_ids=tuple(json.loads(row["assigned_models_json"])),
                agreed_capabilities=tuple(json.loads(row["agreed_capabilities_json"])),
                governance_constraints=tuple(json.loads(row["governance_constraints_json"])),
                status=str(row["status"]),  # type: ignore[arg-type]
                termination_reason=row["termination_reason"],
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )


class AgentCapabilityGovernanceEngine:
    """Provides deterministic capability matching and collaboration contract governance."""

    def __init__(self, store: AgentCapabilityGovernanceStore) -> None:
        self.store = store

    def register_agent_profile(self, profile: AgentCapabilityProfile) -> None:
        """Registers an agent capability profile (declared capabilities only)."""
        self.store.save_agent_profile(profile)

    def register_model_profile(self, profile: ModelCapabilityProfile) -> None:
        """Registers a model capability profile (supported capabilities only)."""
        self.store.save_model_profile(profile)

    def match_agent_candidates(
        self,
        *,
        required_capabilities: Sequence[str],
        locality_filter: LocalityAffinity | None = None,
    ) -> list[DeterministicMatchCandidate]:
        """Matches available agents deterministically by capability subset / intersection."""
        req_set = {str(c).strip().lower() for c in required_capabilities if str(c).strip()}
        all_agents = self.store.list_agent_profiles(enabled_only=True)
        results: list[DeterministicMatchCandidate] = []

        for agent in all_agents:
            if locality_filter and agent.locality_affinity != locality_filter:
                continue

            agent_caps = {str(c).strip().lower() for c in agent.declared_capabilities}
            matched = tuple(sorted(req_set.intersection(agent_caps)))
            missing = tuple(sorted(req_set - agent_caps))
            exact_match = len(missing) == 0

            # Only include if at least one required capability matched or if requirement was empty
            if matched or not req_set:
                results.append(
                    DeterministicMatchCandidate(
                        candidate_id=agent.agent_id,
                        candidate_type="AGENT",
                        matched_capabilities=matched,
                        missing_capabilities=missing,
                        exact_match=exact_match,
                    )
                )

        # Deterministic sorting by exact_match DESC, count of matched DESC, candidate_id ASC
        results.sort(
            key=lambda c: (1 if c.exact_match else 0, len(c.matched_capabilities), c.candidate_id),
            reverse=True,
        )
        return results

    def match_model_candidates(
        self,
        *,
        required_capabilities: Sequence[str],
        locality_filter: str | None = None,
    ) -> list[DeterministicMatchCandidate]:
        """Matches available models deterministically by capability subset / intersection."""
        req_set = {str(c).strip().lower() for c in required_capabilities if str(c).strip()}
        all_models = self.store.list_model_profiles(enabled_only=True)
        results: list[DeterministicMatchCandidate] = []

        for model in all_models:
            if locality_filter and model.locality.lower() != locality_filter.lower():
                continue

            model_caps = {str(c).strip().lower() for c in model.supported_capabilities}
            matched = tuple(sorted(req_set.intersection(model_caps)))
            missing = tuple(sorted(req_set - model_caps))
            exact_match = len(missing) == 0

            if matched or not req_set:
                results.append(
                    DeterministicMatchCandidate(
                        candidate_id=model.model_id,
                        candidate_type="MODEL",
                        matched_capabilities=matched,
                        missing_capabilities=missing,
                        exact_match=exact_match,
                    )
                )

        results.sort(
            key=lambda c: (1 if c.exact_match else 0, len(c.matched_capabilities), c.candidate_id),
            reverse=True,
        )
        return results

    def create_collaboration_contract(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        lead_agent_id: str,
        participant_agent_ids: Sequence[str],
        assigned_model_ids: Sequence[str],
        agreed_capabilities: Sequence[str],
        governance_constraints: Sequence[str] = (),
    ) -> CollaborationContractRecord:
        """Establishes an explicit collaboration contract across agents and models."""
        lead = self.store.get_agent_profile(lead_agent_id)
        if lead is None:
            raise KeyError(f"Lead agent '{lead_agent_id}' not found in registry")

        contract_id = f"cntr-{uuid4().hex[:16]}"
        now_ts = _now()
        contract = CollaborationContractRecord(
            contract_id=contract_id,
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            lead_agent_id=lead_agent_id,
            participant_agent_ids=tuple(participant_agent_ids),
            assigned_model_ids=tuple(assigned_model_ids),
            agreed_capabilities=tuple(agreed_capabilities),
            governance_constraints=tuple(governance_constraints),
            status="AGREED",
            created_at=now_ts,
            updated_at=now_ts,
        )
        self.store.save_contract(contract)
        return contract

    def activate_contract(self, *, contract_id: str, workspace_id: str) -> CollaborationContractRecord:
        contract = self._require_contract(contract_id, workspace_id)
        contract.status = "ACTIVE"
        contract.updated_at = _now()
        self.store.save_contract(contract)
        return contract

    def terminate_contract(
        self,
        *,
        contract_id: str,
        workspace_id: str,
        reason: str = "Completed session",
    ) -> CollaborationContractRecord:
        contract = self._require_contract(contract_id, workspace_id)
        contract.status = "TERMINATED"
        contract.termination_reason = reason
        contract.updated_at = _now()
        self.store.save_contract(contract)
        return contract

    def _require_contract(self, contract_id: str, workspace_id: str) -> CollaborationContractRecord:
        contract = self.store.get_contract(contract_id=contract_id, workspace_id=workspace_id)
        if contract is None:
            raise KeyError(f"Contract '{contract_id}' not found in workspace '{workspace_id}'")
        return contract

