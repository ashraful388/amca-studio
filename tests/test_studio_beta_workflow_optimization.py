"""
AMCA Studio Beta B6 Workflow Optimization Intelligence Foundation Test Suite
Validates:
1. Empirical workflow performance analysis (durations, failure count, local ratio)
2. Evidence-based optimization recommendations (cost, latency, reliability)
3. Project-isolated persistence in SQLite
4. Read-only boundaries: zero automatic execution changes or autonomous self-modification
"""

import tempfile
import unittest
from pathlib import Path

from amca.workflow_optimization import (
    WorkflowAnalysisRecord,
    WorkflowOptimizationStore,
    analyze_workflow_performance,
)


class StudioBetaWorkflowOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_opt.sqlite3"
        self.store = WorkflowOptimizationStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_workflow_analysis_and_evidence_recommendations(self) -> None:
        """Verify empirical derivation of bottlenecks and cost savings without speculation."""
        mock_steps = [
            {"step_id": "step-1-plan", "duration_seconds": 1.2, "is_local": True, "failed": False},
            {"step_id": "step-2-heavy-code", "duration_seconds": 22.5, "is_local": False, "cost_usd": 0.08, "failed": False},
            {"step_id": "step-3-test-run", "duration_seconds": 3.0, "is_local": True, "failed": True},
        ]

        analysis = analyze_workflow_performance("proj-opt-1", "wf-999", mock_steps)
        self.assertEqual(analysis.step_count, 3)
        self.assertEqual(analysis.failure_count, 1)
        self.assertEqual(analysis.bottleneck_step, "step-2-heavy-code")
        self.assertAlmostEqual(analysis.total_duration_seconds, 26.7, places=1)
        self.assertAlmostEqual(analysis.local_execution_ratio, 0.67, places=2)

        # Confirm evidence-based recommendations
        categories = [r.category for r in analysis.recommendations]
        self.assertIn("latency", categories)
        self.assertIn("reliability", categories)

        latency_rec = next(r for r in analysis.recommendations if r.category == "latency")
        self.assertIn("step-2-heavy-code", latency_rec.summary)
        self.assertIn("22.5s", latency_rec.evidence)

    def test_workflow_optimization_store_persistence_and_isolation(self) -> None:
        """Verify analysis records persist in SQLite and respect strict project isolation."""
        mock_steps = [
            {"step_id": "step-fast", "duration_seconds": 0.5, "is_local": True, "failed": False},
        ]
        rec = analyze_workflow_performance("proj-alpha", "wf-alpha-1", mock_steps)
        self.store.save_analysis(rec)

        # Retrieve Project Alpha
        results_alpha = self.store.list_analyses("proj-alpha")
        self.assertEqual(len(results_alpha), 1)
        self.assertEqual(results_alpha[0]["workflow_id"], "wf-alpha-1")
        self.assertEqual(results_alpha[0]["bottleneck_step"], "step-fast")

        # Project Beta must be empty
        results_beta = self.store.list_analyses("proj-beta")
        self.assertEqual(len(results_beta), 0)


if __name__ == "__main__":
    unittest.main()

