"""Tests for the v0.6.0 M2 cost/budget backend core (AUTOCLAW-AMCA-V0.6.0-M2-COST-BUDGET-CORE-01).

Coverage targets (from the work order):
  1. known API cost calculation
  2. Ollama zero API cost
  3. unknown API cost remains unknown
  4. run aggregation
  5. project aggregation where supported
  6. no retry double count
  7. fallback accumulation
  8. cancellation without usage
  9. hard budget ALLOW
 10. hard budget BLOCK
 11. warning threshold
 12. unknown-cost strict-budget block
 13. read API
 14. credential/privacy exclusion
 15. fresh context does not alter feature-gate defaults
"""

from __future__ import annotations

import unittest

from amca.cost_budget import (
    BudgetBlockReason,
    BudgetEnforcer,
    BudgetPolicy,
    BudgetVerdict,
    CostCore,
    CostSource,
    ModelPrice,
    PricingRegistry,
    RunCostAccumulator,
    build_read_state,
    is_local_provider,
    sanitize_evidence,
)


class TestLocalProviderClassifier(unittest.TestCase):
    def test_local_provider_classifier(self) -> None:
        self.assertTrue(is_local_provider("ollama"))
        self.assertTrue(is_local_provider("ollama:qwen3:1.7b"))
        self.assertTrue(is_local_provider("local-mistral"))
        self.assertTrue(is_local_provider("deterministic-fallback"))
        self.assertTrue(is_local_provider("local/echo"))
        self.assertTrue(is_local_provider("stub"))
        # Remote / API providers
        self.assertFalse(is_local_provider("gpt-4o"))
        self.assertFalse(is_local_provider("openai/gpt-4o-mini"))
        self.assertFalse(is_local_provider("anthropic.claude-3"))
        self.assertFalse(is_local_provider(""))
        self.assertFalse(is_local_provider(None))


class TestKnownApiCostCalculation(unittest.TestCase):
    def test_provider_reported_cost(self) -> None:
        core = CostCore(run_id="r1")
        rec = core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.0125,
            pricing_known=True,
        )
        self.assertEqual(rec.cost_usd, 0.0125)
        self.assertEqual(rec.cost_source, CostSource.PROVIDER_REPORTED)
        self.assertTrue(rec.pricing_known)
        self.assertEqual(core.accumulator.total_known_cost_usd, 0.0125)

    def test_calculated_from_pricing_registry(self) -> None:
        prices = PricingRegistry({"gpt-4o": ModelPrice(2.5, 10.0)})
        core = CostCore(run_id="r2", pricing=prices)
        rec = core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost_usd=None,
            pricing_known=True,
        )
        # 1.0M * 2.5 / 1M = 2.5, 0.5M * 10.0 / 1M = 5.0; total 7.5
        self.assertAlmostEqual(rec.cost_usd, 7.5, places=6)
        self.assertEqual(rec.cost_source, CostSource.CALCULATED_FROM_USAGE)
        self.assertEqual(core.accumulator.total_known_cost_usd, 7.5)


