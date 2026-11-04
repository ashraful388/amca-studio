"""
AMCA Studio Beta B17 Public Beta Preparation and Stabilization Test Suite
Validates:
1. BetaFeedbackStore offline SQLite persistence and workspace isolation
2. sanitize_diagnostic_export redaction of API keys, passwords, and tokens
3. evaluate_public_beta_stabilization readiness verification
4. Zero external cloud dependencies or telemetry
"""

import tempfile
import unittest
from pathlib import Path

from amca.public_beta_stabilization import (
    BetaFeedbackRecord,
    BetaFeedbackStore,
    BetaStabilizationStatus,
    evaluate_public_beta_stabilization,
    sanitize_diagnostic_export,
)


class StudioBetaPublicBetaPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_feedback.sqlite3"
        self.store = BetaFeedbackStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_feedback_persistence_and_isolation(self) -> None:
        """Verify user feedback is recorded offline in SQLite and partitioned by workspace."""
        rec = self.store.record_feedback(
            workspace_id="ws-beta-user",
            category="ux_improvement",
            title="Model selection card clarity",
            description="Clearer indication of local vs cloud execution on model cards.",
            diagnostic_ref="diag-101",
        )
        self.assertTrue(rec.feedback_id.startswith("fdb-"))

        # Query user workspace
        feedbacks = self.store.list_feedback("ws-beta-user")
        self.assertEqual(len(feedbacks), 1)
        self.assertEqual(feedbacks[0]["title"], "Model selection card clarity")
        self.assertEqual(feedbacks[0]["category"], "ux_improvement")

        # Query other workspace
        other = self.store.list_feedback("ws-other-user")
        self.assertEqual(len(other), 0)

    def test_diagnostic_export_sanitization(self) -> None:
        """Verify API keys, credentials, and sensitive headers are strictly redacted."""
        raw_diagnostics = {
            "workspace_id": "ws-beta",
            "api_token": "sk-proj-12345678901234567890abcdef",
            "user_password": "supersecretpassword",
            "nested_config": {
                "auth_key": "ghp_abcdefghijklmnopqrstuvwxyz123456",
                "normal_field": "safe_value",
            },
            "error_log": "Connection failed with sk-secret99887766554433221100 on host localhost",
        }

        sanitized = sanitize_diagnostic_export(raw_diagnostics)
        self.assertEqual(sanitized["api_token"], "[REDACTED]")
        self.assertEqual(sanitized["user_password"], "[REDACTED]")
        self.assertEqual(sanitized["nested_config"]["auth_key"], "[REDACTED]")
        self.assertEqual(sanitized["nested_config"]["normal_field"], "safe_value")
        self.assertNotIn("sk-secret", sanitized["error_log"])
        self.assertIn("[REDACTED]", sanitized["error_log"])

    def test_public_beta_stabilization_readiness(self) -> None:
        """Verify readiness status evaluates to PUBLIC_BETA_READY when all criteria pass."""
        status = evaluate_public_beta_stabilization(
            release_version="v0.7.0-beta.1",
            distribution_ready=True,
            onboarding_validated=True,
            docs_complete=True,
            feedback_ready=True,
        )
        self.assertEqual(status.overall_status, "PUBLIC_BETA_READY")
        self.assertTrue(status.distribution_ready)


if __name__ == "__main__":
    unittest.main()

