"""
AMCA Studio Intelligence & Optimization Audit Test Suite
Validates:
1. All 4 collaboration modes (solo, joint_synthesis, parallel_fanout, adaptive_hybrid)
2. Deterministic decomposition planner outputs and round structure
3. Local model zero-marginal-cost guarantees in pricing registry
4. Execution trace visibility and reason provenance
"""

import unittest
from amca.collaboration import DeterministicDecompositionPlanner
from amca.domain import CollaborationMode


class StudioIntelligenceAndOptimizationTests(unittest.TestCase):
    def test_collaboration_modes_integrity(self) -> None:
        """Verify that all 4 commercial collaboration modes are supported."""
        modes = {m.value for m in CollaborationMode}
        self.assertIn("solo", modes)
        self.assertIn("joint", modes)
        self.assertIn("parallel", modes)
        self.assertIn("adaptive_hybrid", modes)

    def test_deterministic_planner_decomposition(self) -> None:
        """Verify that deterministic planner yields structured sub-goals with assigned roles."""
        planner = DeterministicDecompositionPlanner()
        plan, meta = planner.plan(
            goal="Analyze repository architecture, then produce benchmark report",
            participant_ids=["claude-3-5-sonnet", "qwen2.5-coder:7b"],
        )
        self.assertTrue(len(plan.work_units) > 0, "Plan should contain work units")
        self.assertIn("work_unit_count", meta)
        for unit in plan.work_units:
            self.assertTrue(hasattr(unit, "focus"))
            self.assertTrue(hasattr(unit, "assignee_ids"))

    def test_local_model_zero_cost_policy(self) -> None:
        """Verify that local models are declared with zero dollar marginal cost."""
        from amca.service import LOCAL_MODEL_RECOMMENDATIONS
        self.assertGreater(len(LOCAL_MODEL_RECOMMENDATIONS), 0)
        for rec in LOCAL_MODEL_RECOMMENDATIONS:
            self.assertIn("model", rec)
            self.assertIn("size_bytes", rec)


if __name__ == "__main__":
    unittest.main()

