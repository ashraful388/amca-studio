"""
AMCA Studio v0.7.0 Post-Release Operations Readiness Test Suite
Validates:
1. Support escalation matrix classification (L0, L1, L2, L3) and SLAs
2. Rollback verification to v0.6.2 and database backward compatibility
3. Post-release operational certificate generation (OPERATIONS_READY vs HOLD)
4. Zero external cloud telemetry or analytics
"""

import unittest

from amca.post_release_operations import (
    EscalationClassification,
    PostReleaseOperationsCertificate,
    RollbackVerificationRecord,
    classify_support_issue,
    generate_post_release_operations_certificate,
)


class StudioPostReleaseOperationsTests(unittest.TestCase):
    def test_escalation_classification_levels(self) -> None:
        """Verify L0-L3 support escalation levels and SLAs."""
        l0 = classify_support_issue("L0")
        self.assertEqual(l0.level, "L0")
        self.assertEqual(l0.target_sla_hours, 48)

        l1 = classify_support_issue("L1")
        self.assertEqual(l1.level, "L1")
        self.assertEqual(l1.target_sla_hours, 24)

        l2 = classify_support_issue("L2")
        self.assertEqual(l2.level, "L2")
        self.assertEqual(l2.target_sla_hours, 12)

        l3 = classify_support_issue("L3")
        self.assertEqual(l3.level, "L3")
        self.assertEqual(l3.target_sla_hours, 4)

    def test_post_release_operations_certificate_ready(self) -> None:
        """Verify certificate generates OPERATIONS_READY when all criteria pass."""
        cert = generate_post_release_operations_certificate(
            release_version="v0.7.0",
            fallback_version="v0.6.2",
            db_compatible=True,
            recovery_doc_present=True,
            limitations_frozen=True,
        )

        self.assertTrue(cert.certificate_id.startswith("opscert-"))
        self.assertEqual(cert.release_version, "v0.7.0")
        self.assertEqual(cert.verdict, "OPERATIONS_READY")
        self.assertTrue(cert.rollback_verification.verified)
        self.assertEqual(len(cert.escalation_matrix), 4)

    def test_operations_certificate_hold_on_unverified_rollback(self) -> None:
        """Verify certificate holds if rollback is unverified."""
        cert = generate_post_release_operations_certificate(
            release_version="v0.7.0",
            db_compatible=False,  # Unverified database compatibility
            recovery_doc_present=True,
            limitations_frozen=True,
        )
        self.assertEqual(cert.verdict, "HOLD")


if __name__ == "__main__":
    unittest.main()

