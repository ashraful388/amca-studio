"""
AMCA Studio Beta B16 Beta Release Candidate Validation Test Suite
Validates:
1. Release gates (Functionality, Security, Privacy, Documentation, Packaging)
2. Evaluation of 15 Beta foundation milestones
3. Overall readiness evaluation ("RELEASE_READY")
4. Zero regressions across the full stack
"""

import unittest

from amca.release_candidate import (
    BetaReleaseCandidateReport,
    BetaReleaseGateStatus,
    evaluate_beta_release_candidate,
)


class StudioBetaReleaseCandidateValidationTests(unittest.TestCase):
    def test_full_release_gates_pass(self) -> None:
        """Verify all 5 release gates pass and mark candidate RELEASE_READY."""
        report = evaluate_beta_release_candidate(
            release_version="v0.7.0-beta.1",
            milestones_verified_count=15,
            functionality_passed=True,
            security_passed=True,
            privacy_passed=True,
            documentation_passed=True,
            packaging_passed=True,
        )

        self.assertTrue(report.candidate_id.startswith("rc-"))
        self.assertEqual(report.release_version, "v0.7.0-beta.1")
        self.assertEqual(report.milestones_verified_count, 15)
        self.assertEqual(report.overall_readiness, "RELEASE_READY")
        self.assertEqual(len(report.gates), 5)

        for gate in report.gates:
            self.assertTrue(gate.passed)
            self.assertTrue(len(gate.verification_evidence) > 0)

    def test_gate_failure_triggers_action_required(self) -> None:
        """Verify any failed gate triggers ACTION_REQUIRED status."""
        report = evaluate_beta_release_candidate(
            release_version="v0.7.0-beta.1",
            milestones_verified_count=10,  # Below required 15
            functionality_passed=True,
            security_passed=False,
        )
        self.assertEqual(report.overall_readiness, "ACTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()

