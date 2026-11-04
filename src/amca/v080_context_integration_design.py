"""AMCA Studio v0.8.0 Knowledge Context Integration Design (AG-AMCA-STUDIO-V0.8.0).

Defines architectural specifications, contracts, permission gates, and data boundaries
for integrating Knowledge Intelligence with Cognitive Workflows and Agent Fabric:
1. Knowledge Context Contract (context item fields, provenance references, confidence score)
2. Agent Fabric Integration Protocol (explicit tool-call access, audit records)
3. Three-Store Partition Model (MemoryStore vs KnowledgeStore vs ArtifactStore)
4. Workflow Integration (explicit user opt-in, zero hidden context injection)
5. Security & Audit Specifications (role-based access, private knowledge filters)

Strictly architecture specification, schema design, and feasibility verification;
no premature runtime injection or workflow modifications introduced.
100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class KnowledgeContextItem:
    artifact_id: str
    workspace_id: str
    title: str
    excerpt: str
    relevance_score: float
    provenance_reference: str
    permission_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "relevance_score": self.relevance_score,
            "provenance_reference": self.provenance_reference,
            "permission_verified": self.permission_verified,
        }


@dataclass(frozen=True, slots=True)
class StoreBoundaryDefinition:
    store_name: str
    purpose: str
    lifecycle_duration: str
    schema_storage: str
    indexing_behavior: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_name": self.store_name,
            "purpose": self.purpose,
            "lifecycle_duration": self.lifecycle_duration,
            "schema_storage": self.schema_storage,
            "indexing_behavior": self.indexing_behavior,
        }


@dataclass(frozen=True, slots=True)
class AgentKnowledgeAccessPolicy:
    agent_role: str
    allowed_actions: tuple[str, ...]
    max_context_tokens: int
    requires_explicit_user_prompt: bool
    audit_trail_logging: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_role": self.agent_role,
            "allowed_actions": list(self.allowed_actions),
            "max_context_tokens": self.max_context_tokens,
            "requires_explicit_user_prompt": self.requires_explicit_user_prompt,
            "audit_trail_logging": self.audit_trail_logging,
        }


@dataclass(frozen=True, slots=True)
class ContextIntegrationDesignPackage:
    design_id: str
    target_version: str
    context_contract_version: str
    store_boundaries: tuple[StoreBoundaryDefinition, ...]
    agent_access_policies: tuple[AgentKnowledgeAccessPolicy, ...]
    hidden_injection_permitted: bool
    overall_status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "target_version": self.target_version,
            "context_contract_version": self.context_contract_version,
            "store_boundaries": [s.to_dict() for s in self.store_boundaries],
            "agent_access_policies": [p.to_dict() for p in self.agent_access_policies],
            "hidden_injection_permitted": self.hidden_injection_permitted,
            "overall_status": self.overall_status,
            "created_at": self.created_at,
        }


def assemble_context_integration_design(
    target_version: str = "v0.8.0",
) -> ContextIntegrationDesignPackage:
    """Assembles the formal v0.8.0 Knowledge Context Integration architectural design specification."""
    store_boundaries = (
        StoreBoundaryDefinition(
            store_name="MemoryStore",
            purpose="Short-term episodic memory, turn-by-turn chat history, scratchpad notes",
            lifecycle_duration="Session / Project scoped, volatile or periodic prune",
            schema_storage="SQLite memories table",
            indexing_behavior="Non-indexed or ephemeral keyword match",
        ),
        StoreBoundaryDefinition(
            store_name="KnowledgeStore (FTS5 + Vector)",
            purpose="Curated project intelligence, documentation, guidelines, indexed artifacts",
            lifecycle_duration="Long-term persistent project knowledge",
            schema_storage="SQLite FTS5 virtual tables + Vector BLOB tables",
            indexing_behavior="Shadow-indexed with pre-sanitization",
        ),
        StoreBoundaryDefinition(
            store_name="ArtifactStore",
            purpose="Immutable content-addressable source payloads and renditions",
            lifecycle_duration="Permanent append-only version history",
            schema_storage="SQLite artifacts table + filesystem payload blobs",
            indexing_behavior="Read-only authoritative source representation",
        ),
    )

    agent_access_policies = (
        AgentKnowledgeAccessPolicy(
            agent_role="researcher",
            allowed_actions=("knowledge.search", "knowledge.fetch_excerpt"),
            max_context_tokens=1500,
            requires_explicit_user_prompt=True,
            audit_trail_logging=True,
        ),
        AgentKnowledgeAccessPolicy(
            agent_role="coder",
            allowed_actions=("knowledge.search", "knowledge.fetch_excerpt"),
            max_context_tokens=2000,
            requires_explicit_user_prompt=True,
            audit_trail_logging=True,
        ),
        AgentKnowledgeAccessPolicy(
            agent_role="reviewer",
            allowed_actions=("knowledge.search", "knowledge.verify_provenance"),
            max_context_tokens=1000,
            requires_explicit_user_prompt=True,
            audit_trail_logging=True,
        ),
    )

    return ContextIntegrationDesignPackage(
        design_id=f"ctx-dsn-{uuid4().hex[:16]}",
        target_version=str(target_version).strip(),
        context_contract_version="v1-explicit",
        store_boundaries=store_boundaries,
        agent_access_policies=agent_access_policies,
        hidden_injection_permitted=False,
        overall_status="CONTEXT_INTEGRATION_DESIGN_APPROVED",
        created_at=_now(),
    )

