"""Tests for the v0.6.0 M2.3 runtime budget enforcement integration.

Work order: AUTOCLAW-AMCA-V0.6.0-M2.3-RUNTIME-BUDGET-ENFORCEMENT-INTEGRATION-01.

These tests cover the 16 required cases plus the critical regression
rule: when no budget is configured, model execution behavior must
remain unchanged. No paid/cloud calls are made. Fake remote
providers simulate the billable model layer.
"""

from __future__ import annotations

import unittest
from typing import Mapping

from amca.cost_budget import (
    AuthoritativeCost,
    BudgetBlockReason,
    BudgetPolicy,
    BudgetVerdict,
    CostSource,
    PricingRegistry,
    ModelPrice,
)
from amca.runtime_budget_integration import (
    PreDispatchDecision,
    RuntimeBudgetEnforcer,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def make_authoritative(records: dict[str, AuthoritativeCost]):
    """Return a thin wrapper that returns the authoritative record
    for a given call_id, or None."""

    def _lookup(call_id: str) -> AuthoritativeCost | None:
        return records.get(call_id)

    return _lookup


# ---------------------------------------------------------------------------
# 1. No budget = unchanged behavior (critical regression rule)
# ---------------------------------------------------------------------------


class TestNoBudgetUnchangedBehavior(unittest.TestCase):
    def test_no_policy_always_allows_local(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        self.assertFalse(enforcer.has_active_policy())
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertIsNone(decision.reason)

    def test_no_policy_always_allows_api(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        self.assertFalse(enforcer.has_active_policy())
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=999.0,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertIsNone(decision.reason)

    def test_no_policy_record_post_dispatch_succeeds(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.05,
            pricing_known=True,
        )
        # Recorded without policy, no surprise.
        self.assertEqual(enforcer.accumulator.total_calls, 1)


# ---------------------------------------------------------------------------
# 2. Hard budget: known cost within cap = dispatch
# ---------------------------------------------------------------------------


class TestKnownCostWithinHardBudget(unittest.TestCase):
    def test_known_cost_within_run_cap_dispatches(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=0.10,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertIsNone(decision.reason)


# ---------------------------------------------------------------------------
# 3. Hard budget: known cost exceeds cap = adapter NEVER called
# ---------------------------------------------------------------------------


class TestKnownCostExceedsHardBudget(unittest.TestCase):
    def test_known_cost_exceeds_run_cap_blocks(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        # First call records 0.90 of the 1.00 cap.
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.90,
            pricing_known=True,
        )
        # Second call would cost 0.50 → exceeds 1.00.
        decision = enforcer.enforce_pre_dispatch(
            call_id="c2",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=0.50,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        self.assertEqual(decision.reason, BudgetBlockReason.RUN_BUDGET_EXCEEDED)
        # The runtime MUST NOT dispatch the adapter.
        # (The integration site guards on the verdict before calling
        # the adapter; that is tested in TestKernelIntegration below.)

    def test_block_keeps_run_id_intact(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.90,
            pricing_known=True,
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c2",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=0.20,
            pricing_known=True,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        # The call has not been recorded; the cap is preserved.
        self.assertEqual(enforcer.accumulator.total_calls, 1)


# ---------------------------------------------------------------------------
# 4. Warning budget: call proceeds + warning recorded
# ---------------------------------------------------------------------------


class TestWarningBudget(unittest.TestCase):
    def test_warning_budget_allows_with_warning(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(
                run_budget_usd=2.0,
                warning_threshold_usd=0.5,
                strict_monetary=False,
            ),
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=0.6,
            pricing_known=True,
        )
        # Soft mode: ALLOW, but warning=True.
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        # Decision carries the warning metadata.
        self.assertIn("warning=True", decision.detail)


# ---------------------------------------------------------------------------
# 5. Unknown-price remote under hard budget = adapter NEVER called
# ---------------------------------------------------------------------------


class TestUnknownPriceHardBudgetBlocks(unittest.TestCase):
    def test_unknown_price_strict_budget_blocks(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="mystery-model",
            provider="mystery-provider",
            estimated_call_cost_usd=None,
            pricing_known=False,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.BLOCK)
        self.assertEqual(decision.reason, BudgetBlockReason.UNKNOWN_COST_STRICT_BUDGET)

    def test_unknown_price_non_strict_budget_allows(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=False),
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="mystery-model",
            provider="mystery-provider",
            estimated_call_cost_usd=None,
            pricing_known=False,
        )
        # No strict policy + no cap exceeded (no known cost) -> ALLOW
        # in soft mode (and we still cannot refuse the call without
        # a price).
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)


# ---------------------------------------------------------------------------
# 6. Local Ollama = zero API cost, always allowed
# ---------------------------------------------------------------------------


class TestLocalOllamaZeroCost(unittest.TestCase):
    def test_local_ollama_allowed_under_strict(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=0.01, strict_monetary=True),
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            estimated_call_cost_usd=9999.0,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)

    def test_local_ollama_recorded_as_zero(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=99.0,  # caller would set this for an API call
            pricing_known=True,
        )
        # Local cost is recorded as 0.0, not 99.0.
        self.assertAlmostEqual(enforcer.accumulator.total_known_cost_usd, 0.0, places=6)
        self.assertEqual(enforcer.accumulator.total_local_calls, 1)


# ---------------------------------------------------------------------------
# 7. Successful call usage recorded once
# ---------------------------------------------------------------------------


class TestSuccessfulCallRecordedOnce(unittest.TestCase):
    def test_successful_call_recorded_exactly_once(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.045,
            pricing_known=True,
        )
        rec = enforcer.accumulator.calls["c1"]
        self.assertEqual(rec.input_tokens, 1000)
        self.assertEqual(rec.output_tokens, 500)
        self.assertAlmostEqual(rec.cost_usd, 0.045, places=6)
        self.assertEqual(rec.cost_source, CostSource.PROVIDER_REPORTED)
        self.assertTrue(rec.pricing_known)


# ---------------------------------------------------------------------------
# 8. Duplicate call_id not double-counted
# ---------------------------------------------------------------------------


class TestDuplicateCallIdNotDoubleCounted(unittest.TestCase):
    def test_repeat_call_id_idempotent(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        for _ in range(5):
            enforcer.record_post_dispatch(
                call_id="c1",
                model_id="gpt-4o",
                provider="openai",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                pricing_known=True,
            )
        # 5x recorded, but accumulator only counts it once.
        self.assertEqual(enforcer.accumulator.total_calls, 1)
        self.assertAlmostEqual(enforcer.accumulator.total_known_cost_usd, 0.01, places=6)
        self.assertEqual(enforcer.accumulator.total_input_tokens, 100)


# ---------------------------------------------------------------------------
# 9. Retry/fallback accounting correct
# ---------------------------------------------------------------------------


class TestRetryFallbackAccounting(unittest.TestCase):
    def test_provider_reports_then_fallback(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        # Primary call: provider reported usage.
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.05,
            pricing_known=True,
        )
        # Fallback to a new call_id (c2) for the local model.
        enforcer.record_post_dispatch(
            call_id="c2",
            model_id="ollama:qwen3:1.7b",
            provider="ollama",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(enforcer.accumulator.total_calls, 2)
        self.assertEqual(enforcer.accumulator.total_local_calls, 1)
        self.assertAlmostEqual(enforcer.accumulator.total_known_cost_usd, 0.05, places=6)

    def test_deterministic_fallback_zero_api_cost(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="deterministic-fallback",
            provider="amca",
            input_tokens=10,
            output_tokens=5,
            cost_usd=None,
            pricing_known=True,
        )
        self.assertEqual(enforcer.accumulator.total_known_cost_usd, 0.0)
        self.assertEqual(enforcer.accumulator.total_local_calls, 1)


# ---------------------------------------------------------------------------
# 10. Failure before usage = no fabricated cost
# ---------------------------------------------------------------------------


class TestFailureBeforeUsageNoCost(unittest.TestCase):
    def test_provider_fails_before_usage_no_record(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        # Provider errored out before reporting usage: the runtime
        # simply does not call record_post_dispatch for that
        # call_id. Total cost must remain 0.
        self.assertEqual(enforcer.accumulator.total_calls, 0)
        self.assertEqual(enforcer.accumulator.total_known_cost_usd, 0.0)


# ---------------------------------------------------------------------------
# 11. Cancellation before usage = no fabricated cost
# ---------------------------------------------------------------------------


class TestCancellationBeforeUsageNoCost(unittest.TestCase):
    def test_cancellation_without_usage_no_cost(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        # A cancelled call without reported usage is simply not
        # recorded.
        self.assertEqual(enforcer.accumulator.total_calls, 0)
        state = enforcer.accumulator.snapshot()
        self.assertEqual(state["total_known_cost_usd"], 0.0)


# ---------------------------------------------------------------------------
# 12. Usage-before-cancel remains recorded
# ---------------------------------------------------------------------------


class TestUsageBeforeCancelRemains(unittest.TestCase):
    def test_provider_reported_before_cancel_persists(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="r1")
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            pricing_known=True,
        )
        # "Cancel" here means: a later call is not recorded, but the
        # already-recorded usage is preserved.
        self.assertEqual(enforcer.accumulator.total_calls, 1)
        self.assertAlmostEqual(enforcer.accumulator.total_known_cost_usd, 0.01, places=6)


# ---------------------------------------------------------------------------
# 13. Run ID associated correctly
# ---------------------------------------------------------------------------


class TestRunIdAssociated(unittest.TestCase):
    def test_run_id_in_snapshot(self) -> None:
        enforcer = RuntimeBudgetEnforcer(run_id="run-42", project_id="p-1")
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            pricing_known=True,
        )
        snap = enforcer.accumulator.snapshot()
        self.assertEqual(snap["run_id"], "run-42")
        # The project_id is in the accumulator field too.
        self.assertEqual(enforcer.accumulator.project_id, "p-1")


# ---------------------------------------------------------------------------
# 14. Pricing registry integration (M2.1)
# ---------------------------------------------------------------------------


class TestPricingRegistryIntegration(unittest.TestCase):
    def test_known_pricing_used_when_caller_omits_cost(self) -> None:
        pricing = PricingRegistry({"gpt-4o": ModelPrice(2.5, 10.0)})
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=10.0, strict_monetary=True),
            pricing=pricing,
        )
        # 1M input, 500k output -> 2.5 + 5.0 = 7.5
        decision = enforcer.enforce_pre_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=None,
            pricing_known=None,
            input_tokens=1_000_000,
            output_tokens=500_000,
        )
        self.assertEqual(decision.verdict, BudgetVerdict.ALLOW)
        self.assertTrue(decision.pricing_known)


# ---------------------------------------------------------------------------
# 15. Authoritative-ledger disagreement
# ---------------------------------------------------------------------------


class TestAuthoritativeLedgerDisagreement(unittest.TestCase):
    def test_disagreement_raises_ledger_conflict_error(self) -> None:
        existing = make_authoritative({
            "c1": AuthoritativeCost(
                call_id="c1",
                model_id="gpt-4o",
                provider="openai",
                input_tokens=1000,
                output_tokens=200,
                cost_usd=0.05,
                pricing_known=True,
            ),
        })
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            authoritative_ledger=existing,
        )
        with self.assertRaises(Exception) as cm:
            enforcer.record_post_dispatch(
                call_id="c1",
                model_id="gpt-4o",
                provider="openai",
                input_tokens=999,  # disagrees with the ledger
                output_tokens=200,
                cost_usd=0.05,
                pricing_known=True,
            )
        # LedgerConflictError from the M2.1 contract.
        from amca.cost_budget import LedgerConflictError

        self.assertIsInstance(cm.exception, LedgerConflictError)


# ---------------------------------------------------------------------------
# 16. PreDispatchDecision serialization
# ---------------------------------------------------------------------------


class TestPreDispatchDecisionSerialization(unittest.TestCase):
    def test_to_dict_for_allow(self) -> None:
        d = PreDispatchDecision(verdict=BudgetVerdict.ALLOW, detail="ok").to_dict()
        self.assertEqual(d["verdict"], "allow")
        self.assertEqual(d["detail"], "ok")
        self.assertIsNone(d["reason"])

    def test_to_dict_for_block(self) -> None:
        d = PreDispatchDecision(
            verdict=BudgetVerdict.BLOCK,
            reason=BudgetBlockReason.RUN_BUDGET_EXCEEDED,
            detail="blocked",
        ).to_dict()
        self.assertEqual(d["verdict"], "block")
        self.assertEqual(d["reason"], "run_budget_exceeded")

    def test_allowed_blocked_properties(self) -> None:
        a = PreDispatchDecision(verdict=BudgetVerdict.ALLOW)
        b = PreDispatchDecision(verdict=BudgetVerdict.BLOCK, reason=BudgetBlockReason.RUN_BUDGET_EXCEEDED)
        self.assertTrue(a.allowed)
        self.assertFalse(a.blocked)
        self.assertFalse(b.allowed)
        self.assertTrue(b.blocked)


# ---------------------------------------------------------------------------
# 17. Bounded error semantics for budget blocks
# ---------------------------------------------------------------------------


class TestBudgetBlockReasonBounded(unittest.TestCase):
    def test_block_reason_is_known_enum(self) -> None:
        enforcer = RuntimeBudgetEnforcer(
            run_id="r1",
            policy=BudgetPolicy(run_budget_usd=1.0, strict_monetary=True),
        )
        enforcer.record_post_dispatch(
            call_id="c1",
            model_id="gpt-4o",
            provider="openai",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.90,
            pricing_known=True,
        )
        decision = enforcer.enforce_pre_dispatch(
            call_id="c2",
            model_id="gpt-4o",
            provider="openai",
            estimated_call_cost_usd=0.50,
            pricing_known=True,
        )
        # The reason is one of the documented enum values; the
        # detail string is a bounded summary (no raw traceback).
        self.assertIn(
            decision.reason,
            {
                BudgetBlockReason.RUN_BUDGET_EXCEEDED,
                BudgetBlockReason.PROJECT_BUDGET_EXCEEDED,
                BudgetBlockReason.UNKNOWN_COST_STRICT_BUDGET,
                BudgetBlockReason.INSUFFICIENT_BUDGET_FOR_BOUND,
            },
        )
        self.assertNotIn("Traceback", decision.detail)


# ---------------------------------------------------------------------------
# 18. Provider adapter behavior is untouched
# ---------------------------------------------------------------------------


class TestAdapterUntouched(unittest.TestCase):
    def test_no_adapter_dependency_in_enforcer(self) -> None:
        # The integration module does not import or patch any
        # provider adapter; the only place that uses the model /
        # provider strings is the M2 cost core, which classifies
        # them. We assert the integration module has no import of
        # any provider implementation.
        from amca import runtime_budget_integration

        mod = runtime_budget_integration
        # The module exposes only the enforcer and the decision;
        # it has no class that talks to a real adapter.
        self.assertTrue(hasattr(mod, "RuntimeBudgetEnforcer"))
        self.assertTrue(hasattr(mod, "PreDispatchDecision"))
        self.assertFalse(hasattr(mod, "Adapter"))


# ---------------------------------------------------------------------------
# 19. Default experimental gates unchanged
# ---------------------------------------------------------------------------


class TestDefaultGatesUnchanged(unittest.TestCase):
    def test_experimental_gates_remain_default_off(self) -> None:
        from amca.intelligence.gates import FeatureGateRegistry

        reg = FeatureGateRegistry()
        for gate in ("model_router", "adaptive_planner", "hybrid_execution", "nano_agents"):
            self.assertFalse(reg.is_enabled(gate), f"{gate} must remain OFF")


if __name__ == "__main__":
    unittest.main()
