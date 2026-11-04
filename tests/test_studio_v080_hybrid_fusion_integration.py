"""
AMCA Studio v0.8.0 Hybrid Fusion Integration Test Suite
Validates:
1. Fusion: lexical-only, semantic-only, combined hybrid RRF ranking and deterministic ordering
2. Isolation: workspace partitioning across all modes
3. Security: secret filtering and exclusion
4. Compatibility: lexical-only default mode preserves backward compatibility
5. Failure Handling: missing vectors, empty semantic index, unavailable provider gracefully falls back
"""

import tempfile
import unittest
from pathlib import Path

from amca.hybrid_retrieval import HybridRetrievalEngine, UnifiedRetrievalResult
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
)


class StudioV080HybridFusionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.fts_path = Path(self.tmp_dir.name) / "fts.sqlite3"
        self.vector_path = Path(self.tmp_dir.name) / "vec.sqlite3"
        self.fts_store = KnowledgeFtsStore(self.fts_path)
        self.vector_store = KnowledgeVectorStore(self.vector_path)
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)
        self.engine = HybridRetrievalEngine(
            fts_store=self.fts_store,
            vector_store=self.vector_store,
            embedding_provider=self.provider,
            rrf_k=60,
        )

        # Seed test documents
        self._seed_artifact("art-1", "ws-prod", "Neural Network Architecture", "Deep learning transformer models.")
        self._seed_artifact("art-2", "ws-prod", "Quantum Computing Principles", "Superposition qubits quantum gates.")
        self._seed_artifact("art-3", "ws-prod", "Neural Quantum Hybrid", "Deep learning models running on quantum circuits.")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _seed_artifact(self, artifact_id: str, workspace_id: str, title: str, content: str) -> None:
        self.fts_store.index_artifact(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            title=title,
            content=content,
        )
        vec = self.provider.embed_text(f"{title} {content}")
        self.vector_store.store_vector(
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            model_name=self.provider.model_name,
            vector=vec,
        )

    def test_fusion_modes_and_deterministic_rrf_ordering(self) -> None:
        """Verify lexical_only, semantic_only, and hybrid_rrf fused ranking."""
        # 1. Lexical only
        lex_results, diag_lex = self.engine.retrieve(
            workspace_id="ws-prod",
            query="quantum",
            mode="lexical_only",
        )
        self.assertGreaterEqual(len(lex_results), 2)
        for r in lex_results:
            self.assertEqual(r.retrieval_mode, "lexical_only")
            self.assertIsNotNone(r.lexical_rank)
            self.assertIsNone(r.semantic_rank)

        # 2. Semantic only
        sem_results, diag_sem = self.engine.retrieve(
            workspace_id="ws-prod",
            query="quantum",
            mode="semantic_only",
        )
        self.assertGreaterEqual(len(sem_results), 2)
        for r in sem_results:
            self.assertEqual(r.retrieval_mode, "semantic_only")
            self.assertIsNone(r.lexical_rank)
            self.assertIsNotNone(r.semantic_rank)

        # 3. Hybrid RRF fusion
        fused_results, diag_hyb = self.engine.retrieve(
            workspace_id="ws-prod",
            query="quantum neural",
            mode="hybrid_rrf",
        )
        self.assertGreaterEqual(len(fused_results), 1)
        for r in fused_results:
            self.assertEqual(r.retrieval_mode, "hybrid_rrf")
            self.assertGreater(r.rrf_score, 0.0)

        # Art-3 matches both quantum and neural, so it should rank first with highest RRF score
        top_art = fused_results[0]
        self.assertEqual(top_art.artifact_id, "art-3")
        self.assertIsNotNone(top_art.lexical_rank)
        self.assertIsNotNone(top_art.semantic_rank)

    def test_workspace_isolation_across_retrieval_modes(self) -> None:
        """Verify results from ws-prod never leak into ws-other."""
        self._seed_artifact("art-tenant-b", "ws-other", "Confidential Strategy", "Other tenant content.")

        for mode in ("lexical_only", "semantic_only", "hybrid_rrf"):
            results, _ = self.engine.retrieve(
                workspace_id="ws-other",
                query="quantum",
                mode=mode,  # type: ignore[arg-type]
            )
            # Must never return ws-prod artifacts
            matched_ids = {r.artifact_id for r in results}
            self.assertNotIn("art-1", matched_ids)
            self.assertNotIn("art-2", matched_ids)
            self.assertNotIn("art-3", matched_ids)

    def test_security_sanitization_integrity(self) -> None:
        """Verify credentials remain sanitized and excluded from search returns."""
        self._seed_artifact(
            "art-sec",
            "ws-prod",
            "API Credential Audit",
            "Secret token sk-ant-secret1234567890abcdef and password=SuperSecretPassword123!",
        )
        results, _ = self.engine.retrieve(
            workspace_id="ws-prod",
            query="sk-ant-secret1234567890abcdef",
            mode="hybrid_rrf",
        )
        self.assertEqual(len(results), 0)

    def test_backward_compatibility_default_mode(self) -> None:
        """Verify retrieve defaults to lexical_only preserving exact existing behavior."""
        results, diag = self.engine.retrieve(workspace_id="ws-prod", query="Neural")
        self.assertEqual(diag.retrieval_mode_used, "lexical_only")
        self.assertFalse(diag.fallback_triggered)
        self.assertGreaterEqual(len(results), 1)

    def test_graceful_fallback_handling(self) -> None:
        """Verify engine gracefully falls back to FTS5 when vector provider or embeddings are absent."""
        engine_no_vector = HybridRetrievalEngine(
            fts_store=self.fts_store,
            vector_store=self.vector_store,
            embedding_provider=None,  # No embedding provider
        )

        # Hybrid mode request without provider falls back to lexical
        results, diag = engine_no_vector.retrieve(
            workspace_id="ws-prod",
            query="Computing",
            mode="hybrid_rrf",
        )
        self.assertTrue(diag.fallback_triggered)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].artifact_id, "art-2")


if __name__ == "__main__":
    unittest.main()