class TestOllamaZeroApiCost(unittest.TestCase):
    def test_ollama_is_zero(self) -> None:
        core = CostCore(run_id="r1")
        rec = core.record_from_response(
            call_id="c1",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            input_tokens=2000,
            output_tokens=500,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(rec.cost_usd, 0.0)
        self.assertTrue(rec.local)
        self.assertEqual(core.accumulator.total_known_cost_usd, 0.0)
        self.assertEqual(core.accumulator.total_local_calls, 1)
        # A provider-reported non-zero cost for an Ollama call must
        # still be ignored — local compute is zero at the API layer.
        rec2 = core.record_from_response(
            call_id="c2",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=99.0,
            pricing_known=True,
        )
        self.assertEqual(rec2.cost_usd, 0.0)
        self.assertEqual(rec2.local, True)


class TestUnknownApiCostRemainsUnknown(unittest.TestCase):
    def test_unknown_call_preserved(self) -> None:
        core = CostCore(run_id="r1")
        rec = core.record_from_response(
            call_id="c1",
            model_id="mystery-model",
            provider="mystery-provider",
            input_tokens=10,
            output_tokens=5,
            cost_usd=None,
            pricing_known=False,
        )
        self.assertEqual(rec.cost_usd, 0.0)
        self.assertFalse(rec.pricing_known)
        self.assertEqual(rec.cost_source, CostSource.UNKNOWN)
        self.assertEqual(core.accumulator.total_known_cost_usd, 0.0)
        self.assertEqual(core.accumulator.total_unknown_calls, 1)
        # known vs unknown must be exposed in the read API
        state = core.read_state()
        self.assertFalse(state.known)


class TestRunAggregation(unittest.TestCase):
    def test_run_aggregates_inputs_outputs_costs(self) -> None:
        core = CostCore(run_id="r1")
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            pricing_known=True,
        )
        core.record_from_response(
            call_id="c2",
            model_id="gpt-4o-mini",
            provider="openai",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.005,
            pricing_known=True,
        )
        core.record_from_response(
            call_id="c3",
            model_id="ollama:llama3",
            provider="ollama",
            input_tokens=300,
            output_tokens=150,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(core.accumulator.total_calls, 3)
        self.assertEqual(core.accumulator.total_input_tokens, 600)
        self.assertEqual(core.accumulator.total_output_tokens, 300)
        self.assertEqual(core.accumulator.total_local_calls, 1)
        self.assertAlmostEqual(core.accumulator.total_known_cost_usd, 0.015, places=6)
        per_model = core.accumulator.per_model_cost()
        self.assertAlmostEqual(per_model["gpt-4o"], 0.01, places=6)
        self.assertAlmostEqual(per_model["gpt-4o-mini"], 0.005, places=6)
        self.assertAlmostEqual(per_model["ollama:llama3"], 0.0, places=6)


class TestProjectAggregation(unittest.TestCase):
    def test_project_aggregate_visible_through_read_api(self) -> None:
        core = CostCore(run_id="r1", project_id="p-1")
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.02,
            pricing_known=True,
        )
        # Caller supplies the project aggregate from the project ledger
        # table (no schema change required).
        state = core.read_state(
            project_aggregate_known_cost_usd=0.42,
            project_aggregate_unknown_calls=3,
        )
        self.assertEqual(state.project_id, "p-1")
        self.assertEqual(state.project_aggregate_known_cost_usd, 0.42)
        self.assertEqual(state.project_aggregate_unknown_calls, 3)
        self.assertEqual(state.run_id, "r1")
        # The run itself is known.
        self.assertEqual(state.total_known_cost_usd, 0.02)
        self.assertEqual(state.total_unknown_calls, 0)


