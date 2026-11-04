"""
AMCA Studio v0.8.0 Knowledge Context Protocol Test Suite
Validates:
1. Retrieval: explicit request returns context package, provenance preserved
2. Security: workspace isolation, permission rejection (unauthorized roles)
3. Governance: audit record created, private content excluded from audit log
4. Boundary: no automatic injection, existing workflows unchanged
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.hybrid_retrieval import HybridRetrievalEngine
from amca.knowledge_context_service import (
    KnowledgeAuditStore,
    KnowledgeContextPackage,
    KnowledgeContextService,
)
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
)


class StudioV080KnowledgeContextProtocolTests(unittest.TestCase):
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
        self.service = KnowledgeContextService(
            retrieval_engine=self.engine,
            audit_store=self.audit_store,
        )

        # Seed sample knowledge
        self._seed_doc("doc-res-1", "ws-research", "Distributed Consensus Protocols", "Raft and Paxos state machines.")
        self._seed_doc("doc-res-2", "ws-research", "Formal Verification Methods", "TLA+ specifications and model checking.")

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

    def test_explicit_retrieval_returns_context_with_provenance(self) -> None:
        """Verify explicit request creates structured context package with intact provenance."""
        pkg = self.service.execute_explicit_knowledge_search(
            workspace_id="ws-research",
            requester_role="researcher",
            query="consensus state machines",
            intent="research_paper_synthesis",
        )
        self.assertTrue(pkg.package_id.startswith("ctx-pkg-"))
        self.assertEqual(pkg.requester_role, "researcher")
        self.assertGreaterEqual(len(pkg.items), 1)

        first = pkg.items[0]
        self.assertEqual(first.artifact_id, "doc-res-1")
        self.assertTrue(first.permission_verified)
        self.assertTrue(len(first.provenance_reference) > 0)
        self.assertGreater(first.relevance_score, 0.0)

    def test_security_workspace_isolation_and_permission_rejection(self) -> None:
        """Verify cross-workspace non-leakage and unauthorized role rejection."""
        # 1. Cross-workspace isolation
        pkg_other = self.service.execute_explicit_knowledge_search(
            workspace_id="ws-unrelated",
            requester_role="researcher",
            query="consensus",
        )
        self.assertEqual(len(pkg_other.items), 0)

        # 2. Unauthorized role rejection
        pkg_unauth = self.service.execute_explicit_knowledge_search(
            workspace_id="ws-research",
            requester_role="guest_untrusted",
            query="consensus",
        )
        self.assertEqual(len(pkg_unauth.items), 0)

        # Audit should show rejection
        events = self.audit_store.list_events(workspace_id="ws-research")
        unauth_events = [e for e in events if e.requester_role == "guest_untrusted"]
        self.assertEqual(len(unauth_events), 1)
        self.assertEqual(unauth_events[0].permission_decision, "DENIED")

    def test_governance_audit_created_and_private_content_excluded(self) -> None:
        """Verify audit trail is recorded without persisting raw query or private text bodies."""
        pkg = self.service.execute_explicit_knowledge_search(
            workspace_id="ws-research",
            requester_role="coder",
            query="TLA+ verification",
            intent="code_synthesis",
        )
        events = self.audit_store.list_events(workspace_id="ws-research")
        coder_events = [e for e in events if e.requester_role == "coder"]
        self.assertEqual(len(coder_events), 1)
        evt = coder_events[0]
        self.assertEqual(evt.action, "knowledge.search")
        self.assertEqual(evt.query_intent, "code_synthesis")
        self.assertEqual(evt.permission_decision, "ALLOWED")
        self.assertIn("doc-res-2", evt.retrieved_artifact_ids)

        # Confirm audit event schema does not contain excerpt or body fields
        evt_dict = evt.to_dict()
        self.assertNotIn("query_text", evt_dict)
        self.assertNotIn("content", evt_dict)
        self.assertNotIn("excerpt", evt_dict)

    def test_boundary_preservation_no_automatic_injection(self) -> None:
        """Verify Agent Fabric and Workflow runtime do not perform automatic injection."""
        import amca.agent_fabric as af
        source = inspect.getsource(af)
        self.assertNotIn("KnowledgeContextService", source)
        self.assertNotIn("execute_explicit_knowledge_search", source)


if __name__ == "__main__":
    unittest.main()

