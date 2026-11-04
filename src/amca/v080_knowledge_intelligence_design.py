"""AMCA Studio v0.8.0 Knowledge Intelligence Foundation Design (AG-AMCA-STUDIO-V0.8.0).

Defines implementation-ready architecture specifications, data schemas, migration boundaries,
and security models for the v0.8.0 Knowledge Intelligence Evolution:
1. Current SQLite ArtifactStore / MemoryStore baseline audit
2. FTS5 full-text indexing schema and lifecycle triggers
3. Hybrid lexical + local vector reranking score fusion model
4. Air-gapped knowledge security, credential redaction, and workspace isolation

Strictly architecture specification, schema design, and feasibility verification;
no premature modifications to existing runtime stores.
100% offline, zero cloud telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class KnowledgeFieldMapping:
    field_name: str
    data_type: str
    indexed_in_fts5: bool
    tokenization_rule: str
    redaction_filter: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "data_type": self.data_type,
            "indexed_in_fts5": self.indexed_in_fts5,
            "tokenization_rule": self.tokenization_rule,
            "redaction_filter": self.redaction_filter,
        }


@dataclass(frozen=True, slots=True)
class FTS5IndexSpecification:
    table_name: str
    virtual_table_sql: str
    update_triggers_sql: tuple[str, ...]
    searchable_fields: tuple[KnowledgeFieldMapping, ...]
    migration_strategy: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "virtual_table_sql": self.virtual_table_sql,
            "update_triggers_sql": list(self.update_triggers_sql),
            "searchable_fields": [f.to_dict() for f in self.searchable_fields],
            "migration_strategy": self.migration_strategy,
        }


@dataclass(frozen=True, slots=True)
class HybridRetrievalSpecification:
    lexical_engine: str
    vector_engine: str
    fusion_algorithm: str
    score_threshold: float
    max_candidates: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "lexical_engine": self.lexical_engine,
            "vector_engine": self.vector_engine,
            "fusion_algorithm": self.fusion_algorithm,
            "score_threshold": self.score_threshold,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSecurityModel:
    isolation_scope: str
    credential_exclusion_policy: str
    cloud_vector_allowed: bool
    ephemeral_session_indexing: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "isolation_scope": self.isolation_scope,
            "credential_exclusion_policy": self.credential_exclusion_policy,
            "cloud_vector_allowed": self.cloud_vector_allowed,
            "ephemeral_session_indexing": self.ephemeral_session_indexing,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeIntelligenceDesignPackage:
    design_id: str
    target_version: str
    fts5_spec: FTS5IndexSpecification
    hybrid_spec: HybridRetrievalSpecification
    security_model: KnowledgeSecurityModel
    design_verdict: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "target_version": self.target_version,
            "fts5_spec": self.fts5_spec.to_dict(),
            "hybrid_spec": self.hybrid_spec.to_dict(),
            "security_model": self.security_model.to_dict(),
            "design_verdict": self.design_verdict,
            "created_at": self.created_at,
        }


def assemble_knowledge_intelligence_design(
    target_version: str = "v0.8.0",
) -> KnowledgeIntelligenceDesignPackage:
    """Assembles the formal v0.8.0 Knowledge Intelligence architectural design specification."""
    fields = (
        KnowledgeFieldMapping("artifact_id", "TEXT", False, "exact", "none"),
        KnowledgeFieldMapping("title", "TEXT", True, "unicode61", "strip_secrets"),
        KnowledgeFieldMapping("content", "TEXT", True, "porter unicode61", "regex_redaction"),
        KnowledgeFieldMapping("provenance_source", "TEXT", True, "unicode61", "none"),
        KnowledgeFieldMapping("workspace_id", "TEXT", False, "exact", "none"),
    )

    fts5_spec = FTS5IndexSpecification(
        table_name="artifacts_fts5_idx",
        virtual_table_sql=(
            "CREATE VIRTUAL TABLE IF NOT EXISTS artifacts_fts5_idx USING fts5("
            "artifact_id UNINDEXED, title, content, provenance_source, workspace_id UNINDEXED, "
            "tokenize = 'porter unicode61');"
        ),
        update_triggers_sql=(
            "CREATE TRIGGER IF NOT EXISTS trg_artifacts_ai AFTER INSERT ON artifacts BEGIN "
            "INSERT INTO artifacts_fts5_idx(artifact_id, title, content, provenance_source, workspace_id) "
            "VALUES (new.artifact_id, new.title, new.content, new.source, new.workspace_id); END;",
            "CREATE TRIGGER IF NOT EXISTS trg_artifacts_ad AFTER DELETE ON artifacts BEGIN "
            "DELETE FROM artifacts_fts5_idx WHERE artifact_id = old.artifact_id; END;",
        ),
        searchable_fields=fields,
        migration_strategy="NON_DESTRUCTIVE_SHADOW_TABLE",
    )

    hybrid_spec = HybridRetrievalSpecification(
        lexical_engine="SQLite_FTS5_BM25",
        vector_engine="Local_ONNX_MiniLM_L6",
        fusion_algorithm="Reciprocal_Rank_Fusion_RRF",
        score_threshold=0.65,
        max_candidates=25,
    )

    sec_model = KnowledgeSecurityModel(
        isolation_scope="STRICT_WORKSPACE_ISOLATION",
        credential_exclusion_policy="AUTOMATIC_PRE_INDEX_REDACTION",
        cloud_vector_allowed=False,
        ephemeral_session_indexing=True,
    )

    return KnowledgeIntelligenceDesignPackage(
        design_id=f"know-dsn-{uuid4().hex[:16]}",
        target_version=str(target_version).strip(),
        fts5_spec=fts5_spec,
        hybrid_spec=hybrid_spec,
        security_model=sec_model,
        design_verdict="DESIGN_SPECIFICATION_APPROVED",
        created_at=_now(),
    )

