"""
AMCA Studio v0.8.0 Runtime Intelligence Integration Test Suite (AG-AMCA-STUDIO-V0.8.0).
Validates:
1. Workflow binds to runtime execution session
2. Multi-model collaboration session participants & execution modes recorded
3. Governance gates verified before execution dispatch
4. Runtime evidence captured cleanly without chain-of-thought or hidden reasoning
5. Failures and cancellations captured truthfully
6. Architectural boundaries preserved (Model Router & Agent Fabric untouched)
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
from amca.cognitive_runtime_bridge import (
    CognitiveRuntimeBridge,
    CognitiveRuntimeBridgeStore,
    RuntimeEvidenceRecord,
    RuntimeSessionRecord,
    sanitize_evidence_facts,
)


class StudioV080RuntimeIntelligenceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

        self.wf_db_path = self.tmp_path / "cognitive_workflows.sqlite3"
        self.bridge_db_path = self.tmp_path / "runtime_bridge.sqlite3"

        self.wf_store = CognitiveWorkflowStore(self.wf_db_path)
        self.bridge_store = CognitiveRuntimeBridgeStore(self.bridge_db_path)

        self.orchestrator = CognitiveWorkflowOrchestrator(store=self.wf_store)
        self.bridge = CognitiveRuntimeBridge(
            store=self.bridge_store,
            orchestrator=self.orchestrator,
        )

        # Seed a planned workflow with selected resources
        self.wf = self.orchestrator.initialize_workflow(
            workspace_id="ws-runtime",
            raw_goal="Deploy high performance analytics service",
            structured_intent="DEPLOY_ANALYTICS",
        )
        self.wf = self.orchestrator.attach_plan(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
            planned_steps=("provision", "benchmark", "verify"),
            required_capabilities=("compute", "analytics"),
        )
        self.wf = self.orchestrator.select_resources(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
            selected_models=("llama3-70b-local", "qwen2.5-coder-32b"),
            selected_agents=("lead_architect", "perf_tester"),
            provider_health_references=("ollama-local:HEALTHY",),
            decision_reason="Offline local model performance match",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_workflow_binds_to_runtime_session(self) -> None:
        """Verify workflow creates traceable runtime session with recorded participants."""
        session = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
            execution_mode="adaptive_hybrid",
        )
        self.assertIsNotNone(session.session_id)
        self.assertEqual(session.status, "INITIALIZED")
        self.assertEqual(session.workflow_id, self.wf.workflow_id)
        self.assertEqual(session.execution_mode, "adaptive_hybrid")
        self.assertEqual(len(session.selected_models), 2)
        self.assertEqual(len(session.participating_agents), 2)

        evidence = self.bridge_store.list_evidence(
            session_id=session.session_id,
            workspace_id="ws-runtime",
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].event_type, "SESSION_START")

    def test_governance_gates_verification_passed(self) -> None:
        """Verify successful governance gates transition session to GOVERNANCE_VERIFIED."""
        session = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
        )
        session = self.bridge.verify_governance_gates(
            session_id=session.session_id,
            workspace_id="ws-runtime",
            approvals_verified=True,
            provider_health_status="HEALTHY",
            knowledge_permission_outcome="AUTHORIZED",
        )
        self.assertTrue(session.governance_check_passed)
        self.assertEqual(session.status, "GOVERNANCE_VERIFIED")

        evidence = self.bridge_store.list_evidence(
            session_id=session.session_id,
            workspace_id="ws-runtime",
        )
        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[1].event_type, "GOVERNANCE_GATE_VERIFIED")
        self.assertTrue(evidence[1].recorded_facts["gate_passed"])

    def test_governance_gates_rejection_blocks_dispatch(self) -> None:
        """Verify unapproved governance gates transition session to FAILED and block execution."""
        session = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
        )
        session = self.bridge.verify_governance_gates(
            session_id=session.session_id,
            workspace_id="ws-runtime",
            approvals_verified=False,  # Unapproved!
            provider_health_status="HEALTHY",
        )
        self.assertFalse(session.governance_check_passed)
        self.assertEqual(session.status, "FAILED")

        # Attempting dispatch raises RuntimeError
        with self.assertRaises(RuntimeError):
            self.bridge.dispatch_execution(
                session_id=session.session_id,
                workspace_id="ws-runtime",
                worker_callable=lambda: (True, (), None),
            )

    def test_successful_dispatch_and_evidence_sequencing(self) -> None:
        """Verify execution dispatch records complete evidence trail and produces artifacts."""
        session = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
        )
        self.bridge.verify_governance_gates(
            session_id=session.session_id,
            workspace_id="ws-runtime",
            approvals_verified=True,
            provider_health_status="HEALTHY",
        )

        def mock_worker() -> tuple[bool, tuple[str, ...], str | None]:
            return True, ("analytics_manifest.json", "benchmark_report.md"), None

        session = self.bridge.dispatch_execution(
            session_id=session.session_id,
            workspace_id="ws-runtime",
            worker_callable=mock_worker,
        )
        self.assertEqual(session.status, "COMPLETED")
        self.assertEqual(len(session.artifacts_produced), 2)

        evidence = self.bridge_store.list_evidence(
            session_id=session.session_id,
            workspace_id="ws-runtime",
        )
        # Sequence: 0: SESSION_START, 1: GOVERNANCE_GATE_VERIFIED, 2: DISPATCH, 3: SESSION_COMPLETE
        self.assertEqual(len(evidence), 4)
        self.assertEqual(evidence[0].event_type, "SESSION_START")
        self.assertEqual(evidence[1].event_type, "GOVERNANCE_GATE_VERIFIED")
        self.assertEqual(evidence[2].event_type, "DISPATCH")
        self.assertEqual(evidence[3].event_type, "SESSION_COMPLETE")

    def test_failure_and_cancellation_paths(self) -> None:
        """Verify worker failures and cancellations are captured accurately."""
        # 1. Execution failure
        session1 = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
        )
        self.bridge.verify_governance_gates(
            session_id=session1.session_id,
            workspace_id="ws-runtime",
            approvals_verified=True,
        )
        session1 = self.bridge.dispatch_execution(
            session_id=session1.session_id,
            workspace_id="ws-runtime",
            worker_callable=lambda: (False, (), "GPU Out of Memory error"),
        )
        self.assertEqual(session1.status, "FAILED")
        self.assertIn("GPU Out of Memory", session1.error_message)

        # 2. Session cancellation
        session2 = self.bridge.create_runtime_session(
            workflow_id=self.wf.workflow_id,
            workspace_id="ws-runtime",
        )
        session2 = self.bridge.cancel_session(
            session_id=session2.session_id,
            workspace_id="ws-runtime",
            reason="User aborted run",
        )
        self.assertEqual(session2.status, "CANCELLED")
        self.assertEqual(session2.error_message, "User aborted run")

    def test_safety_invariants_and_router_decoupling(self) -> None:
        """Verify chain-of-thought is sanitized away and Model Router is untouched."""
        # 1. Chain-of-thought sanitization
        dirty_facts = {
            "execution_mode": "solo",
            "chain_of_thought": "Private hidden steps",
            "hidden_reasoning": "Do not expose",
            "raw_thoughts": "Internal deliberation",
            "token_count": 450,
        }
        clean_facts = sanitize_evidence_facts(dirty_facts)
        self.assertNotIn("chain_of_thought", clean_facts)
        self.assertNotIn("hidden_reasoning", clean_facts)
        self.assertNotIn("raw_thoughts", clean_facts)
        self.assertEqual(clean_facts["token_count"], 450)

        # 2. Model Router decoupling
        import amca.intelligence.cross_provider_router as cpr
        source_cpr = inspect.getsource(cpr)
        self.assertNotIn("CognitiveRuntimeBridge", source_cpr)


if __name__ == "__main__":
    unittest.main()

