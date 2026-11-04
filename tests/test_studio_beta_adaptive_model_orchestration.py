"""
AMCA Studio Beta B4 Adaptive Model Orchestration Foundation Test Suite
Validates:
1. ModelCapabilityProfile schema and properties
2. OrchestrationDecisionStore persistence and strict project isolation
3. Transparent model suitability evaluation and $0.00 local preference
4. Full backward compatibility with existing ComplexityRouter and routing pipeline
"""

import tempfile
import unittest
from pathlib import Path

from amca.routing import (
    ModelCapabilityProfile,
    OrchestrationDecisionRecord,
    OrchestrationDecisionStore,
    evaluate_model_suitability,
    ComplexityRouter,
)
from amca.domain import ComplexityLevel


class StudioBetaAdaptiveModelOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_orch.sqlite3"
        self.store = OrchestrationDecisionStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_model_capability_profile_creation(self) -> None:
        """Verify model profile fields and dictionary conversion."""
        profile = ModelCapabilityProfile(
            model_id="ollama/qwen2.5-coder:7b",
            display_name="Qwen 2.5 Coder 7B",
            context_window=32768,
            supports_tools=True,
            supports_vision=False,
            is_local=True,
            cost_per_1k_input_usd=0.0,
            cost_per_1k_output_usd=0.0,
            speed_tier="fast",
        )
        d = profile.to_dict()
        self.assertEqual(d["model_id"], "ollama/qwen2.5-coder:7b")
        self.assertTrue(d["is_local"])
        self.assertEqual(d["cost_per_1k_input_usd"], 0.0)

    def test_suitability_evaluation_local_preference(self) -> None:
        """Verify local $0 inference receives suitability boost over cloud models."""
        local_p = ModelCapabilityProfile(
            model_id="ollama/qwen2.5-coder:7b",
            display_name="Qwen 2.5 Coder 7B",
            context_window=32768,
            supports_tools=True,
            supports_vision=False,
            is_local=True,
            speed_tier="fast",
        )
        cloud_p = ModelCapabilityProfile(
            model_id="anthropic/claude-3-5-sonnet",
            display_name="Claude 3.5 Sonnet",
            context_window=200000,
            supports_tools=True,
            supports_vision=True,
            is_local=False,
            cost_per_1k_input_usd=0.003,
            cost_per_1k_output_usd=0.015,
            speed_tier="standard",
        )

        score_local, reasons_local = evaluate_model_suitability(
            local_p,
            "Write python script to validate database migrations",
        )
        score_cloud, reasons_cloud = evaluate_model_suitability(
            cloud_p,
            "Write python script to validate database migrations",
        )
        self.assertGreater(score_local, score_cloud)
        self.assertTrue(any("Local zero-marginal-cost" in r for r in reasons_local))

        # Vision requirement filter
        score_no_vision, reasons_no_vision = evaluate_model_suitability(
            local_p,
            "Analyze screenshot diagram",
            requires_vision=True,
        )
        self.assertEqual(score_no_vision, 0.0)
        self.assertIn("does not support required vision", reasons_no_vision[0])

    def test_orchestration_decision_persistence_and_isolation(self) -> None:
        """Verify decisions saved in project 1 do not leak to project 2."""
        dec = OrchestrationDecisionRecord(
            decision_id="orch-001",
            project_id="proj-1",
            task_summary="Generate REST endpoints",
            selected_model_id="ollama/qwen2.5-coder:7b",
            rationale="Local code specialist with 0 cost",
            estimated_cost_usd=0.0,
            actual_cost_usd=0.0,
            is_local_zero_cost=True,
        )
        self.store.record_decision(dec)

        p1_records = self.store.list_decisions("proj-1")
        self.assertEqual(len(p1_records), 1)
        self.assertEqual(p1_records[0]["selected_model_id"], "ollama/qwen2.5-coder:7b")
        self.assertTrue(p1_records[0]["is_local_zero_cost"])

        p2_records = self.store.list_decisions("proj-2")
        self.assertEqual(len(p2_records), 0)

    def test_backward_compatibility_with_complexity_router(self) -> None:
        """Verify ComplexityRouter continues to operate unmodified."""
        router = ComplexityRouter()
        complexity = router.assess("Prove the mathematical theorem holds universally")
        self.assertIn(complexity, (ComplexityLevel.CRITICAL, ComplexityLevel.HIGH))


if __name__ == "__main__":
    unittest.main()

