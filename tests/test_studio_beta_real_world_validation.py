"""
AMCA Studio Beta B18 Real-World Usage Validation Framework Test Suite
Validates:
1. BetaUsageScenario registry and workspace isolation
2. KnownLimitationRecord persistence and workaround tracking
3. BetaHealthSummary derivation based on empirical pass ratio and documentation coverage
4. Zero external telemetry or user scoring
"""

import tempfile
import unittest
from pathlib import Path

from amca.real_world_validation import (
    BetaHealthSummary,
    BetaUsageScenario,
    KnownLimitationRecord,
    RealWorldValidationStore,
    calculate_beta_health_summary,
)


class StudioBetaRealWorldValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_rw_val.sqlite3"
        self.store = RealWorldValidationStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_scenario_persistence_and_isolation(self) -> None:
        """Verify scenario records persist in SQLite and maintain workspace isolation."""
        scen = BetaUsageScenario(
            scenario_id="scen-rw-01",
            workspace_id="ws-real-pilot",
            name="Research Survey Pilot",
            workflow_type="research",
            required_modules=("cognitive_workflow", "agents", "models", "knowledge"),
            status="passed",
            evidence_ref="/artifacts/art-pilot-survey.md",
            updated_at="2026-09-02T08:00:00Z",
        )
        self.store.save_scenario(scen)

        # Retrieve
        scens = self.store.list_scenarios("ws-real-pilot")
        self.assertEqual(len(scens), 1)
        self.assertEqual(scens[0]["scenario_id"], "scen-rw-01")
        self.assertEqual(scens[0]["status"], "passed")

        # Isolated workspace
        other = self.store.list_scenarios("ws-other-client")
        self.assertEqual(len(other), 0)

    def test_known_limitation_tracking(self) -> None:
        """Verify known limitations and workarounds persist correctly."""
        lim = KnownLimitationRecord(
            limitation_id="lim-model-catalog-01",
            module_name="model_hub",
            description="Catalog does not yet detect tool-use capabilities for uncalibrated models.",
            workaround="Specify tool suitability manually in routing preference overrides.",
            planned_improvement="Automatic tool capability detection in v0.8.",
            is_resolved=False,
        )
        self.store.save_limitation(lim)

        all_lims = self.store.list_limitations(unresolved_only=True)
        self.assertEqual(len(all_lims), 1)
        self.assertEqual(all_lims[0]["limitation_id"], "lim-model-catalog-01")
        self.assertIn("v0.8", all_lims[0]["planned_improvement"])

    def test_beta_health_summary_calculation(self) -> None:
        """Verify empirical health summary is derived with measurable metrics and zero user scoring."""
        scenarios = [
            BetaUsageScenario("s1", "ws", "S1", "research", (), "passed", "ev1", ""),
            BetaUsageScenario("s2", "ws", "S2", "swe", (), "passed", "ev2", ""),
        ]
        limitations = [
            KnownLimitationRecord("l1", "mod", "desc", "workaround", "plan", is_resolved=False)
        ]

        summary = calculate_beta_health_summary(
            "ws",
            scenarios,
            limitations,
            feedback_count=4,
            documentation_coverage=1.0,
        )

        self.assertEqual(summary.total_scenarios, 2)
        self.assertEqual(summary.passed_scenarios, 2)
        self.assertEqual(summary.unresolved_limitations, 1)
        self.assertEqual(summary.feedback_count, 4)
        # pass_ratio (1.0)*0.8 + doc (1.0)*0.2 - 0.05 = 0.95
        self.assertEqual(summary.health_ratio, 0.95)


if __name__ == "__main__":
    unittest.main()

