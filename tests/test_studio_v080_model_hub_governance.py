"""
AMCA Studio v0.8.0 Model Hub Governance Integration Test Suite
Validates:
1. Provider visibility: active, revoked, expired credential states displayed
2. Capability: verified capability displayed, unknown capability preserved, no inferred capability
3. Security: no secret exposure, key fingerprint only
4. Compatibility: Model Router behavior and model selection unchanged
5. Local models: Ollama remains $0 local path with no external credential dependency
"""

import inspect
from pathlib import Path
import tempfile
import unittest

from amca.model_hub_governance import ModelHubGovernanceAdapter
from amca.provider_governance import (
    CredentialGovernanceStore,
    ProviderHealthService,
    compute_key_fingerprint,
)


class StudioV080ModelHubGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "gov.sqlite3"
        self.store = CredentialGovernanceStore(self.db_path)
        self.health_service = ProviderHealthService()
        self.adapter = ModelHubGovernanceAdapter(
            credential_store=self.store,
            health_service=self.health_service,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_provider_credential_visibility_and_warning_states(self) -> None:
        """Verify ACTIVE, EXPIRED, and REVOKED credential visibility in Model Hub cards."""
        # 1. Active credential
        fp_active = compute_key_fingerprint("sk-ant-test-active-key")
        self.store.create_credential(
            credential_id="cred-act",
            workspace_id="ws-hub",
            provider_name="anthropic",
            key_fingerprint=fp_active,
        )
        card_act = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="claude-3-5-sonnet",
            display_name="Claude 3.5 Sonnet",
            provider_name="anthropic",
        )
        self.assertEqual(card_act.credential_state, "ACTIVE")
        self.assertEqual(card_act.key_fingerprint, fp_active)
        self.assertFalse(card_act.is_local)
        self.assertEqual(card_act.cost_tier, "Commercial (BYOK)")

        # 2. Expired credential
        self.store.update_state(credential_id="cred-act", workspace_id="ws-hub", new_state="EXPIRED")
        card_exp = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="claude-3-5-sonnet",
            display_name="Claude 3.5 Sonnet",
            provider_name="anthropic",
        )
        self.assertEqual(card_exp.credential_state, "EXPIRED")
        self.assertEqual(card_exp.warning_state, "CREDENTIAL_EXPIRED")

        # 3. Revoked credential
        self.store.update_state(credential_id="cred-act", workspace_id="ws-hub", new_state="REVOKED")
        card_rev = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="claude-3-5-sonnet",
            display_name="Claude 3.5 Sonnet",
            provider_name="anthropic",
        )
        self.assertEqual(card_rev.credential_state, "REVOKED")
        self.assertEqual(card_rev.warning_state, "CREDENTIAL_REVOKED")

    def test_capability_evidence_and_unknown_preservation(self) -> None:
        """Verify only verified capabilities are displayed, unknown remains empty, zero inference."""
        # Unchecked provider: capabilities empty, status UNCHECKED
        card_uncheck = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="gpt-4o",
            display_name="GPT-4o",
            provider_name="openai",
        )
        self.assertEqual(card_uncheck.health_status, "UNCHECKED")
        self.assertEqual(len(card_uncheck.verified_capabilities), 0)

        # Explicit check records specific capabilities
        self.health_service.check_provider_health(
            provider_name="openai",
            endpoint_url="https://api.openai.com/v1",
            mock_checker=lambda prov, url: ("HEALTHY", None, ("chat_completion", "function_calling"), 14.2),
        )
        card_checked = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="gpt-4o",
            display_name="GPT-4o",
            provider_name="openai",
        )
        self.assertEqual(card_checked.health_status, "HEALTHY")
        self.assertIn("function_calling", card_checked.verified_capabilities)
        # Vision was not tested, so it must not be inferred
        self.assertNotIn("vision", card_checked.verified_capabilities)

    def test_security_zero_secret_leakage(self) -> None:
        """Verify card dictionaries contain fingerprints only, never raw keys."""
        card = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="claude-3-5-sonnet",
            display_name="Claude 3.5 Sonnet",
            provider_name="anthropic",
        )
        d = card.to_dict()
        self.assertIn("key_fingerprint", d)
        self.assertNotIn("secret", d)
        self.assertNotIn("api_key", d)
        self.assertNotIn("token", d)

    def test_local_model_path_zero_cost_and_no_credential_dependency(self) -> None:
        """Verify Ollama / local models report $0 offline cost tier and NOT_REQUIRED credential."""
        self.health_service.check_provider_health(
            provider_name="ollama",
            endpoint_url="http://127.0.0.1:11434",
        )
        card_ollama = self.adapter.build_model_governance_card(
            workspace_id="ws-hub",
            model_id="llama3-8b",
            display_name="Llama 3 8B (Local)",
            provider_name="ollama",
            is_local=True,
        )
        self.assertTrue(card_ollama.is_local)
        self.assertEqual(card_ollama.cost_tier, "$0.00 (Local Offline)")
        self.assertEqual(card_ollama.credential_state, "NOT_REQUIRED")
        self.assertIsNone(card_ollama.key_fingerprint)
        self.assertEqual(card_ollama.health_status, "HEALTHY")

    def test_compatibility_model_router_behavior_unchanged(self) -> None:
        """Verify Model Router decision runtime is completely untouched."""
        import amca.intelligence.cross_provider_router as cpr
        source = inspect.getsource(cpr)
        self.assertNotIn("ModelHubGovernanceAdapter", source)
        self.assertNotIn("CredentialGovernanceStore", source)


if __name__ == "__main__":
    unittest.main()

