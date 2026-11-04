"""Cross-provider router integration tests.

Tests for M1 Cross-Provider Router Integration
(opencode/amca-v0.6.0-m1-cross-provider-router-integration-01).
"""

import tempfile
import os
import unittest

from amca.intelligence import (
    ContractMetadata,
    FeatureGateRegistry,
    build_routing_candidates_from_model_hub,
    route_cross_provider,
    route_cross_provider_with_fallback,
    resolve_adapter_for_candidate,
    RoutingPolicy,
    Sensitivity,
)
from amca.model_hub import ModelHubStore
from amca.intelligence import RoutingPolicy


class MockCredentialStore:
    def get(self, ref):
        return "test-key"

    def set(self, ref, secret):
        pass

    def delete(self, ref):
        return True


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="test",
        producer="test",
        created_at="2024-01-01T00:00:00+00:00",
        sensitivity=Sensitivity.INTERNAL,
    )


class CrossProviderRouterTests(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        self.store = ModelHubStore(self.db_path)
        self.cred_store = MockCredentialStore()

    def tearDown(self):
        os.unlink(self.db_path)

    def _create_providers_with_pricing(self):
        # OpenAI provider
        self.provider1, _ = self.store.create_provider({
            "provider_kind": "openai",
            "name": "OpenAI",
            "endpoint": "https://api.openai.com/v1",
            "model_ids": ["gpt-4o", "gpt-4o-mini"],
            "pricing_mode": "auto",
        })

        # Anthropic provider
        self.provider2, _ = self.store.create_provider({
            "provider_kind": "anthropic",
            "name": "Anthropic",
            "endpoint": "https://api.anthropic.com",
            "model_ids": ["claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],
            "pricing_mode": "auto",
        })

        # DeepSeek provider (OpenAI-compatible)
        self.provider3, _ = self.store.create_provider({
            "provider_kind": "deepseek",
            "name": "DeepSeek",
            "endpoint": "https://api.deepseek.com/v1",
            "model_ids": ["deepseek-chat", "deepseek-coder"],
            "pricing_mode": "auto",
        })

        # Add pricing
        with self.store._transaction() as db:
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.openai.gpt4o",
                    "byok",
                    "openai",
                    "gpt-4o",
                    "USD",
                    5.0,
                    2.5,
                    15.0,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.openai.gpt4o-mini",
                    "byok",
                    "openai",
                    "gpt-4o-mini",
                    "USD",
                    0.15,
                    0.075,
                    0.6,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.anthropic.sonnet",
                    "byok",
                    "anthropic",
                    "claude-3-5-sonnet-20241022",
                    "USD",
                    3.0,
                    1.5,
                    15.0,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.anthropic.haiku",
                    "byok",
                    "anthropic",
                    "claude-3-haiku-20240307",
                    "USD",
                    0.25,
                    0.125,
                    1.25,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.deepseek.chat",
                    "byok",
                    "deepseek",
                    "deepseek-chat",
                    "USD",
                    0.14,
                    0.14,
                    0.28,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.deepseek.coder",
                    "byok",
                    "deepseek",
                    "deepseek-coder",
                    "USD",
                    0.14,
                    0.14,
                    0.28,
                    "{}",
                    0,
                    "test",
                    "2024-01-01T00:00:00+00:00",
                    "current",
                    None,
                ),
            )

    def _meta_factory(self, record_id):
        return ContractMetadata(
            record_id=record_id,
            correlation_id="test",
            producer="test",
            created_at="2024-01-01T00:00:00+00:00",
            sensitivity=Sensitivity.INTERNAL,
        )

    def _policy(self, **kwargs):
        defaults = dict(
            meta=self._meta_factory("policy"),
            allowed_providers=("openai", "anthropic", "deepseek"),
            required_capabilities=(),
            local_only=False,
            cloud_disabled=False,
            max_budget=10.0,
            strict_unknown_cost=False,
            maximum=2,
        )
        defaults.update(kwargs)
        return RoutingPolicy(**defaults)

    def _gates(self):
        return FeatureGateRegistry(model_router=True)

    def test_build_routing_candidates(self):
        self._create_providers_with_pricing()
        candidates = build_routing_candidates_from_model_hub(
            meta_factory=self._meta_factory,
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
        )
        self.assertEqual(len(candidates), 6)
        for c in candidates:
            self.assertTrue(c.available)
            self.assertIn(c.provider, ("openai", "anthropic", "deepseek"))
            self.assertFalse(c.local_execution)
            self.assertIsNotNone(c.estimated_cost)

    def test_multi_provider_routing(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-1",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(),
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "routed")
        self.assertIsNotNone(outcome.selected)
        # DeepSeek should be selected due to lowest cost
        self.assertEqual(outcome.selected.provider, "deepseek")

    def test_fallback_chain(self):
        self._create_providers_with_pricing()
        policies = [
            self._policy(
                meta=self._meta_factory("policy-strict"),
                allowed_providers=("openai",),
                max_budget=0.1,  # Very low budget - should reject OpenAI
                maximum=1,
            ),
            self._policy(
                meta=self._meta_factory("policy-fallback"),
                allowed_providers=("anthropic", "deepseek"),
                max_budget=10.0,
                maximum=1,
            ),
        ]
        outcome, policy_idx = route_cross_provider_with_fallback(
            meta_factory=self._meta_factory,
            request_ref="test-req-fallback",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policies=policies,
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "routed")
        self.assertEqual(policy_idx, 1)  # Should use fallback policy
        self.assertIn(outcome.selected.provider, ("anthropic", "deepseek"))

    def test_local_only_policy(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-local",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(local_only=True),
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "blocked")  # No local candidates in test setup

    def test_cloud_disabled_policy(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-no-cloud",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(cloud_disabled=True),
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "blocked")  # No local candidates in test setup

    def test_adapter_resolution(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-1",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(),
            gates=self._gates(),
        )
        self.assertIsNotNone(outcome.selected)
        adapter = resolve_adapter_for_candidate(
            outcome.selected, self.store, MockCredentialStore()
        )
        self.assertEqual(adapter.provider, outcome.selected.provider)
        self.assertEqual(adapter.model, outcome.selected.model)
        self.assertEqual(adapter.adapter_id, f"router-{outcome.selected.candidate_id}")

    def test_unauthorized_provider_rejected(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-1",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(allowed_providers=("openai",)),  # Only allow OpenAI
            gates=self._gates(),
        )
        # Anthropic and DeepSeek should be rejected
        rejected_ids = [r.candidate_id for r in outcome.rejected]
        self.assertTrue(any("anthropic" in r for r in rejected_ids))
        self.assertTrue(any("deepseek" in r for r in rejected_ids))

    def test_budget_limit_enforced(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-budget",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(max_budget=0.1),  # Very low budget
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "blocked")

    def test_cross_provider_fallback(self):
        self._create_providers_with_pricing()
        policies = [
            self._policy(
                meta=self._meta_factory("policy-strict"),
                allowed_providers=("openai",),
                max_budget=0.1,  # Very low budget - should reject OpenAI
                maximum=1,
            ),
            self._policy(
                meta=self._meta_factory("policy-fallback"),
                allowed_providers=("anthropic", "deepseek"),
                max_budget=10.0,
                maximum=1,
            ),
        ]
        outcome, policy_idx = route_cross_provider_with_fallback(
            meta_factory=self._meta_factory,
            request_ref="test-req-fallback",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policies=policies,
            gates=self._gates(),
        )
        self.assertEqual(outcome.status.value, "routed")
        self.assertEqual(policy_idx, 1)
        self.assertIn(outcome.selected.provider, ("anthropic", "deepseek"))

    def test_evidence_whitelist(self):
        self._create_providers_with_pricing()
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-1",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=self._policy(),
            gates=self._gates(),
        )
        self.assertTrue(set(outcome.evidence.keys()).issubset(
            {"candidate_count", "eligible_count", "rejected_count", "skipped_count", "selected_candidate_id"}
        ))

    def test_gate_off_blocks_routing(self):
        self._create_providers_with_pricing()
        with self.assertRaises(Exception) as cm:
            route_cross_provider(
                meta_factory=self._meta_factory,
                request_ref="test-req-1",
                model_hub_store=self.store,
                credential_store=MockCredentialStore(),
                policy=self._policy(),
                gates=FeatureGateRegistry(),  # model_router is False by default
            )
        self.assertIn("model_router gate is disabled", str(cm.exception))

    def test_local_ollama_zero_cost_semantics(self):
        """Test that local/Ollama candidates are normalized with zero API cost."""
        # Create an Ollama provider (local) by directly inserting into DB
        # since Model Hub requires HTTPS endpoints for create_provider()
        with self.store._transaction() as db:
            timestamp = "2024-01-01T00:00:00+00:00"
            provider_id = "provider.test-ollama"
            db.execute(
                "INSERT INTO model_hub_providers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_id, "ollama", "Ollama", "http://127.0.0.1:11434",
                    f"model-hub.{provider_id}", "catalog_ready", "auto",
                    '["qwen3:1.7b", "llama3:8b"]', timestamp, timestamp
                ),
            )
            # Seed endpoint registry for Ollama
            db.execute(
                "INSERT INTO model_hub_endpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (f"endpoint.test-ollama", "ollama", "http://127.0.0.1:11434",
                 "provider_native", "api_key", "bundled official endpoint registry seed",
                 "2024-01-01T00:00:00+00:00", 0.9, "current"),
            )

        # Add zero-cost pricing for Ollama models
        with self.store._transaction() as db:
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.ollama.qwen", "local", "ollama", "qwen3:1.7b", "USD",
                    0.0, 0.0, 0.0, "{}", 0, "test", "2024-01-01T00:00:00+00:00", "current", None,
                ),
            )
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.ollama.llama", "local", "ollama", "llama3:8b", "USD",
                    0.0, 0.0, 0.0, "{}", 0, "test", "2024-01-01T00:00:00+00:00", "current", None,
                ),
            )

        candidates = build_routing_candidates_from_model_hub(
            meta_factory=self._meta_factory,
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
        )

        # Find Ollama candidates
        ollama_candidates = [c for c in candidates if c.provider == "ollama"]
        self.assertEqual(len(ollama_candidates), 2)

        for c in ollama_candidates:
            self.assertTrue(c.local_execution)
            self.assertEqual(c.estimated_cost, 0.0)

        # Test routing with strict cost policy: local zero-cost should be preferred over unknown-cost remote
        outcome = route_cross_provider(
            meta_factory=self._meta_factory,
            request_ref="test-req-ollama",
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
            policy=RoutingPolicy(
                meta=self._meta_factory("policy"),
                allowed_providers=("ollama", "openai"),
                required_capabilities=(),
                local_only=False,
                cloud_disabled=False,
                max_budget=10.0,
                strict_unknown_cost=True,  # Strict: unknown cost should be rejected
                maximum=1,
            ),
            gates=self._gates(),
        )

        self.assertEqual(outcome.status.value, "routed")
        self.assertEqual(outcome.selected.provider, "ollama")
        self.assertEqual(outcome.selected.estimated_cost, 0.0)

    def test_stale_deleted_configuration_fails_safely(self):
        """Test that deleted/stale Model Hub configuration fails safely."""
        # Create a provider and route successfully
        provider, _ = self.store.create_provider({
            "provider_kind": "openai",
            "name": "OpenAI",
            "endpoint": "https://api.openai.com/v1",
            "model_ids": ["gpt-4o"],
            "pricing_mode": "auto",
        })

        with self.store._transaction() as db:
            db.execute(
                "INSERT INTO model_hub_pricing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "pricing.openai.gpt4o", "byok", "openai", "gpt-4o", "USD",
                    5.0, 2.5, 15.0, "{}", 0, "test", "2024-01-01T00:00:00+00:00", "current", None,
                ),
            )

        candidates = build_routing_candidates_from_model_hub(
            meta_factory=self._meta_factory,
            model_hub_store=self.store,
            credential_store=MockCredentialStore(),
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]

        # Now delete the provider from Model Hub
        self.store.delete_provider(provider["id"])

        # Attempting to resolve adapter for deleted provider should fail safely
        with self.assertRaises(ValueError) as cm:
            resolve_adapter_for_candidate(candidate, self.store, MockCredentialStore())

        error_msg = str(cm.exception)
        self.assertIn("no configured provider found", error_msg.lower())
        # No credential reconstruction, no invented configuration, typed failure


if __name__ == "__main__":
    unittest.main()