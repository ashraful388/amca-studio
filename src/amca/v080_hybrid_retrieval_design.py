"""AMCA Studio v0.8.0 Hybrid Retrieval Architecture Design (AG-AMCA-STUDIO-V0.8.0).

Defines architectural models, embedding selection trade-offs, vector storage strategies,
hybrid fusion algorithms, and air-gapped security boundaries:
1. Local ONNX embedding candidate evaluation (MiniLM-L6-v2 vs BGE-small)
2. Vector storage options (sqlite-vec vs float32 blob arrays)
3. Reciprocal Rank Fusion (RRF) pipeline specification
4. Hardware tier recommendations (low-end CPU, standard desktop, high-performance)
5. Strict air-gapped privacy boundaries and pre-embedding credential redaction

Strictly architecture analysis, specification modeling, and verification;
zero runtime vector models or external cloud dependencies introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class EmbeddingModelCandidate:
    model_name: str
    dimension: int
    parameter_size_mb: float
    cpu_latency_ms: float
    recommended_hardware: str
    offline_supported: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "dimension": self.dimension,
            "parameter_size_mb": self.parameter_size_mb,
            "cpu_latency_ms": self.cpu_latency_ms,
            "recommended_hardware": self.recommended_hardware,
            "offline_supported": self.offline_supported,
        }


@dataclass(frozen=True, slots=True)
class VectorStorageOption:
    option_id: str
    name: str
    storage_type: str
    migration_complexity: str
    hardware_footprint: str
    workspace_isolation_method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "name": self.name,
            "storage_type": self.storage_type,
            "migration_complexity": self.migration_complexity,
            "hardware_footprint": self.hardware_footprint,
            "workspace_isolation_method": self.workspace_isolation_method,
        }


@dataclass(frozen=True, slots=True)
class HybridPipelineSpecification:
    pipeline_id: str
    lexical_primary_engine: str
    semantic_secondary_engine: str
    fusion_algorithm: str
    rrf_k_parameter: int
    score_cut_off: float
    max_return_candidates: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "lexical_primary_engine": self.lexical_primary_engine,
            "semantic_secondary_engine": self.semantic_secondary_engine,
            "fusion_algorithm": self.fusion_algorithm,
            "rrf_k_parameter": self.rrf_k_parameter,
            "score_cut_off": self.score_cut_off,
            "max_return_candidates": self.max_return_candidates,
        }


@dataclass(frozen=True, slots=True)
class HybridSecuritySpecification:
    redaction_policy: str
    cloud_embedding_permitted: bool
    isolated_by_workspace: bool
    private_memory_exclusion: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "redaction_policy": self.redaction_policy,
            "cloud_embedding_permitted": self.cloud_embedding_permitted,
            "isolated_by_workspace": self.isolated_by_workspace,
            "private_memory_exclusion": self.private_memory_exclusion,
        }


@dataclass(frozen=True, slots=True)
class HybridRetrievalDesignPackage:
    design_id: str
    target_version: str
    candidates: tuple[EmbeddingModelCandidate, ...]
    storage_options: tuple[VectorStorageOption, ...]
    pipeline: HybridPipelineSpecification
    security: HybridSecuritySpecification
    overall_status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "target_version": self.target_version,
            "candidates": [c.to_dict() for c in self.candidates],
            "storage_options": [s.to_dict() for s in self.storage_options],
            "pipeline": self.pipeline.to_dict(),
            "security": self.security.to_dict(),
            "overall_status": self.overall_status,
            "created_at": self.created_at,
        }


def assemble_hybrid_retrieval_design(
    target_version: str = "v0.8.0",
) -> HybridRetrievalDesignPackage:
    """Assembles the formal v0.8.0 Hybrid Retrieval architectural design specification."""
    candidates = (
        EmbeddingModelCandidate(
            model_name="all-MiniLM-L6-v2-quantized",
            dimension=384,
            parameter_size_mb=23.0,
            cpu_latency_ms=12.5,
            recommended_hardware="Low-end / Standard Desktop (CPU-only)",
            offline_supported=True,
        ),
        EmbeddingModelCandidate(
            model_name="bge-small-en-v1.5",
            dimension=384,
            parameter_size_mb=67.0,
            cpu_latency_ms=28.0,
            recommended_hardware="High-Performance Desktop / Optional GPU",
            offline_supported=True,
        ),
    )

    storage_options = (
        VectorStorageOption(
            option_id="vec-opt-01",
            name="SQLite Native BLOB Array + Brute-force Cosine",
            storage_type="sqlite3_blob",
            migration_complexity="Low",
            hardware_footprint="Minimal (< 1MB overhead)",
            workspace_isolation_method="workspace_id indexed column filter",
        ),
        VectorStorageOption(
            option_id="vec-opt-02",
            name="sqlite-vec Extension Virtual Tables",
            storage_type="virtual_table",
            migration_complexity="Medium",
            hardware_footprint="Moderate (C-extension dynamic loading)",
            workspace_isolation_method="workspace_id partition column",
        ),
    )

    pipeline = HybridPipelineSpecification(
        pipeline_id="pipe-hybrid-v08",
        lexical_primary_engine="SQLite_FTS5",
        semantic_secondary_engine="Local_ONNX",
        fusion_algorithm="RRF",
        rrf_k_parameter=60,
        score_cut_off=0.015,
        max_return_candidates=25,
    )

    security = HybridSecuritySpecification(
        redaction_policy="PRE_EMBEDDING_SECRET_REDACTION_AND_STRIPPING",
        cloud_embedding_permitted=False,
        isolated_by_workspace=True,
        private_memory_exclusion=True,
    )

    return HybridRetrievalDesignPackage(
        design_id=f"hyb-dsn-{uuid4().hex[:16]}",
        target_version=str(target_version).strip(),
        candidates=candidates,
        storage_options=storage_options,
        pipeline=pipeline,
        security=security,
        overall_status="HYBRID_DESIGN_SPECIFIED",
        created_at=_now(),
    )

