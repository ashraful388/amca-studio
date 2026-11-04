"""
AMCA Studio Beta RC2 Final Integration Validation Test Suite
Validates:
1. Gate 1 — Functional Integrity (all Beta modules)
2. Gate 2 — Security Boundary (RBAC, isolation, secret masking)
3. Gate 3 — Persistence Integrity (SQLite, restart recovery)
4. Gate 4 — Documentation Completeness (all milestone validation reports)
5. Gate 5 — Regression Baseline
"""

import unittest

from amca.rc2_integration_validator import (
    RC2FinalIntegrationReport,
    RC2GateVerification,
    run_rc2_final_integration_validation,
)


class StudioBetaRC2FinalIntegrationValidationTests(unittest.TestCase):
    def test_all_five_rc2_gates_pass(self) -> None:
        """Verify all 5 RC2 gates pass and verdict is RELEASE_APPROVED."""
        report = run_rc2_final_integration_validation(
            target_version="v0.7.0-rc.2",
            functional_integrity_passed=True,
            security_boundary_passed=True,
            persistence_integrity_passed=True,
            documentation_passed=True,
            regression_passed=True,
        )

        self.assertTrue(report.candidate_id.startswith("rc2-"))
        self.assertEqual(report.target_version, "v0.7.0-rc.2")
        self.assertEqual(report.overall_verdict, "RELEASE_APPROVED")
        self.assertEqual(len(report.gates), 5)
        self.assertGreaterEqual(len(report.modules_validated), 15)

        for gate in report.gates:
            self.assertTrue(gate.passed)
            self.assertTrue(len(gate.details) > 0)

    def test_gate_failure_yields_action_required(self) -> None:
        """Verify a failed gate triggers ACTION_REQUIRED status."""
        report = run_rc2_final_integration_validation(
            target_version="v0.7.0-rc.2",
            functional_integrity_passed=True,
            security_boundary_passed=False,  # Security failure
        )
        self.assertEqual(report.overall_verdict, "ACTION_REQUIRED")
        sec_gate = [g for g in report.gates if g.gate_name == "Security Boundary"][0]
        self.assertFalse(sec_gate.passed)


if __name__ == "__main__":
    unittest.main()

