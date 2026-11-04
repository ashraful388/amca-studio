"""
AMCA Studio v0.8.0 Provider Health & Credential Governance Design Test Suite
Validates:
1. Lifecycle states defined (ACTIVE, ROTATING, ROTATED, REVOKED, EXPIRED)
2. Zero plaintext secrets in schema or audit models (key_fingerprint only)
3. Rotation workflow specification and atomic rollback steps documented
4. Provider health contract specification and valid statuses
5. Explicit check model (background_monitoring_permitted is False)
"""

import unittest

from amca.v080_provider_governance_design import (
    CredentialLifecycleMetadata,
    CredentialRotationAuditEvent,
    CredentialRotationStep,
    CredentialRotationWorkflowSpecification,
    ProviderGovernanceDesignPackage,
    ProviderHealthRecord,
    assemble_provider_governance_design,
)


class StudioV080ProviderGovernanceDesignTests(unittest.TestCase):
    def test_credential_lifecycle_states_and_schema(self) -> None:
        """Verify credential lifecycle states and metadata definition."""
        pkg = assemble_provider_governance_design("v0.8.0")
        expected_states = {"ACTIVE", "ROTATING", "ROTATED", "REVOKED", "EXPIRED"}
        self.assertEqual(set(pkg.rotation_workflow.supported_states), expected_states)

        meta = CredentialLifecycleMetadata(
            credential_id="cred-test-1",
            workspace_id="ws-alpha",
            provider_name="anthropic",
            key_fingerprint="sha256:4f8a...c91e",
            state="ACTIVE",
            created_at="2026-09-02T14:00:00Z",
            last_rotated_at=None,
            expires_at=None,
        )
        self.assertIn("key_fingerprint", meta.to_dict())
        self.assertNotIn("secret_key", meta.to_dict())
        self.assertNotIn("api_key", meta.to_dict())

    def test_zero_plaintext_secrets_guarantee(self) -> None:
        """Verify audit and metadata models prohibit storing raw keys or tokens."""
        audit = CredentialRotationAuditEvent(
            audit_id="aud-rot-1",
            workspace_id="ws-alpha",
            operator_role="admin",
            provider_name="openai",
            old_fingerprint="sha256:1111...aaaa",
            new_fingerprint="sha256:2222...bbbb",
            verification_result="SUCCESS",
            timestamp="2026-09-02T14:05:00Z",
        )
        d = audit.to_dict()
        self.assertIn("old_fingerprint", d)
        self.assertIn("new_fingerprint", d)
        self.assertNotIn("old_key", d)
        self.assertNotIn("new_key", d)
        self.assertNotIn("secret", d)

    def test_rotation_workflow_specification(self) -> None:
        """Verify 4-stage atomic rotation workflow with rollback definitions."""
        pkg = assemble_provider_governance_design("v0.8.0")
        flow = pkg.rotation_workflow
        self.assertTrue(flow.supports_in_place_swap)
        self.assertTrue(flow.requires_zero_plaintext_storage)

        step_names = [s.step_name for s in flow.steps]
        expected_steps = [
            "PRE_ROTATION_VALIDATE",
            "ATOMIC_SWAP",
            "POST_ROTATION_VERIFY",
            "COMMIT_AND_AUDIT",
        ]
        self.assertEqual(step_names, expected_steps)
        for step in flow.steps:
            self.assertTrue(len(step.description) > 0)
            self.assertTrue(len(step.rollback_action) > 0)

    def test_provider_health_contract(self) -> None:
        """Verify provider health record schema and statuses."""
        pkg = assemble_provider_governance_design("v0.8.0")
        expected_statuses = {"HEALTHY", "DEGRADED", "UNREACHABLE", "UNCHECKED"}
        self.assertEqual(set(pkg.supported_health_statuses), expected_statuses)

        record = ProviderHealthRecord(
            provider_name="ollama-local",
            endpoint_url="http://127.0.0.1:11434",
            last_checked="2026-09-02T14:10:00Z",
            status="HEALTHY",
            failure_reason=None,
            capabilities_checked=("chat_completion", "embeddings"),
            latency_ms=18.4,
        )
        rec_d = record.to_dict()
        self.assertEqual(rec_d["status"], "HEALTHY")
        self.assertIn("embeddings", rec_d["capabilities_checked"])

    def test_explicit_check_model_no_background_telemetry(self) -> None:
        """Verify background monitoring is explicitly prohibited."""
        pkg = assemble_provider_governance_design("v0.8.0")
        self.assertFalse(pkg.background_monitoring_permitted)
        self.assertEqual(pkg.overall_status, "PROVIDER_GOVERNANCE_DESIGN_APPROVED")


if __name__ == "__main__":
    unittest.main()

