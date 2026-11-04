"""
AMCA Studio v0.8.0 Knowledge-Assisted Workflow Validation Test Suite
Validates:
1. Research: retrieval request succeeds, provenance preserved
2. Software workflow: context available, permissions enforced
3. Document workflow: artifact references preserved
4. Privacy: no private content stored
5. Boundary: no automatic injection
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.hybrid_retrieval import HybridRetrievalEngine
from amca.knowledge_context_service import (
    KnowledgeAuditStore,
    KnowledgeContextService,
)
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
)
from amca.v080_workflow_validation import WorkflowValidationRunner


class StudioV080KnowledgeAssistedWorkflowValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.fts_path = Path(self.tmp_dir.name) / "fts.sqlite3"
        self.vector_path = Path(self.tmp_dir.name) / "vec.sqlite3"
        self.audit_path = Path(self.tmp_dir.name) / "audit.sqlite3"

        self.fts_store = KnowledgeFtsStore(self.fts_path)
        self.vector_store = KnowledgeVectorStore(self.vector_path)
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)
        self.engine = HybridRetrievalEngine(
            fts_store=self.fts_store,
            vector_store=self.vector_store,
            embedding_provider=self.provider,
            rrf_k=60,
        )
        self.audit_store = KnowledgeAuditStore(self.audit_path)
        self.context_service = KnowledgeContextService(
            retrieval_engine=self.engine,
            audit_store=self.audit_store,
        )
        self.runner = WorkflowValidationRunner(self.context_service)

        # Seed test documents
        self._seed_doc("doc-ai-align", "ws-val", "AI Safety and Alignment Standards", "Value alignment, RLHF, and boundary guards.")
        self._seed_doc("doc-arch-v8", "ws-val", "AMCA v0.8.0 Architecture Spec", "Distributed agent fabric and hybrid retrieval.")
        self._seed_doc("doc-pol-01", "ws-val", "Enterprise Compliance Policy", "Zero cloud leakage and air-gap deployment rules.")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _seed_doc(self, artifact_id: str, workspace_id: str, title: str, content: str) -> None:
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

    def test_research_workflow_scenario(self) -> None:
        """Verify research workflow retrieval succeeds with intact provenance."""
        res = self.runner.run_research_workflow_scenario(
            workspace_id="ws-val",
            query="safety alignment guards",
        )
        self.assertEqual(res.status, "PASS")
        self.assertTrue(res.context_available)
        self.assertTrue(res.provenance_preserved)
        self.assertIn("doc-ai-align", res.usage_record.artifact_references)

    def test_software_engineering_workflow_scenario(self) -> None:
        """Verify software workflow context is available and permissions are enforced."""
        # 1. Authorized coder succeeds
        res_coder = self.runner.run_software_engineering_scenario(
            workspace_id="ws-val",
            query="distributed agent fabric",
            requester_role="coder",
        )
        self.assertEqual(res_coder.status, "PASS")
        self.assertTrue(res_coder.context_available)
        self.assertEqual(res_coder.usage_record.permission_result, "ALLOWED")
        self.assertIn("doc-arch-v8", res_coder.usage_record.artifact_references)

        # 2. Unauthorized role rejected
        res_unauth = self.runner.run_software_engineering_scenario(
            workspace_id="ws-val",
            query="distributed agent fabric",
            requester_role="external_bot",
        )
        self.assertFalse(res_unauth.context_available)
        self.assertEqual(res_unauth.usage_record.permission_result, "DENIED")

    def test_document_productivity_scenario(self) -> None:
        """Verify document workflow policy lookup preserves artifact references."""
        res = self.runner.run_document_productivity_scenario(
            workspace_id="ws-val",
            query="compliance policy air-gap",
        )
        self.assertEqual(res.status, "PASS")
        self.assertTrue(res.context_available)
        self.assertTrue(res.provenance_preserved)
        self.assertIn("doc-pol-01", res.usage_record.artifact_references)

    def test_privacy_guarantee_no_sensitive_body_persisted(self) -> None:
        """Verify usage record stores only metadata, never private body excerpts."""
        res = self.runner.run_research_workflow_scenario(
            workspace_id="ws-val",
            query="safety alignment",
        )
        rec_dict = res.usage_record.to_dict()
        self.assertNotIn("excerpt", rec_dict)
        self.assertNotIn("body", rec_dict)
        self.assertNotIn("content", rec_dict)
        self.assertNotIn("query_string", rec_dict)

    def test_boundary_preservation_no_automatic_injection(self) -> None:
        """Verify Agent Fabric runtime is not modified or automatically injected."""
        import amca.agent_fabric as af
        source = inspect.getsource(af)
        self.assertNotIn("WorkflowValidationRunner", source)
        self.assertNotIn("run_research_workflow_scenario", source)


if __name__ == "__main__":
    unittest.main()