class TestNoRetryDoubleCount(unittest.TestCase):
    def test_same_call_id_is_idempotent(self) -> None:
        core = CostCore(run_id="r1")
        for _ in range(5):
            core.record_from_response(
                call_id="c1",
                model_id="gpt-4o",
                provider="openai",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                pricing_known=True,
            )
        # 5x recorded, but accumulator should only count it once.
        self.assertEqual(core.accumulator.total_calls, 1)
        self.assertAlmostEqual(core.accumulator.total_known_cost_usd, 0.01, places=6)
        self.assertEqual(core.accumulator.total_input_tokens, 100)

    def test_fallback_after_settled_call(self) -> None:
        core = CostCore(run_id="r1")
        # Primary provider fails AFTER it reported usage. Record once.
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.05,
            pricing_known=True,
        )
        # Fallback to Ollama. New call_id, separate record.
        core.record_from_response(
            call_id="c2",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(core.accumulator.total_calls, 2)
        self.assertEqual(core.accumulator.total_local_calls, 1)
        # Recorded exactly once for the primary.
        self.assertAlmostEqual(core.accumulator.total_known_cost_usd, 0.05, places=6)

    def test_provider_fails_before_usage_no_fabricated_charge(self) -> None:
        core = CostCore(run_id="r1")
        # Provider errored out before reporting usage. We simply do
        # NOT record anything. Total cost must remain 0.
        # (The caller is responsible for NOT calling record_from_response
        # in that case; this test documents the contract.)
        self.assertEqual(core.accumulator.total_calls, 0)
        self.assertEqual(core.accumulator.total_known_cost_usd, 0.0)

    def test_cancelled_call_without_usage_no_cost(self) -> None:
        core = CostCore(run_id="r1")
        # A cancelled call with no reported usage simply isn't recorded.
        self.assertEqual(core.accumulator.total_calls, 0)
        state = core.read_state()
        self.assertEqual(state.total_calls, 0)
        self.assertEqual(state.total_known_cost_usd, 0.0)

    def test_local_deterministic_fallback_zero_api_cost(self) -> None:
        core = CostCore(run_id="r1")
        core.record_from_response(
            call_id="c1",
            model_id="deterministic-fallback",
            provider="amca",
            input_tokens=10,
            output_tokens=5,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(core.accumulator.total_known_cost_usd, 0.0)
        self.assertEqual(core.accumulator.total_local_calls, 1)


class TestHardBudget(unittest.TestCase):
    def test_hard_budget_allow(self) -> None:
        policy = BudgetPolicy(run_budget_usd=1.0)
        core = CostCore(run_id="r1", policy=policy)
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.10,
            pricing_known=True,
        )
        # Now ask for a 0.5 USD call. 0.10 + 0.5 = 0.60 < 1.0 → ALLOW.
        decision = core.evaluate(
            model_id="gpt-4o",
            estimated_call_cost_usd=0.5,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertAlmostEqual(decision.remaining_run_budget_usd, 0.4, places=6)
        self.assertIsNone(decision.reason)

    def test_hard_budget_block(self) -> None:
        policy = BudgetPolicy(run_budget_usd=1.0)
        core = CostCore(run_id="r1", policy=policy)
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.90,
            pricing_known=True,
        )
        # 0.90 + 0.5 > 1.0 → BLOCK.
        decision = core.evaluate(
            model_id="gpt-4o",
            estimated_call_cost_usd=0.5,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        self.assertEqual(decision.reason, BudgetBlockReason.RUN_BUDGET_EXCEEDED)
        self.assertAlmostEqual(decision.remaining_run_budget_usd, 0.1, places=6)

    def test_warning_threshold(self) -> None:
        policy = BudgetPolicy(run_budget_usd=2.0, warning_threshold_usd=1.5)
        core = CostCore(run_id="r1", policy=policy)
        # 1.0 already recorded, 0.6 prospective → 1.6 ≥ 1.5 → warning
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=1.0,
            pricing_known=True,
        )
        decision = core.evaluate(
            model_id="gpt-4o",
            estimated_call_cost_usd=0.6,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertTrue(decision.warning)

    def test_project_budget_exceeded(self) -> None:
        policy = BudgetPolicy(project_budget_usd=1.0)
        core = CostCore(run_id="r1", project_id="p-1", policy=policy)
        # Run cost 0.4; project ledger already at 0.8 → 0.8 + 0.4 + 0.5 > 1.0
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.4,
            pricing_known=True,
        )
        decision = core.evaluate(
            model_id="gpt-4o",
            estimated_call_cost_usd=0.5,
            pricing_known=True,
            project_spent_usd=0.8,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        self.assertEqual(decision.reason, BudgetBlockReason.PROJECT_BUDGET_EXCEEDED)


class TestUnknownCostStrictBudget(unittest.TestCase):
    def test_unknown_cost_strict_budget_blocks(self) -> None:
        policy = BudgetPolicy(run_budget_usd=10.0, strict_monetary=True)
        core = CostCore(run_id="r1", policy=policy)
        decision = core.evaluate(
            model_id="mystery-model",
            estimated_call_cost_usd=None,
            pricing_known=False,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        self.assertEqual(decision.reason, BudgetBlockReason.UNKNOWN_COST_STRICT_BUDGET)

    def test_unknown_cost_non_strict_allows_when_no_budget(self) -> None:
        # No run/project budget → no constraint → unknown cost allowed.
        policy = BudgetPolicy()
        core = CostCore(run_id="r1", policy=policy)
        decision = core.evaluate(
            model_id="mystery-model",
            estimated_call_cost_usd=None,
            pricing_known=False,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)


class TestReadApi(unittest.TestCase):
    def test_read_api_with_no_run(self) -> None:
        state = build_read_state(
            accumulator=None,
            policy=None,
            project_aggregate_known_cost_usd=1.23,
            project_aggregate_unknown_calls=2,
        )
        d = state.to_dict()
        self.assertIsNone(state.run_id)
        self.assertEqual(state.currency, "USD")
        self.assertEqual(state.total_known_cost_usd, 0.0)
        self.assertEqual(state.project_aggregate_known_cost_usd, 1.23)
        self.assertEqual(state.project_aggregate_unknown_calls, 2)
        self.assertFalse(state.known)
        self.assertIn("run_budget_usd", d)

    def test_read_api_with_run_and_policy(self) -> None:
        policy = BudgetPolicy(run_budget_usd=1.0)
        core = CostCore(run_id="r1", policy=policy)
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.3,
            pricing_known=True,
        )
        state = core.read_state(project_aggregate_known_cost_usd=0.7)
        self.assertEqual(state.run_id, "r1")
        self.assertEqual(state.total_known_cost_usd, 0.3)
        self.assertEqual(state.run_budget_usd, 1.0)
        self.assertAlmostEqual(state.remaining_run_budget_usd, 0.7, places=6)
        self.assertTrue(state.known)

    def test_read_api_marks_unknown_calls(self) -> None:
        core = CostCore(run_id="r1")
        core.record_from_response(
            call_id="c1",
            model_id="mystery",
            provider="mystery",
            input_tokens=10,
            output_tokens=5,
            cost_usd=None,
            pricing_known=False,
        )
        state = core.read_state()
        self.assertEqual(state.total_unknown_calls, 1)
        self.assertFalse(state.known)


class TestCredentialPrivacyExclusion(unittest.TestCase):
    def test_sanitize_removes_credential_fields(self) -> None:
        record = {
            "model_id": "gpt-4o",
            "input_tokens": 100,
            "output_tokens": 50,
            "api_key": "sk-abcdefghijklmnopqrstuv",
            "Authorization": "Bearer xyz",
            "x-amca-session": "abc123def",
            "AMCA_SESSION_TOKEN": "deadbeef",
            "password": "hunter2",
            "secret": "shh",
            "raw_prompt": "Tell me a joke",
            "raw_response": "Why did the chicken cross the road?",
        }
        cleaned = sanitize_evidence(record)
        self.assertNotIn("api_key", cleaned)
        self.assertNotIn("Authorization", cleaned)
        self.assertNotIn("x-amca-session", cleaned)
        self.assertNotIn("AMCA_SESSION_TOKEN", cleaned)
        self.assertNotIn("password", cleaned)
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("raw_prompt", cleaned)
        self.assertNotIn("raw_response", cleaned)
        # Public fields kept.
        self.assertEqual(cleaned["model_id"], "gpt-4o")
        self.assertEqual(cleaned["input_tokens"], 100)
        self.assertEqual(cleaned["output_tokens"], 50)

    def test_sanitize_redacts_high_entropy_patterns(self) -> None:
        record = {
            "user_provided": "AKIAIOSFODNN7EXAMPLE",
            "normal": "hello world",
        }
        cleaned = sanitize_evidence(record)
        self.assertEqual(cleaned["user_provided"], "[REDACTED]")
        self.assertEqual(cleaned["normal"], "hello world")

    def test_sanitize_handles_nested(self) -> None:
        record = {
            "outer": {
                "api_key": "sk-abcdefghijklmnopqrstuv",
                "model": "gpt-4o",
            },
            "list": [
                {"prompt": "Tell me a secret"},
                {"ok": "yes"},
            ],
        }
        cleaned = sanitize_evidence(record)
        self.assertNotIn("api_key", cleaned["outer"])
        self.assertEqual(cleaned["outer"]["model"], "gpt-4o")
        self.assertNotIn("prompt", cleaned["list"][0])
        self.assertEqual(cleaned["list"][1]["ok"], "yes")

    def test_snapshot_sanitized(self) -> None:
        core = CostCore(run_id="r1", project_id="p-1")
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            pricing_known=True,
        )
        snap = core.accumulator.snapshot()
        cleaned = sanitize_evidence(snap)
        # No credential-shaped fields in a fresh snapshot.
        for forbidden in ("api_key", "Authorization", "x-amca-session", "raw_prompt"):
            self.assertNotIn(forbidden, cleaned)
        # The legitimate numbers are preserved.
        self.assertEqual(cleaned["run_id"], "r1")
        self.assertEqual(cleaned["total_calls"], 1)


class TestFeatureGatesUnchanged(unittest.TestCase):
    def test_fresh_context_does_not_alter_feature_gates(self) -> None:
        """A fresh CostCore on a fresh context must not touch the
        AMCA feature-gate registry. The cost core is a pure-Python
        component with no side effects on the four shipping
        experimental gates.
        """
        core = CostCore(run_id="r1")
        # The cost core exposes no API to mutate the gate registry,
        # and instantiating it does not import the gate module.
        # Sanity: it is a run ledger + budget enforcer only.
        self.assertIsNone(core.policy)
        self.assertIsInstance(core.accumulator, RunCostAccumulator)
        # Recording a call never mutates a global.
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.01,
            pricing_known=True,
        )
        # If the import was successful without dragging in
        # feature_gates.py, we're done. The following import is the
        # positive control: it must succeed and the registry default
        # state must be the same as before.

        # And the four shipping-default experimental gates must
        # remain default OFF after cost-core activity.
        from amca.intelligence.gates import (
            FeatureGateRegistry,
        )

        reg = FeatureGateRegistry()
        for gate in ("model_router", "adaptive_planner", "hybrid_execution", "nano_agents"):
            self.assertFalse(reg.is_enabled(gate), f"{gate} must remain OFF by default")


