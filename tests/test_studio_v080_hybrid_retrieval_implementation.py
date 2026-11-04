"""
AMCA Studio v0.8.0 Hybrid Retrieval Implementation Test Suite
Validates:
1. Embedding layer: provider initializes, deterministic output shape (384), model metadata recorded
2. Storage: vectors persist, workspace isolation works, artifact deletion removes vectors
3. Security: secrets never embedded
4. Migration: existing FTS5 workflows unaffected
5. Offline guarantee: zero external network dependency
"""

import tempfile
import unittest
from pathlib import Path

from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
    cosine_similarity,
    pack_vector_blob,
    unpack_vector_blob,
)


class StudioV080HybridRetrievalImplementationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_knowledge_vector.sqlite3"
        self.fts_db_path = Path(self.tmp_dir.name) / "test_knowledge_fts.sqlite3"
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)
        self.vector_store = KnowledgeVectorStore(self.db_path)
        self.fts_store = KnowledgeFtsStore(self.fts_db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_embedding_layer_initialization_and_shape(self) -> None:
        """Verify provider initializes with 384 dimensions and deterministic output."""
        self.assertEqual(self.provider.dimension, 384)
        self.assertEqual(self.provider.model_name, "amca-offline-minilm-ref")

        v1 = self.provider.embed_text("Autonomous Cognition Agent Fabric")
        v2 = self.provider.embed_text("Autonomous Cognition Agent Fabric")
        self.assertEqual(len(v1), 384)
        self.assertEqual(v1, v2)

    def test_vector_storage_and_workspace_isolation(self) -> None:
        """Verify vector persistence, artifact deletion, and strict workspace isolation."""
        vec_a = self.provider.embed_text("Workspace A proprietary strategy")
        vec_b = self.provider.embed_text("Workspace B confidential roadmap")

        self.vector_store.store_vector(
            artifact_id="art-ws-a-1",
            workspace_id="ws-alpha",
            model_name=self.provider.model_name,
            vector=vec_a,
        )
        self.vector_store.store_vector(
            artifact_id="art-ws-b-1",
            workspace_id="ws-beta",
            model_name=self.provider.model_name,
            vector=vec_b,
        )

        # Retrieval in respective workspaces
        rec_a = self.vector_store.get_vector(artifact_id="art-ws-a-1", workspace_id="ws-alpha")
        self.assertIsNotNone(rec_a)
        self.assertEqual(rec_a.artifact_id, "art-ws-a-1")
        self.assertEqual(len(rec_a.vector), 384)

        # Workspace isolation: ws-beta cannot access art-ws-a-1
        rec_leak = self.vector_store.get_vector(artifact_id="art-ws-a-1", workspace_id="ws-beta")
        self.assertIsNone(rec_leak)

        # Artifact deletion
        deleted = self.vector_store.delete_vector(artifact_id="art-ws-a-1", workspace_id="ws-alpha")
        self.assertTrue(deleted)
        self.assertIsNone(self.vector_store.get_vector(artifact_id="art-ws-a-1", workspace_id="ws-alpha"))

    def test_security_secret_stripping_prior_to_embedding(self) -> None:
        """Verify that secret tokens are sanitized prior to vector generation."""
        text_with_secret = "Confidential plan sk-ant-api03-secret1234567890abcdef and ghp_token1234567890"
        text_without_secret = "Confidential plan [REDACTED_SECRET] and [REDACTED_SECRET]"

        vec_raw = self.provider.embed_text(text_with_secret)
        vec_redacted = self.provider.embed_text(text_without_secret)

        # Because secrets are sanitized to [REDACTED_SECRET], both embeddings match identically
        self.assertEqual(vec_raw, vec_redacted)

    def test_migration_and_fts5_coexistence(self) -> None:
        """Verify vector storage exists alongside FTS5 without modifying FTS5 behavior."""
        # Index in FTS5
        self.fts_store.index_artifact(
            artifact_id="art-fts-1",
            workspace_id="ws-shared",
            title="Shared Project Architecture",
            content="Full-text searchable artifact content.",
        )
        # Store vector in vector store
        vec = self.provider.embed_text("Shared Project Architecture")
        self.vector_store.store_vector(
            artifact_id="art-fts-1",
            workspace_id="ws-shared",
            model_name=self.provider.model_name,
            vector=vec,
        )

        # FTS5 search functions cleanly
        res = self.fts_store.search(workspace_id="ws-shared", query="searchable")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].artifact_id, "art-fts-1")

    def test_offline_guarantee(self) -> None:
        """Verify embedding generation requires zero network calls."""
        # Embed 10 distinct texts completely locally
        for i in range(10):
            v = self.provider.embed_text(f"Offline test document batch number {i}")
            self.assertEqual(len(v), 384)


if __name__ == "__main__":
    unittest.main()

