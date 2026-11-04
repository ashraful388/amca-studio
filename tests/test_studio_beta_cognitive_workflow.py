"""
AMCA Studio Beta B5 Cognitive Workflow Orchestration Foundation Test Suite
Validates:
1. Cognitive task understanding record synthesis
2. Cross-system decision trace links (routing, collaboration, memory, agents)
3. Measurable cognitive workflow metrics computation
4. Project isolation and persistence in SQLite
"""

import tempfile
import unittest
from pathlib import Path

from amca.cognitive_orchestration import (
    CognitiveTaskUnderstandingRecord,
    CognitiveWorkflowStore,
    synthesize_cognitive_workflow,
)


class StudioBetaCognitiveWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_cog.sqlite3"
        self.store = CognitiveWorkflowStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_workflow_synthesis_and_metrics_calculation(self) -> None:
        """Verify empirical metrics derived from capability, model, and context coverage."""
        rec = synthesize_cognitive_workflow(
            "Refactor auth service with regression tests",
            project_id="proj-cognition-1",
            required_capabilities=["code_refactor", "unit_test"],
            matched_agent_ids=["specialist.coding", "core.verification-agent"],
            matched_model_ids=["ollama/qwen2.5-coder:7b"],
            relevant_memory_ids=["mem-auth-01", "mem-auth-02"],
            recommended_mode="joint",
            orchestration_decision_ids=["orch-101"],
            collaboration_session_ids=["collab-201"],
        )

        self.assertTrue(rec.understanding_id.startswith("cog-"))
        self.assertEqual(rec.project_id, "proj-cognition-1")
        self.assertEqual(rec.recommended_mode, "joint")
        self.assertEqual(len(rec.matched_agent_ids), 2)
        self.assertEqual(rec.metrics.capability_coverage_ratio, 1.0)
        self.assertEqual(rec.metrics.model_suitability_ratio, 1.0)
        self.assertTrue(rec.metrics.context_availability)
        self.assertGreater(rec.metrics.planning_completeness, 0.9)

    def test_cross_system_trace_linkage_and_persistence(self) -> None:
        """Verify cross-system references persist and maintain project isolation."""
        rec1 = synthesize_cognitive_workflow(
            "Analyze data logs",
            project_id="proj-alpha",
            required_capabilities=["data_analysis"],
            matched_agent_ids=["specialist.data-analysis"],
            matched_model_ids=["ollama/llama3.2:3b"],
            relevant_memory_ids=["mem-data-01"],
            orchestration_decision_ids=["orch-a1"],
            collaboration_session_ids=["sess-a1"],
        )
        self.store.save_understanding(rec1)

        # Retrieve in Project Alpha
        results_alpha = self.store.list_understandings("proj-alpha")
        self.assertEqual(len(results_alpha), 1)
        self.assertEqual(results_alpha[0]["orchestration_decision_ids"], ["orch-a1"])
        self.assertEqual(results_alpha[0]["collaboration_session_ids"], ["sess-a1"])

        # Project Beta must remain completely empty (strict isolation)
        results_beta = self.store.list_understandings("proj-beta")
        self.assertEqual(len(results_beta), 0)


if __name__ == "__main__":
    unittest.main()

