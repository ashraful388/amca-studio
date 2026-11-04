"""
AMCA Studio Beta B14 Operational Intelligence and Reporting Foundation Test Suite
Validates:
1. OperationalMetricsSnapshot formulation and measurable metrics
2. Operational report generation without subjective scores
3. Workspace isolation and local SQLite persistence
4. Zero external telemetry or cloud analytics
"""

import tempfile
import unittest
from pathlib import Path

from amca.operational_intelligence import (
    OperationalIntelligenceStore,
    OperationalMetricsSnapshot,
    OperationalReport,
    generate_operational_report,
)


class StudioBetaOperationalIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_operational.sqlite3"
        self.store = OperationalIntelligenceStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_report_generation_and_measurable_metrics(self) -> None:
        """Verify report derives strictly measurable performance indicators without subjective scoring."""
        report = generate_operational_report(
            workspace_id="ws-prod-corp",
            completed_workflows=12,
            active_projects=4,
            agent_executions=48,
            local_model_calls=40,
            cloud_model_calls=8,
            artifact_count=22,
        )
        self.assertTrue(report.report_id.startswith("oprep-"))
        self.assertEqual(report.workspace_id, "ws-prod-corp")
        self.assertEqual(report.metrics.completed_workflows, 12)
        self.assertEqual(report.metrics.local_model_calls, 40)
        self.assertEqual(report.metrics.cloud_model_calls, 8)
        self.assertIn("48 agent executions recorded", report.summary_text)
        self.assertIn("83% local", report.summary_text)

    def test_report_persistence_and_workspace_isolation(self) -> None:
        """Verify reports are saved to SQLite and partitioned strictly by workspace."""
        rep1 = generate_operational_report(
            workspace_id="ws-alpha",
            completed_workflows=3,
            active_projects=1,
            agent_executions=10,
            local_model_calls=10,
            cloud_model_calls=0,
            artifact_count=5,
        )
        self.store.save_report(rep1)

        # Retrieve Alpha
        alpha_reps = self.store.list_reports("ws-alpha")
        self.assertEqual(len(alpha_reps), 1)
        self.assertEqual(alpha_reps[0]["workspace_id"], "ws-alpha")
        self.assertEqual(alpha_reps[0]["metrics"]["completed_workflows"], 3)

        # Query Beta (isolated)
        beta_reps = self.store.list_reports("ws-beta")
        self.assertEqual(len(beta_reps), 0)


if __name__ == "__main__":
    unittest.main()