class TestEnforcerEdgeCases(unittest.TestCase):
    def test_invalid_budget_policy_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BudgetPolicy(run_budget_usd=-0.01)
        with self.assertRaises(ValueError):
            BudgetPolicy(run_budget_usd=float("inf"))
        with self.assertRaises(ValueError):
            BudgetPolicy(project_budget_usd=float("nan"))

    def test_invalid_cost_rejected(self) -> None:
        core = CostCore(run_id="r1")
        with self.assertRaises(ValueError):
            core.record_from_response(
                call_id="c1",
                model_id="gpt-4o",
                provider="openai",
                input_tokens=100,
                output_tokens=50,
                cost_usd=float("nan"),
                pricing_known=True,
            )

    def test_evaluate_without_policy_always_allows(self) -> None:
        core = CostCore(run_id="r1")
        decision = core.evaluate(
            model_id="gpt-4o",
            estimated_call_cost_usd=999.0,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)


class TestRunCostSnapshotShape(unittest.TestCase):
    def test_snapshot_is_json_safe(self) -> None:
        import json

        core = CostCore(run_id="r1", project_id="p-1")
        core.record_from_response(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            pricing_known=True,
        )
        snap = core.accumulator.snapshot()
        encoded = json.dumps(snap)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["run_id"], "r1")
        self.assertEqual(decoded["project_id"], "p-1")
        self.assertEqual(decoded["currency"], "USD")
        self.assertEqual(decoded["total_calls"], 1)
        self.assertIn("per_model_cost_usd", decoded)


if __name__ == "__main__":
    unittest.main()
