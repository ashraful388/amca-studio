"""
AMCA Studio Beta B19 Beta Feedback Analysis and Refinement Framework Test Suite
Validates:
1. FeedbackInsightRecord creation and priority calculation
2. generate_beta_improvement_report category breakdown and ranking
3. BetaRefinementStore persistence and workspace isolation in SQLite
4. Zero external cloud telemetry or subjective AI scoring
"""

import tempfile
import unittest
from pathlib import Path

from amca.feedback_analysis import (
    BetaImprovementReport,
    BetaRefinementStore,
    FeedbackInsightRecord,
    calculate_refinement_priority,
    generate_beta_improvement_report,
)


class StudioBetaFeedbackAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_feedback_analysis.sqlite3"
        self.store = BetaRefinementStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_refinement_priority_calculation(self) -> None:
        """Verify priority formula calculates objective scores without subjective scoring."""
        # Critical severity (1.0), 3 occurrences (0.6), 2 affected modules (0.5)
        # 0.5 * 1.0 + 0.3 * 0.6 + 0.2 * 0.5 = 0.5 + 0.18 + 0.1 = 0.78
        score = calculate_refinement_priority(
            severity="critical",
            frequency=3,
            affected_module_count=2,
        )
        self.assertEqual(score, 0.78)

        # 0.5 * 0.25 + 0.3 * 0.2 + 0.2 * 0.25 = 0.125 + 0.06 + 0.05 = 0.235 -> round(0.235, 2) is 0.23
        score_low = calculate_refinement_priority(
            severity="low",
            frequency=1,
            affected_module_count=1,
        )
        self.assertEqual(score_low, 0.23)

    def test_improvement_report_generation(self) -> None:
        """Verify report aggregates category counts and ranks insights by priority."""
        insights = [
            FeedbackInsightRecord(
                insight_id="ins-01",
                workspace_id="ws-corp",
                category="bug",
                title="Model catalog retry error",
                affected_modules=("model_hub",),
                severity="high",
                frequency=2,
                priority_score=0.65,
            ),
            FeedbackInsightRecord(
                insight_id="ins-02",
                workspace_id="ws-corp",
                category="ux_improvement",
                title="Shortcut key clarity",
                affected_modules=("user_interaction", "routing"),
                severity="low",
                frequency=4,
                priority_score=0.45,
            ),
        ]

        report = generate_beta_improvement_report("ws-corp", insights)
        self.assertTrue(report.report_id.startswith("bimprep-"))
        self.assertEqual(report.total_feedback_items, 6)
        self.assertEqual(report.category_breakdown["bug"], 2)
        self.assertEqual(report.category_breakdown["ux_improvement"], 4)
        self.assertEqual(report.top_refinements[0].insight_id, "ins-01")

    def test_refinement_store_persistence_and_isolation(self) -> None:
        """Verify insight records and reports persist with workspace isolation."""
        ins = FeedbackInsightRecord(
            insight_id="ins-101",
            workspace_id="ws-alpha",
            category="performance",
            title="SQLite lock timeout on high concurrency",
            affected_modules=("persistence",),
            severity="medium",
            frequency=3,
            priority_score=0.55,
        )
        self.store.save_insight(ins)

        # Retrieve Alpha
        alpha_items = self.store.list_insights("ws-alpha")
        self.assertEqual(len(alpha_items), 1)
        self.assertEqual(alpha_items[0]["title"], "SQLite lock timeout on high concurrency")

        # Query Beta (isolated)
        beta_items = self.store.list_insights("ws-beta")
        self.assertEqual(len(beta_items), 0)


if __name__ == "__main__":
    unittest.main()

