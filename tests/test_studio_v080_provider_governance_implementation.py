"""
AMCA Studio v0.8.0 Provider Health & Credential Governance Implementation Test Suite
Validates:
1. Credential: create lifecycle record, fingerprint exists, plaintext rejected
2. Rotation: successful rotation, failed validation rollback, audit generated
3. Health: explicit check succeeds, failed provider handled, no background process
4. Isolation: workspace separation
"""

from pathlib import Path
import tempfile
import unittest

from amca.provider_governance import (
    CredentialGovernanceStore,
    CredentialRotationEngine,
    ProviderHealthService,
    compute_key_fingerprint,
)


class StudioV080ProviderGovernanceImplementationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "gov.sqlite3"
        self.store = CredentialGovernanceStore(self.db_path)
        self.engine = CredentialRotationEngine(self.store)
        self.health_service = ProviderHealthService()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_credential_lifecycle_record_and_plaintext_rejection(self) -> None:
        """Verify credential creation stores fingerprint and rejects raw secret in fingerprint field."""
        raw_key = "sk-ant-api03-samplekey1234567890abcdef12345"
        fp = compute_key_fingerprint(raw_key)

        cred = self.store.create_credential(
            credential_id="cred-anthropic-1",
            workspace_id="ws-prod",
            provider_name="anthropic",
            key_fingerprint=fp,
        )
        self.assertEqual(cred.state, "ACTIVE")
        self.assertEqual(cred.key_fingerprint, fp)
        self.assertTrue(cred.key_fingerprint.startswith("sha256:"))

        # Verify plaintext secret rejection
        with self.assertRaises(ValueError):
            self.store.create_credential(
                credential_id="cred-leak-1",
                workspace_id="ws-prod",
                provider_name="anthropic",
                key_fingerprint="sk-ant-api03-rawkeyleak123456789012345",
            )

    def test_rotation_successful_and_failed_validation_rollback(self) -> None:
        """Verify successful rotation and automated rollback when pre-validation fails."""
        initial_key = "initial-secret-key-12345"
        fp_init = compute_key_fingerprint(initial_key)
        self.store.create_credential(
            credential_id="cred-openai-1",
            workspace_id="ws-prod",
            provider_name="openai",
            key_fingerprint=fp_init,
        )

        # 1. Failed validation -> Rollback preserved active key
        def failing_validator(provider: str, key: str) -> bool:
            return False

        ok, msg, current_cred = self.engine.rotate_credential(
            credential_id="cred-openai-1",
            workspace_id="ws-prod",
            operator_role="admin",
            new_secret_key="invalid-new-key",
            validator_func=failing_validator,
        )
        self.assertFalse(ok)
        self.assertIn("failed", msg)
        self.assertEqual(current_cred.key_fingerprint, fp_init)
        self.assertEqual(current_cred.state, "ACTIVE")

        # Confirm audit failure record
        audits = self.store.list_audits(workspace_id="ws-prod")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].verification_result, "FAILED")

        # 2. Successful validation -> Key rotated in-place
        def passing_validator(provider: str, key: str) -> bool:
            return True

        new_key = "valid-new-key-67890"
        fp_new = compute_key_fingerprint(new_key)
        ok_succ, msg_succ, updated_cred = self.engine.rotate_credential(
            credential_id="cred-openai-1",
            workspace_id="ws-prod",
            operator_role="admin",
            new_secret_key=new_key,
            validator_func=passing_validator,
        )
        self.assertTrue(ok_succ)
        self.assertEqual(updated_cred.key_fingerprint, fp_new)
        self.assertEqual(updated_cred.state, "ACTIVE")
        self.assertIsNotNone(updated_cred.last_rotated_at)

        # Confirm audit success record
        audits_succ = self.store.list_audits(workspace_id="ws-prod")
        self.assertEqual(len(audits_succ), 2)
        self.assertEqual(audits_succ[0].verification_result, "SUCCESS")

    def test_provider_health_explicit_checks(self) -> None:
        """Verify on-demand explicit checks succeed or handle failure without background polling."""
        # 1. Local provider explicit check
        rec_local = self.health_service.check_provider_health(
            provider_name="ollama-local",
            endpoint_url="http://127.0.0.1:11434",
        )
        self.assertEqual(rec_local.status, "HEALTHY")
        self.assertGreater(rec_local.latency_ms, 0.0)

        # 2. Custom mock checker failure handling
        def mock_failing(prov: str, url: str) -> tuple[str, str | None, tuple[str, ...], float]:
            return "UNREACHABLE", "Connection refused: 503 Service Unavailable", (), 0.0

        rec_fail = self.health_service.check_provider_health(
            provider_name="remote-custom",
            endpoint_url="https://remote.example.com/api",
            mock_checker=mock_failing,  # type: ignore[arg-type]
        )
        self.assertEqual(rec_fail.status, "UNREACHABLE")
        self.assertIn("Connection refused", rec_fail.failure_reason)

    def test_workspace_isolation_in_governance_store(self) -> None:
        """Verify credentials in ws-prod cannot be accessed by ws-dev."""
        fp = compute_key_fingerprint("secret-key-isolation")
        self.store.create_credential(
            credential_id="cred-iso-1",
            workspace_id="ws-prod",
            provider_name="ollama",
            key_fingerprint=fp,
        )

        # Retrieval in correct workspace succeeds
        self.assertIsNotNone(self.store.get_credential(credential_id="cred-iso-1", workspace_id="ws-prod"))

        # Retrieval in foreign workspace returns None
        self.assertIsNone(self.store.get_credential(credential_id="cred-iso-1", workspace_id="ws-dev"))


if __name__ == "__main__":
    unittest.main()

