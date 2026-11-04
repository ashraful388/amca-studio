from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from amca.hardware import GIB, LocalComputeProfile
from amca.model_hub import (
    AccessSource,
    AutoIntelligenceRouter,
    LocalModelCompatibilityEvaluator,
    ModelAccessIntelligenceAgent,
    ModelCandidate,
    ModelHubStore,
    SelectionPolicy,
)


class ModelHubStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"
        self.store = ModelHubStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_provider_is_provider_centric_and_secret_free(self) -> None:
        provider, credential_ref = self.store.create_provider(
            {"provider_kind": "anthropic", "name": "Anthropic", "endpoint": "", "pricing_mode": "auto"}
        )
        self.assertEqual(provider["model_ids"], [])
        self.assertEqual(provider["endpoint"], "https://api.anthropic.com")
        self.assertNotIn("credential_ref", provider)
        self.assertTrue(credential_ref.startswith("model-hub.provider."))
        self.assertNotIn("secret", str(self.store.list_providers()).casefold())

    def test_provider_catalog_can_be_imported_and_edited_without_a_fixed_model(self) -> None:
        provider, _ = self.store.create_provider({
            "provider_kind": "openai",
            "name": "OpenAI",
            "endpoint": "",
            "model_ids": ["gpt-a", "gpt-b"],
        })
        self.assertEqual(provider["status"], "catalog_ready")
        updated = self.store.update_provider(provider["id"], {
            "name": "Work key",
            "model_ids": ["gpt-b", "gpt-c"],
        })
        self.assertEqual(updated["name"], "Work key")
        self.assertEqual(updated["model_ids"], ["gpt-b", "gpt-c"])
        refreshed = self.store.replace_provider_catalog(provider["id"], ["gpt-live"])
        self.assertEqual(refreshed["model_ids"], ["gpt-live"])

    def test_custom_provider_may_remain_endpoint_unverified(self) -> None:
        provider, _ = self.store.create_provider(
            {"provider_kind": "custom_vendor", "name": "Custom Vendor", "endpoint": ""}
        )
        self.assertEqual(provider["status"], "endpoint_unverified")
        self.assertEqual(provider["endpoint"], "")

    def test_model_team_round_trip_and_persistence(self) -> None:
        team = self.store.save_team(
            {"name": "Research Team", "description": "Mixed access", "model_refs": ["ollama:qwen", "provider.x:model"]}
        )
        reopened = ModelHubStore(self.database)
        self.assertEqual(reopened.list_teams()[0]["id"], team["id"])
        self.assertEqual(reopened.list_teams()[0]["model_refs"], ["ollama:qwen", "provider.x:model"])

    def test_task_cost_keeps_source_breakdown(self) -> None:
        cost = self.store.record_task_cost(
            {
                "task_id": "task-1", "project_id": "p1",
                "total_external_api_cost": 0.2, "amca_api_cost": 0.14,
                "byok_cost": 0.06, "local_external_api_cost": 0,
                "local_compute": {"seconds": 12.8},
                "breakdown": [{"model": "m", "cost_usd": 0.2}],
                "pricing_record_refs": ["price-1"],
                "cost_status": "calculated_from_measured_usage",
            }
        )
        self.assertEqual(cost["amca_api_cost"], 0.14)
        self.assertEqual(cost["byok_cost"], 0.06)
        self.assertEqual(cost["local_external_api_cost"], 0)
        self.assertEqual(cost["pricing_record_refs"], ["price-1"])

    def test_maia_is_dormant_and_uses_registry_level_zero(self) -> None:
        maia = ModelAccessIntelligenceAgent(self.store)
        self.assertFalse(maia.normally_active)
        self.assertEqual(len(maia.nano_roles()), 8)
        self.assertEqual(maia.endpoint_for("openai")["level"], 0)


class RoutingAndCompatibilityTests(unittest.TestCase):
    def candidate(self, ref: str, cost: float | None, quality: float, **changes: object) -> ModelCandidate:
        values = {
            "model_ref": ref, "source": AccessSource.BYOK, "provider": "test",
            "expected_cost_usd": cost, "quality_score": quality,
            "capabilities": frozenset({"text", "tools"}), "context_length": 128_000,
        }
        values.update(changes)
        return ModelCandidate(**values)  # type: ignore[arg-type]

    def test_auto_selects_lowest_cost_only_after_the_eligibility_gate(self) -> None:
        """UPDATED for the canonical Model Router (Task F).

        This asserted that the rationale mentions "quality".  The rationale is
        now produced by the canonical router, which does NOT rank on quality:
        the score came from `_model_quality`, a model-NAME heuristic the manager
        ordered removed from authoritative routing.  What still holds is the
        substance of the test — a candidate that fails the caller's floor is
        excluded, and the cheapest eligible one wins.
        """
        cheap_bad = self.candidate("cheap", 0.01, 0.4)
        adequate = self.candidate("adequate", 0.08, 0.85)
        premium = self.candidate("premium", 0.5, 0.98)
        decision = AutoIntelligenceRouter().resolve([cheap_bad, premium, adequate], minimum_quality=0.8)
        self.assertEqual(decision.model_refs, ("adequate",))
        # The cheapest eligible model won, and the decision explains itself
        # without claiming a quality judgement it cannot support.
        self.assertIn("selected adequate", decision.rationale.casefold())
        self.assertNotIn("higher quality", decision.rationale.casefold())

    def test_unknown_price_does_not_win_lowest_verified_claim(self) -> None:
        unknown = self.candidate("unknown", None, 0.99)
        known = self.candidate("known", 0.1, 0.9)
        decision = AutoIntelligenceRouter().resolve([unknown, known], minimum_quality=0.8)
        self.assertEqual(decision.model_refs, ("known",))

    def test_equal_cost_prefers_capable_local_model_before_quality(self) -> None:
        local = self.candidate(
            "ollama:qwen3:1.7b",
            0.0,
            0.66,
            source=AccessSource.LOCAL,
            provider="ollama",
        )
        cloud = self.candidate("cloud-free", 0.0, 0.99)
        decision = AutoIntelligenceRouter().resolve(
            [cloud, local], minimum_quality=0.65
        )
        self.assertEqual(decision.model_refs, ("ollama:qwen3:1.7b",))

    def test_manual_model_membership_is_authoritative(self) -> None:
        candidates = [self.candidate("a", 1, 0.9), self.candidate("b", 0.01, 0.9)]
        decision = AutoIntelligenceRouter().resolve(
            candidates, policy=SelectionPolicy.MANUAL_MODEL_SET, manual_refs=["a"]
        )
        self.assertEqual(decision.model_refs, ("a",))

    def profile(self, *, ram: int, disk: int) -> LocalComputeProfile:
        return LocalComputeProfile(
            operating_system="test", architecture="x64", cpu="test", logical_cores=8,
            total_ram_bytes=ram, available_ram_bytes=ram, disk_path=".", free_disk_bytes=disk,
        )

    def test_local_compatibility_green_and_red_are_deterministic(self) -> None:
        evaluator = LocalModelCompatibilityEvaluator()
        green = evaluator.evaluate(self.profile(ram=32 * GIB, disk=100 * GIB), model_size_bytes=4 * GIB)
        red = evaluator.evaluate(self.profile(ram=4 * GIB, disk=2 * GIB), model_size_bytes=8 * GIB)
        self.assertEqual(green.status, "green")
        self.assertEqual(red.status, "red")
        self.assertIn("disk", red.explanation.casefold())


if __name__ == "__main__":
    unittest.main()
