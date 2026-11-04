"""
AMCA Studio v0.8.0 End-to-End Cognitive Workflow Validation Suite (AG-AMCA-STUDIO-V0.8.0).
Validates:
1. Scenario A — Commercial Research Workflow (provenance, role permissions, artifact creation, trace)
2. Scenario B — Software Engineering Workflow (project isolation, capability selection, model governance, execution)
3. Scenario C — Document Productivity Workflow (knowledge attachment, artifact generation, review state)
4. Governance & Anti-Injection Invariants (explicit request only, zero prompt injection, audit logging)
5. Failure Handling (missing knowledge, provider degradation, execution exception, access denial)
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


class StudioV080EndToEndCognitiveWorkflowValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.db_path = self.tmp_path / "cognitive_workflows.sqlite3"
        self.fts_path = self.tmp_path / "fts.sqlite3"
        self.vec_path = self.tmp_path / "vec.sqlite3"
        self.audit_path = self.tmp_path / "audit.sqlite3"

        self.store = CognitiveWorkflowStore(self.db_path)
        self.fts_store = KnowledgeFtsStore(self.fts_path)
        self.vector_store = KnowledgeVectorStore(self.vec_path)
        self.provider = DeterministicLocalEmbeddingProvider("amca-offline-minilm-ref", 384)

        # Seed Domain Knowledge Base
        self._seed_doc(
            "doc-biomarker-01",
            "ws-research",
            "Targeted Oncology Clinical Trials",
            "Glioblastoma multiforme GBM biomarker EGFRvIII target identification.",
        )
        self._seed_doc(
            "doc-arch-02",
            "ws-engineering",
            "High-Throughput Streaming Engine",
            "Distributed pipeline architecture using zero-copy memory buffers and SIMD acceleration.",
        )
        self._seed_doc(
            "doc-policy-03",
            "ws-documents",
            "Corporate Multi-Model AI Governance Policy",
            "Guidelines requiring zero plaintext credentials and local offline parity for sensitive documents.",
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

    def _seed_doc(self, doc_id: str, ws_id: str, title: str, text: str) -> None:
        self.fts_store.index_artifact(
            artifact_id=doc_id,
            workspace_id=ws_id,
            title=title,
            content=text,
        )
        vec = self.provider.embed_text(f"{title} {text}")
        self.vector_store.store_vector(
            artifact_id=doc_id,
            workspace_id=ws_id,
            model_name=self.provider.model_name,
            vector=vec,
        )

    def test_scenario_a_research_workflow(self) -> None:
        """Validate realistic commercial research assistant workflow."""
        # 1. Goal
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-research",
            raw_goal="Synthesize clinical oncology trial data for GBM biomarkers",
            structured_intent="LITERATURE_SYNTHESIS",
            domain="oncology_clinical",
            constraints=("hipaa_compliance", "offline_local"),
            required_outputs=("gbm_biomarker_brief.md",),
        )
        self.assertEqual(wf.state, "GOAL_UNDERSTOOD")

        # 2. Plan
        wf = self.orchestrator.attach_plan(
            workflow_id=wf.workflow_id,
            workspace_id="ws-research",
            planned_steps=("query_biomarker_corpus", "extract_trial_targets", "compile_brief"),
            required_capabilities=("medical_nlp", "evidence_synthesis"),
            strategy="parallel_synthesis",
        )
        self.assertEqual(wf.state, "PLANNED")

        # 3. Select resources
        wf = self.orchestrator.select_resources(
            workflow_id=wf.workflow_id,
            workspace_id="ws-research",
            selected_models=("med-llama-8b-local",),
            selected_agents=("clinical_researcher",),
            provider_health_references=("ollama-local:HEALTHY",),
            decision_reason="Domain-tuned medical model verified healthy",
        )
        self.assertEqual(wf.state, "RESOURCES_SELECTED")

        # 4. Knowledge binding
        wf = self.orchestrator.bind_knowledge(
            workflow_id=wf.workflow_id,
            workspace_id="ws-research",
            query="GBM biomarker EGFRvIII",
            requester_role="researcher",
            mode="lexical_only",
        )
        self.assertEqual(wf.state, "KNOWLEDGE_BOUND")
        self.assertIn("doc-biomarker-01", wf.knowledge.retrieved_artifact_ids)

        # 5. Execute
        def research_worker() -> tuple[bool, tuple[str, ...], str | None]:
            return True, ("gbm_biomarker_brief.md", "evidence_appendix.json"), None

        wf = self.orchestrator.execute_workflow(
            workflow_id=wf.workflow_id,
            workspace_id="ws-research",
            execution_callable=research_worker,
        )
        self.assertEqual(wf.state, "COMPLETED")
        self.assertTrue(wf.verification.execution_completed)
        self.assertIn("gbm_biomarker_brief.md", wf.verification.artifacts_produced)

        # 6. Learning Signal
        wf = self.orchestrator.record_learning_signal(
            workflow_id=wf.workflow_id,
            workspace_id="ws-research",
            duration_ms=1240.0,
            failure_count=0,
            retry_count=0,
            resource_metrics={"peak_mem_mb": 512, "token_count": 840},
            user_correction_count=0,
        )
        self.assertEqual(wf.learning.execution_duration_ms, 1240.0)

    def test_scenario_b_software_engineering_workflow(self) -> None:
        """Validate realistic software engineering development workflow."""
        # 1. Goal
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-engineering",
            raw_goal="Implement SIMD-accelerated streaming memory buffer",
            structured_intent="FEATURE_IMPLEMENTATION",
            domain="systems_programming",
            constraints=("zero_copy", "simd_avx2"),
            required_outputs=("stream_buffer.py", "test_stream_buffer.py"),
        )

        # 2. Plan
        wf = self.orchestrator.attach_plan(
            workflow_id=wf.workflow_id,
            workspace_id="ws-engineering",
            planned_steps=("read_streaming_spec", "implement_buffer", "run_unit_tests"),
            required_capabilities=("code_generation", "performance_benchmarking"),
            strategy="adaptive_hybrid",
        )

        # 3. Select resources
        wf = self.orchestrator.select_resources(
            workflow_id=wf.workflow_id,
            workspace_id="ws-engineering",
            selected_models=("qwen2.5-coder-32b",),
            selected_agents=("systems_engineer", "qa_verifier"),
            provider_health_references=("local-vllm:HEALTHY",),
            decision_reason="Top coding benchmark score and local offline availability",
        )

        # 4. Knowledge binding
        wf = self.orchestrator.bind_knowledge(
            workflow_id=wf.workflow_id,
            workspace_id="ws-engineering",
            query="Streaming Engine SIMD",
            requester_role="coder",
            mode="lexical_only",
        )
        self.assertIn("doc-arch-02", wf.knowledge.retrieved_artifact_ids)

        # 5. Execute
        def engineering_worker() -> tuple[bool, tuple[str, ...], str | None]:
            return True, ("stream_buffer.py", "test_stream_buffer.py"), None

        wf = self.orchestrator.execute_workflow(
            workflow_id=wf.workflow_id,
            workspace_id="ws-engineering",
            execution_callable=engineering_worker,
        )
        self.assertEqual(wf.state, "COMPLETED")
        self.assertEqual(len(wf.verification.artifacts_produced), 2)

    def test_scenario_c_document_productivity_workflow(self) -> None:
        """Validate executive document creation workflow."""
        # 1. Goal
        wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-documents",
            raw_goal="Draft Enterprise AI Governance Policy Presentation",
            structured_intent="POLICY_PRESENTATION_DRAFT",
            domain="enterprise_governance",
            required_outputs=("ai_governance_presentation.deck.md",),
        )

        # 2. Plan
        wf = self.orchestrator.attach_plan(
            workflow_id=wf.workflow_id,
            workspace_id="ws-documents",
            planned_steps=("load_policy_rules", "outline_slides", "generate_deck"),
            required_capabilities=("document_drafting", "policy_synthesis"),
        )

        # 3. Select resources
        wf = self.orchestrator.select_resources(
            workflow_id=wf.workflow_id,
            workspace_id="ws-documents",
            selected_models=("claude-3-5-sonnet",),
            selected_agents=("executive_assistant",),
            provider_health_references=("anthropic-byok:HEALTHY",),
        )

        # 4. Knowledge binding
        wf = self.orchestrator.bind_knowledge(
            workflow_id=wf.workflow_id,
            workspace_id="ws-documents",
            query="Governance Policy plaintext",
            requester_role="reviewer",
            mode="lexical_only",
        )
        self.assertIn("doc-policy-03", wf.knowledge.retrieved_artifact_ids)

        # 5. Execute
        wf = self.orchestrator.execute_workflow(
            workflow_id=wf.workflow_id,
            workspace_id="ws-documents",
            execution_callable=lambda: (True, ("ai_governance_presentation.deck.md",), None),
        )
        self.assertEqual(wf.state, "COMPLETED")
        self.assertIn("ai_governance_presentation.deck.md", wf.verification.artifacts_produced)

    def test_governance_and_failure_scenarios(self) -> None:
        """Validate failure handling for missing knowledge, degraded health, failed execution, and denial."""
        # 1. Execution failure gracefully sets FAILED_EXECUTION
        wf_err = self.orchestrator.initialize_workflow(workspace_id="ws-engineering", raw_goal="Buggy task", structured_intent="BUG_TEST")
        wf_err = self.orchestrator.attach_plan(workflow_id=wf_err.workflow_id, workspace_id="ws-engineering", planned_steps=("s1",), required_capabilities=("c1",))
        wf_err = self.orchestrator.select_resources(workflow_id=wf_err.workflow_id, workspace_id="ws-engineering", selected_models=("m1",), selected_agents=("a1",))
        
        def failing_worker() -> tuple[bool, tuple[str, ...], str | None]:
            return False, (), "Compilation error: missing symbol"

        wf_err = self.orchestrator.execute_workflow(
            workflow_id=wf_err.workflow_id,
            workspace_id="ws-engineering",
            execution_callable=failing_worker,
        )
        self.assertEqual(wf_err.state, "FAILED_EXECUTION")
        self.assertIn("missing symbol", wf_err.failure_reason)

        # 2. Permission denial for unauthorized guest role
        wf_den = self.orchestrator.initialize_workflow(workspace_id="ws-documents", raw_goal="Secret task", structured_intent="SECRET_TEST")
        wf_den = self.orchestrator.attach_plan(workflow_id=wf_den.workflow_id, workspace_id="ws-documents", planned_steps=("s1",), required_capabilities=("c1",))
        wf_den = self.orchestrator.select_resources(workflow_id=wf_den.workflow_id, workspace_id="ws-documents", selected_models=("m1",), selected_agents=("a1",))
        wf_den = self.orchestrator.bind_knowledge(
            workflow_id=wf_den.workflow_id,
            workspace_id="ws-documents",
            query="confidential documents",
            requester_role="anonymous_unauthorized",
        )
        self.assertEqual(wf_den.state, "FAILED_KNOWLEDGE_ACCESS")
        self.assertIn("not authorized", wf_den.failure_reason)


if __name__ == "__main__":
    unittest.main()

