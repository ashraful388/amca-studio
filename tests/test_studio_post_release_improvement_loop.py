"""
AMCA Studio v0.7.0 Post-Release Observability & Improvement Loop Test Suite
Validates:
1. Local persistence and workspace isolation in QualityImprovementStore
2. Module-level issue aggregation and severity tracking
3. Evidence-based report generation honesty (zero subjective AI scoring, deterministic patch justification)
"""

import tempfile
import unittest
from pathlib import Path

from amca.post_release_improvement import (
    EvidenceBasedImprovementReport,
    QualityImprovementStore,
    QualityIssueSummary,
    ReleaseHealthSnapshot,
    generate_improvement_report,
)


class StudioPostReleaseImprovementLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_improvement.sqlite3"
        self.store = QualityImprovementStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_local_persistence_and_isolation(self) -> None:
        """Verify quality reports persist in SQLite and respect workspace isolation."""
        rep = generate_improvement_report(
            workspace_id="ws-client-a",
            issues=[
                {
                    "affected_module": "model_orchestration",
                    "severity": "L1",
                    "resolution_status": "open",
                }
            ],
        )
        self.store.save_report(rep)

        # Retrieve Client A
        a_reports = self.store.list_reports("ws-client-a")
        self.assertEqual(len(a_reports), 1)
        self.assertEqual(a_reports[0]["workspace_id"], "ws-client-a")

        # Client B isolated
        b_reports = self.store.list_reports("ws-client-b")
        self.assertEqual(len(b_reports), 0)

    def test_issue_aggregation_by_module(self) -> None:
        """Verify issues are aggregated accurately per affected module."""
        issues = [
            {"affected_module": "agents", "severity": "L0", "resolution_status": "resolved"},
            {"affected_module": "agents", "severity": "L1", "resolution_status": "open"},
            {"affected_module": "memory", "severity": "L2", "resolution_status": "open"},
        ]

        report = generate_improvement_report("ws-test", issues)
        self.assertEqual(len(report.summaries), 2)

        agents_summary = [s for s in report.summaries if s.module_name == "agents"][0]
        self.assertEqual(agents_summary.total_issues, 2)
        self.assertEqual(agents_summary.resolved_issues, 1)
        self.assertEqual(agents_summary.open_issues, 1)
        self.assertEqual(agents_summary.max_severity, "L1")

    def test_report_generation_honesty_and_patch_justification(self) -> None:
        """Verify patch justification is strictly derived from recorded severities and regression status."""
        # Non-critical issues -> patch not justified
        low_issues = [
            {"affected_module": "ui", "severity": "L0", "resolution_status": "open"},
            {"affected_module": "ui", "severity": "L1", "resolution_status": "open"},
        ]
        rep_low = generate_improvement_report("ws-test", low_issues, regression_status="ALL_PASS")
        self.assertFalse(rep_low.patch_release_justified)

        # Critical (L3) issue open -> patch justified
        crit_issues = [
            {"affected_module": "security", "severity": "L3", "resolution_status": "open"}
        ]
        rep_crit = generate_improvement_report("ws-test", crit_issues, regression_status="ALL_PASS")
        self.assertTrue(rep_crit.patch_release_justified)


if __name__ == "__main__":
    unittest.main()

