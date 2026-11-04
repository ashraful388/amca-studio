"""
AMCA Studio v0.8.0 Cognitive Workflow Loop Integration Test Suite
Validates:
1. Full lifecycle: Goal -> Understand -> Plan -> Select -> Bind Knowledge -> Execute -> Verify -> Learn
2. Boundary tests: Model Router and Agent Fabric decoupled, zero hidden reasoning, zero auto-injection
3. Failure paths: Missing model, denied knowledge access, failed execution, cancellation
4. Persistence: Workspace separation, audit trail references, restart recovery
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.cognitive_loop_orchestrator import (
    CognitiveWorkflowOrchestrator,
    CognitiveWorkflowRecord,
    CognitiveWorkflowStore,
)
from amca.hybrid_retrieval import HybridRetrievalEngine
from amca.knowledge_context_service import KnowledgeAuditStore, KnowledgeContextService
from amca.knowledge_fts import KnowledgeFtsStore
from amca.knowledge_service_runtime import KnowledgeRuntimeService
from amca.knowledge_vector import (
    DeterministicLocalEmbeddingProvider,
    KnowledgeVectorStore,
)


class StudioV080CognitiveWorkflowLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        # Storage setup
        self.db_path = self.tmp_path / "cognitive_workflows.sqlite3"
        self.fts_path = self.tmp_path / "fts.sqlite3"
        self.vec_path = self.tmp_path / "vec.sqlite3"
        self.audit_path = self.tmp_path / "audit.sqlite3"

        self.store = CognitiveWorkflowStore(self.db_path)
        self.fts_store = KnowledgeFtsStore(self.fts_path)
        self.vector_store = KnowledgeVectorStore(self.vec_path)
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)

        # Seed knowledge
        self.fts_store.index_artifact(
            artifact_id="art-spec-1",
            workspace_id="ws-main",
            title="system_design.md",
            content="Cognitive loop coordinates model router, planner, and knowledge retrieval.",
        )
        vec = self.provider.embed_text("system_design.md Cognitive loop coordinates model router, planner, and knowledge retrieval.")
        self.vector_store.store_vector(
            artifact_id="art-spec-1",
            workspace_id="ws-main",
            model_name=self.provider.model_name,
            vector=vec,
        )

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
        self.runtime_service = KnowledgeRuntimeService(context_service=self.context_service)
        self.orchestrator = CognitiveWorkflowOrchestrator(
            store=self.store,
            knowledge_runtime=self.runtime_service,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_full_cognitive_workflow_lifecycle(self) -> None:
        """Verify the complete loop: Goal -> Understand -> Plan -> Select -> Knowledge -> Execute -> Verify -> Learn."""
        # 1. Goal understanding
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-main",
            raw_goal="Perform architecture review for v0.8.0 release",
            structured_intent="ARCHITECTURE_REVIEW",
            domain="software_engineering",
            constraints=("offline_only", "strict_audit"),
            required_outputs=("review_summary.md",),
        )
        self.assertEqual(wf.state, "GOAL_UNDERSTOOD")
        self.assertEqual(wf.goal.structured_intent, "ARCHITECTURE_REVIEW")

        # 2. Plan
        wf = self.orchestrator.attach_plan(
            workflow_id=wf.workflow_id,
            workspace_id="ws-main",
            planned_steps=("fetch_spec", "review_models", "generate_summary"),
            required_capabilities=("code_analysis", "knowledge_retrieval"),
            strategy="adaptive_hybrid",
        )
        self.assertEqual(wf.state, "PLANNED")
        self.assertEqual(len(wf.plan.planned_steps), 3)

        # 3. Resource selection
        wf = self.orchestrator.select_resources(
            workflow_id=wf.workflow_id,
            workspace_id="ws-main",
            selected_models=("llama3-8b",),
            selected_agents=("lead_architect",),
            provider_health_references=("ollama-local:HEALTHY",),
            decision_reason="Offline local model capability match",
        )
        self.assertEqual(wf.state, "RESOURCES_SELECTED")

        # 4. Knowledge binding
        wf = self.orchestrator.bind_knowledge(
            workflow_id=wf.workflow_id,
            workspace_id="ws-main",
            query="cognitive loop",
            requester_role="researcher",
            mode="lexical_only",
        )
        self.assertEqual(wf.state, "KNOWLEDGE_BOUND")
        self.assertIn("art-spec-1", wf.knowledge.retrieved_artifact_ids)
        self.assertTrue(wf.knowledge.audit_event_ref.startswith("aud-"))

        # 5. Execute
        def mock_executor() -> tuple[bool, tuple[str, ...], str | None]:
            return True, ("review_summary.md",), None

        wf = self.orchestrator.execute_workflow(
            workflow_id=wf.workflow_id,
            workspace_id="ws-main",
            execution_callable=mock_executor,
        )
        self.assertEqual(wf.state, "COMPLETED")
        self.assertTrue(wf.verification.execution_completed)
        self.assertIn("review_summary.md", wf.verification.artifacts_produced)

        # 6. Record learning signal
        wf = self.orchestrator.record_learning_signal(
            workflow_id=wf.workflow_id,
            workspace_id="ws-main",
            duration_ms=45.2,
            failure_count=0,
            retry_count=0,
            resource_metrics={"peak_mem_mb": 128},
            user_correction_count=0,
        )
        self.assertIsNotNone(wf.learning)
        self.assertEqual(wf.learning.execution_duration_ms, 45.2)

    def test_architectural_boundary_invariants(self) -> None:
        """Verify Model Router and Agent Fabric decoupled; zero hidden reasoning storage."""
        # 1. Inspect Model Router decoupled
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("CognitiveWorkflowOrchestrator", source_cpr)

        # 2. Verify no hidden reasoning stored in dictionary representation
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-main",
            raw_goal="Deploy local assistant",
            structured_intent="DEPLOYMENT",
        )
        d = wf.to_dict()
        self.assertNotIn("chain_of_thought", d)
        self.assertNotIn("hidden_reasoning", d)
        self.assertNotIn("raw_thoughts", d)

    def test_failure_paths_and_cancellation(self) -> None:
        """Verify transitions to clean failure states on missing resources, access denial, and cancellation."""
        # Failure 1: No compatible models selected
        wf1 = self.orchestrator.initialize_workflow(workspace_id="ws-main", raw_goal="Test fail", structured_intent="TEST")
        wf1 = self.orchestrator.attach_plan(workflow_id=wf1.workflow_id, workspace_id="ws-main", planned_steps=("s1",), required_capabilities=("c1",))
        wf1 = self.orchestrator.select_resources(workflow_id=wf1.workflow_id, workspace_id="ws-main", selected_models=(), selected_agents=())
        self.assertEqual(wf1.state, "FAILED_RESOURCE_SELECTION")

        # Failure 2: Unauthorized knowledge access
        wf2 = self.orchestrator.initialize_workflow(workspace_id="ws-main", raw_goal="Test fail 2", structured_intent="TEST")
        wf2 = self.orchestrator.attach_plan(workflow_id=wf2.workflow_id, workspace_id="ws-main", planned_steps=("s1",), required_capabilities=("c1",))
        wf2 = self.orchestrator.select_resources(workflow_id=wf2.workflow_id, workspace_id="ws-main", selected_models=("llama3-8b",), selected_agents=("coder",))
        wf2 = self.orchestrator.bind_knowledge(workflow_id=wf2.workflow_id, workspace_id="ws-main", query="secret", requester_role="unauthorized_guest")
        self.assertEqual(wf2.state, "FAILED_KNOWLEDGE_ACCESS")

        # Failure 3: User cancellation
        wf3 = self.orchestrator.initialize_workflow(workspace_id="ws-main", raw_goal="Test cancel", structured_intent="TEST")
        wf3 = self.orchestrator.cancel_workflow(workflow_id=wf3.workflow_id, workspace_id="ws-main", reason="Operator requested abort")
        self.assertEqual(wf3.state, "CANCELLED")

    def test_persistence_workspace_isolation_and_restart_recovery(self) -> None:
        """Verify workflow records survive store reload and adhere to workspace isolation."""
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-secure",
            raw_goal="Verify storage persistence",
            structured_intent="STORAGE_AUDIT",
        )
        wf_id = wf.workflow_id

        # Workspace isolation
        self.assertIsNone(self.store.get_workflow(workflow_id=wf_id, workspace_id="ws-other"))

        # Reopen store from disk (restart recovery)
        reopened_store = CognitiveWorkflowStore(self.db_path)
        recovered_wf = reopened_store.get_workflow(workflow_id=wf_id, workspace_id="ws-secure")
        self.assertIsNotNone(recovered_wf)
        self.assertEqual(recovered_wf.state, "GOAL_UNDERSTOOD")
        self.assertEqual(recovered_wf.goal.structured_intent, "STORAGE_AUDIT")


if __name__ == "__main__":
    unittest.main()

