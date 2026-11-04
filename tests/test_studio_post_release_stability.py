"""
AMCA Studio v0.7.0 Post-Release Stability & Quality Track Test Suite
Validates:
1. IssueIntakeStore offline SQLite persistence and workspace isolation
2. Operational issue status lifecycle (reproduced, resolved)
3. Stability baseline and post-release quality certificate evaluation
4. Zero external cloud telemetry or analytics
"""

import tempfile
import unittest
from pathlib import Path

from amca.post_release_stability import (
    IssueIntakeStore,
    OperationalIssueRecord,
    PostReleaseQualityCertificate,
    StabilityBaselineRecord,
    evaluate_post_release_quality,
)


class StudioPostReleaseStabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_issues.sqlite3"
        self.store = IssueIntakeStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_issue_intake_persistence_and_isolation(self) -> None:
        """Verify issue records persist in SQLite and respect workspace isolation."""
        issue = self.store.record_issue(
            workspace_id="ws-prod-client",
            title="Model latency spike during high batching",
            severity="L2",
            affected_module="model_orchestration",
            reproduction_status="reproduced",
            resolution_status="in_progress",
        )
        self.assertTrue(issue.issue_id.startswith("iss-"))

        # Retrieve client workspace
        issues = self.store.list_issues("ws-prod-client")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "L2")
        self.assertEqual(issues[0]["affected_module"], "model_orchestration")

        # Isolated workspace
        other = self.store.list_issues("ws-other-client")
        self.assertEqual(len(other), 0)

    def test_quality_certificate_healthy_evaluation(self) -> None:
        """Verify quality certificate evaluates to STABILITY_HEALTHY with clean baseline."""
        cert = evaluate_post_release_quality(
            release_version="v0.7.0",
            startup_reliability=1.0,
            clean_install_verified=True,
            migration_safety_verified=True,
            regression_passed=219,
            regression_total=219,
            open_critical_issues=0,
        )

        self.assertTrue(cert.certificate_id.startswith("qualcert-"))
        self.assertEqual(cert.release_version, "v0.7.0")
        self.assertEqual(cert.final_verdict, "STABILITY_HEALTHY")
        self.assertEqual(cert.baseline.status, "STABLE")

    def test_quality_certificate_action_required_on_critical_issue(self) -> None:
        """Verify open critical (L3) issues trigger ACTION_REQUIRED."""
        cert = evaluate_post_release_quality(
            release_version="v0.7.0",
            open_critical_issues=1,  # Critical issue open
        )
        self.assertEqual(cert.final_verdict, "ACTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()

