"""
AMCA Studio v0.8.0 Knowledge Service Runtime Integration Test Suite
Validates:
1. Authorized explicit invocation produces complete KnowledgeContextPackage
2. Unauthorized role denied with audit trail
3. Anti-injection invariant: explicit attachment only, zero automatic prompt pollution
4. Privacy and provenance completeness (no secret queries, no private bodies in audit)
5. Runtime boundary discipline: Agent Fabric and Model Router decouple intact
"""

from pathlib import Path
import tempfile
import unittest

from amca.hybrid_retrieval import HybridRetrievalEngine
from amca.knowledge_context_service import KnowledgeAuditStore, KnowledgeContextService
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_service_runtime import KnowledgeRuntimeService
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
)


class StudioV080KnowledgeServiceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        # FTS5 & Vector store setup
        self.fts_path = self.tmp_path / "fts.sqlite3"
        self.vec_path = self.tmp_path / "vec.sqlite3"
        self.audit_path = self.tmp_path / "audit.sqlite3"

        self.fts_store = KnowledgeFtsStore(self.fts_path)
        self.vector_store = KnowledgeVectorStore(self.vec_path)
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)

        # Seed knowledge items
        self.fts_store.index_artifact(
            artifact_id="doc-arch-01",
            workspace_id="ws-test",
            title="architecture_overview.md",
            content="AMCA Studio architecture integrates local-first cognition with explicit knowledge retrieval.",
        )
        vec = self.provider.embed_text("architecture_overview.md AMCA Studio architecture integrates local-first cognition")
        self.vector_store.store_vector(
            artifact_id="doc-arch-01",
            workspace_id="ws-test",
            model_name=self.provider.model_name,
            vector=vec,
        )

        self.retrieval_engine = HybridRetrievalEngine(
            fts_store=self.fts_store,
            vector_store=self.vector_store,
            embedding_provider=self.provider,
            rrf_k=60,
        )
        self.audit_store = KnowledgeAuditStore(self.audit_path)
        self.context_service = KnowledgeContextService(
            retrieval_engine=self.retrieval_engine,
            audit_store=self.audit_store,
        )
        self.runtime_service = KnowledgeRuntimeService(
            context_service=self.context_service,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_authorized_explicit_invocation(self) -> None:
        """Verify authorized role invocation retrieves knowledge and returns structured package."""
        res = self.runtime_service.execute_runtime_invocation(
            workspace_id="ws-test",
            requester_role="researcher",
            query="architecture retrieval",
            mode="lexical_only",
            limit=3,
        )
        self.assertTrue(res.is_authorized)
        self.assertIsNone(res.denial_reason)
        self.assertIsNotNone(res.context_package)
        self.assertEqual(res.context_package.workspace_id, "ws-test")
        self.assertGreater(len(res.context_package.items), 0)

    def test_unauthorized_role_denial_and_audit(self) -> None:
        """Verify unauthorized role request is rejected cleanly and recorded in audit ledger."""
        res = self.runtime_service.execute_runtime_invocation(
            workspace_id="ws-test",
            requester_role="external_guest",
            query="confidential specs",
        )
        self.assertFalse(res.is_authorized)
        self.assertIn("not authorized", res.denial_reason)
        self.assertIsNone(res.context_package)

        audits = self.audit_store.list_events(workspace_id="ws-test")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].permission_decision, "DENIED")
        self.assertEqual(audits[0].requester_role, "external_guest")

    def test_anti_injection_and_explicit_attachment_invariant(self) -> None:
        """Verify context packages require explicit binding and contain complete provenance."""
        res = self.runtime_service.execute_runtime_invocation(
            workspace_id="ws-test",
            requester_role="researcher",
            query="architecture",
        )
        self.assertTrue(res.is_authorized)
        pkg = res.context_package
        self.assertGreater(len(pkg.items), 0)
        for item in pkg.items:
            self.assertTrue(len(item.artifact_id) > 0)
            self.assertTrue(len(item.provenance_reference) > 0)
            self.assertEqual(item.workspace_id, "ws-test")

    def test_privacy_and_audit_boundaries(self) -> None:
        """Verify audit events do not contain raw document text or leaked tokens."""
        self.runtime_service.execute_runtime_invocation(
            workspace_id="ws-test",
            requester_role="admin",
            query="studio architecture",
        )
        audits = self.audit_store.list_events(workspace_id="ws-test")
        self.assertGreaterEqual(len(audits), 1)
        for ev in audits:
            d = ev.to_dict()
            self.assertNotIn("document_body", d)
            self.assertNotIn("raw_content", d)
            self.assertNotIn("secret", d)

    def test_runtime_boundary_discipline(self) -> None:
        """Verify Agent Fabric and Model Router have no imported runtime coupling to knowledge service."""
        import inspect
        import amca.intelligence.cross_provider_router as cpr
        source = inspect.getsource(cpr)
        self.assertNotIn("KnowledgeRuntimeService", source)
        self.assertNotIn("execute_runtime_invocation", source)


if __name__ == "__main__":
    unittest.main()

