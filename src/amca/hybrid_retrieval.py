"""AMCA Studio v0.8.0 Hybrid Knowledge Retrieval Engine (AG-AMCA-STUDIO-V0.8.0).

Integrates SQLite FTS5 BM25 lexical search and offline vector cosine similarity
via Reciprocal Rank Fusion (RRF).
Supports explicit retrieval modes ("lexical_only", "semantic_only", "hybrid_rrf"),
graceful fallback, strict workspace isolation, and local performance telemetry.
100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Sequence
import time

from amca.knowledge_fts import KnowledgeFtsStore, KnowledgeSearchResult
from amca.knowledge_vector import (
    KnowledgeVectorStore,
    LocalEmbeddingProvider,
    cosine_similarity,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


RetrievalMode = Literal["lexical_only", "semantic_only", "hybrid_rrf"]


@dataclass(frozen=True, slots=True)
class UnifiedRetrievalResult:
    artifact_id: str
    workspace_id: str
    title: str
    snippet: str
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    retrieval_mode: str
    provenance_reference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "snippet": self.snippet,
            "rrf_score": self.rrf_score,
            "lexical_rank": self.lexical_rank,
            "semantic_rank": self.semantic_rank,
            "retrieval_mode": self.retrieval_mode,
            "provenance_reference": self.provenance_reference,
        }


@dataclass(frozen=True, slots=True)
class RetrievalDiagnostics:
    query_latency_ms: float
    candidate_count: int
    retrieval_mode_used: str
    fallback_triggered: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_latency_ms": self.query_latency_ms,
            "candidate_count": self.candidate_count,
            "retrieval_mode_used": self.retrieval_mode_used,
            "fallback_triggered": self.fallback_triggered,
        }


class HybridRetrievalEngine:
    """Combines lexical FTS5 and offline vector embeddings using Reciprocal Rank Fusion."""

    def __init__(
        self,
        *,
        fts_store: KnowledgeFtsStore,
        vector_store: KnowledgeVectorStore,
        embedding_provider: LocalEmbeddingProvider | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.fts_store = fts_store
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.rrf_k = max(1, rrf_k)

    def retrieve(
        self,
        *,
        workspace_id: str,
        query: str,
        mode: RetrievalMode = "lexical_only",
        limit: int = 20,
    ) -> tuple[list[UnifiedRetrievalResult], RetrievalDiagnostics]:
        start_t = time.perf_counter()
        clean_query = str(query).strip()
        if not clean_query:
            diag = RetrievalDiagnostics(0.0, 0, mode, False)
            return [], diag

        # Security check: Strip credential patterns from search queries
        from amca.knowledge_fts import SECRET_PATTERNS, sanitize_knowledge_text
        has_secrets = any(pat.search(clean_query) for pat in SECRET_PATTERNS)
        safe_query = sanitize_knowledge_text(clean_query).replace("[REDACTED_SECRET]", "").strip()
        if has_secrets and not safe_query:
            # Pure credential pattern query is rejected
            diag = RetrievalDiagnostics(0.0, 0, mode, False)
            return [], diag
        if safe_query:
            clean_query = safe_query

        # Mode 1: Lexical only (Default, guaranteed backward compatibility)
        if mode == "lexical_only":
            lex_results = self.fts_store.search(
                workspace_id=workspace_id,
                query=clean_query,
                limit=limit,
            )
            out: list[UnifiedRetrievalResult] = []
            for rank, item in enumerate(lex_results, start=1):
                out.append(
                    UnifiedRetrievalResult(
                        artifact_id=item.artifact_id,
                        workspace_id=item.workspace_id,
                        title=item.title,
                        snippet=item.snippet,
                        rrf_score=1.0 / (self.rrf_k + rank),
                        lexical_rank=rank,
                        semantic_rank=None,
                        retrieval_mode="lexical_only",
                        provenance_reference=item.provenance_reference,
                    )
                )
            latency = (time.perf_counter() - start_t) * 1000.0
            return out, RetrievalDiagnostics(latency, len(out), "lexical_only", False)

        # Mode 2: Semantic only
        if mode == "semantic_only":
            if self.embedding_provider is None:
                return self.retrieve(workspace_id=workspace_id, query=clean_query, mode="lexical_only", limit=limit)

            sem_results = self._search_semantic(workspace_id=workspace_id, query=clean_query, limit=limit)
            if not sem_results:
                res, _ = self.retrieve(workspace_id=workspace_id, query=clean_query, mode="lexical_only", limit=limit)
                latency = (time.perf_counter() - start_t) * 1000.0
                return res, RetrievalDiagnostics(latency, len(res), "semantic_only", True)

            out = []
            for rank, (art_id, _) in enumerate(sem_results, start=1):
                out.append(
                    UnifiedRetrievalResult(
                        artifact_id=art_id,
                        workspace_id=workspace_id,
                        title=f"Artifact {art_id}",
                        snippet="[Semantic vector match]",
                        rrf_score=1.0 / (self.rrf_k + rank),
                        lexical_rank=None,
                        semantic_rank=rank,
                        retrieval_mode="semantic_only",
                        provenance_reference="vector_store",
                    )
                )
            latency = (time.perf_counter() - start_t) * 1000.0
            return out, RetrievalDiagnostics(latency, len(out), "semantic_only", False)

        # Mode 3: Hybrid RRF Fusion
        if mode == "hybrid_rrf":
            lex_results = self.fts_store.search(
                workspace_id=workspace_id,
                query=clean_query,
                limit=limit * 2,
            )

            fallback_needed = self.embedding_provider is None
            sem_results: list[tuple[str, float]] = []
            if not fallback_needed:
                try:
                    sem_results = self._search_semantic(workspace_id=workspace_id, query=clean_query, limit=limit * 2)
                    if not sem_results:
                        fallback_needed = True
                except Exception:
                    fallback_needed = True

            if fallback_needed:
                res, _ = self.retrieve(workspace_id=workspace_id, query=clean_query, mode="lexical_only", limit=limit)
                latency = (time.perf_counter() - start_t) * 1000.0
                return res, RetrievalDiagnostics(latency, len(res), "hybrid_rrf", True)

            lex_rank_map: dict[str, int] = {item.artifact_id: idx for idx, item in enumerate(lex_results, start=1)}
            sem_rank_map: dict[str, int] = {art_id: idx for idx, (art_id, _) in enumerate(sem_results, start=1)}
            lex_dict = {item.artifact_id: item for item in lex_results}

            all_ids = set(lex_rank_map.keys()) | set(sem_rank_map.keys())
            rrf_scores: list[tuple[str, float, int | None, int | None]] = []

            for art_id in all_ids:
                l_rank = lex_rank_map.get(art_id)
                s_rank = sem_rank_map.get(art_id)
                score = 0.0
                if l_rank is not None:
                    score += 1.0 / (self.rrf_k + l_rank)
                if s_rank is not None:
                    score += 1.0 / (self.rrf_k + s_rank)
                rrf_scores.append((art_id, score, l_rank, s_rank))

            rrf_scores.sort(key=lambda x: (-x[1], x[2] or 999999, x[0]))

            fused_results: list[UnifiedRetrievalResult] = []
            for art_id, score, l_rank, s_rank in rrf_scores[:limit]:
                lex_item = lex_dict.get(art_id)
                title = lex_item.title if lex_item else f"Artifact {art_id}"
                snippet = lex_item.snippet if lex_item else "[Semantic vector match]"
                source = lex_item.provenance_reference if lex_item else "vector_store"
                fused_results.append(
                    UnifiedRetrievalResult(
                        artifact_id=art_id,
                        workspace_id=workspace_id,
                        title=title,
                        snippet=snippet,
                        rrf_score=round(score, 6),
                        lexical_rank=l_rank,
                        semantic_rank=s_rank,
                        retrieval_mode="hybrid_rrf",
                        provenance_reference=source,
                    )
                )

            latency = (time.perf_counter() - start_t) * 1000.0
            return fused_results, RetrievalDiagnostics(latency, len(fused_results), "hybrid_rrf", False)

        raise ValueError(f"Unknown retrieval mode: {mode}")

    def _search_semantic(self, *, workspace_id: str, query: str, limit: int) -> list[tuple[str, float]]:
        if self.embedding_provider is None:
            return []
        query_vec = self.embedding_provider.embed_text(query)
        records = self.vector_store.list_workspace_vectors(workspace_id=workspace_id)
        if not records:
            return []

        scored: list[tuple[str, float]] = []
        for r in records:
            sim = cosine_similarity(query_vec, r.vector)
            scored.append((r.artifact_id, sim))

        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:limit]

