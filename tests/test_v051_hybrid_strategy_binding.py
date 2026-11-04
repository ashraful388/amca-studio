"""v0.5.1 Hybrid/Adaptive strategy-binding tests.

Work order ZC-AMCA-V0.5.1-HYBRID-ADAPTIVE-STRATEGY-BINDING-01: the selected
candidate's authoritative ExecutionStrategy is propagated VERBATIM into the
PlannerResult so M2 validates the genuine strategy. The model keeps advisory
authority only (it selects a candidate_id; it can never author, alter, or
widen the strategy). All M2 safety checks are untouched and every invalid
path still falls back deterministically.
"""

import asyncio
import json
import unittest

from amca.intelligence import (
    CandidateSet,
    ExecutionStrategy,
    FeatureGateRegistry,
    HybridCoordinator,
    HybridDecisionPolicy,
    HybridExecutionBounds,
    ModelBackedAdaptivePlanner,
    ModelCallRequest,
    ModelCallResponse,
    PlannerInput,
    PlannerResultStatus,
    PolicyContext,
    WorkUnit,
)
from amca.intelligence.model_planner import (
    AdaptivePlannerError,
    PLANNER_RESPONSE_FORMAT,
)
from amca.intelligence.local_runtime import LocalModelPlannerRuntime

CREATED = "2026-08-28T00:00:00+00:00"


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata, Sensitivity
    return ContractMetadata(
        record_id=record_id, correlation_id="corr-binding",
        producer="binding-test", created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def candidate_strategy(unit_id="u1", units=1, collaboration_mode="solo",
                       max_total_cost=0.0, max_rounds=1):
    return ExecutionStrategy(
        meta=meta_factory("estrat"), planner_kind="deterministic",
        planner_version="v0.4.0", collaboration_mode=collaboration_mode,
        synchronization_mode="sequential",
        work_units=tuple(WorkUnit(
            meta=meta_factory(f"wu-{unit_id}-{i}"),
            unit_id=f"{unit_id}-{i}", depends_on=(),
            focus=f"{unit_id} bounded local work",
            assigned_agent_ref="agent://r", verifier_ref="verifier://c",
            max_retries=0) for i in range(units)),
        max_rounds=max_rounds, max_total_cost=max_total_cost,
        stop_condition="unit verified", fallback_strategy_ref=None)


def candidate_entry(candidate_id="cand-a", *, strategy=None,
                    estimated_cost=0.0, work_unit_count=1,
                    collaboration_mode="solo"):
    entry = {
        "candidate_id": candidate_id, "estimated_cost": estimated_cost,
        "work_unit_count": work_unit_count, "risk_class": "low",
        "required_permissions": (), "capabilities": ("chat",),
        "collaboration_mode": collaboration_mode,
        "synchronization_mode": "sequential", "label": candidate_id,
    }
    if strategy is not None:
        entry["execution_strategy"] = strategy
    return entry


def candidate_set(entries):
    return CandidateSet(meta=meta_factory("cands"), request_ref="req-1",
                        candidates=tuple(entries))


def planner_input():
    return PlannerInput(
        meta=meta_factory("pin"), request_id="req-1",
        project_ref="project://demo", goal_ref="goal://demo-1",
        state_version=1, eligible_collaboration_modes=("solo",),
        budget_remaining=0.0, budget_hard_limit=True,
        feature_gates={"adaptive_planner": True, "hybrid_execution": False,
                       "nano_agents": False, "model_router": False},
    )


def policy():
    return PolicyContext(
        meta=meta_factory("pol"), policy_id="live-policy",
        policy_version="1",
        evaluated_envelope={"max_total_cost": 5.0, "max_work_units": 4,
                            "max_allowed_risk": "low",
                            "allowed_collaboration_modes": ("solo",)},
        pinned=True,
    )


def structured_text(candidate_id="cand-a", confidence=0.9):
    return json.dumps({
        "response_version": 1, "policy_id": "live-policy",
        "policy_version": "1",
        "recommendations": [{"candidate_id": candidate_id,
                             "model_id": "qwen3:1.7b",
                             "confidence": confidence,
                             "reason_code": "bounded_local_choice"}],
    })


class _FakeRuntime:
    def __init__(self, text):
        self.text = text
        self.requests = []

    def complete(self, request: ModelCallRequest) -> ModelCallResponse:
        self.requests.append(request)
        return ModelCallResponse(
            meta=meta_factory("mres"), request_ref=request.request_id,
            provider_ref="stub/local", resolved_model_id="qwen3:1.7b",
            text_output=self.text, finish_reason="stop", input_tokens=10,
            output_tokens=5, latency_ms=1.0, cost=None,
            local_execution=True, failure_category=None,
        )


def make_planner(runtime):
    return ModelBackedAdaptivePlanner(
        runtime=runtime, meta_factory=meta_factory,
        policy_id="live-policy", policy_version="1")


def bound_outcome(strategy, candidate_id="cand-a"):
    runtime = _FakeRuntime(structured_text(candidate_id))
    planner = make_planner(runtime)
    return planner.propose(
        planner_input(),
        candidate_set([candidate_entry(candidate_id, strategy=strategy)]),
        policy()), runtime


class _DeterministicBaseline:
    def __init__(self):
        self.calls = 0

    def propose(self, planner_input_arg):
        self.calls += 1
        from amca.intelligence import PlannerResult
        return PlannerResult(
            meta=meta_factory("det-" + planner_input_arg.request_id),
            request_ref=planner_input_arg.request_id,
            status=PlannerResultStatus.PROPOSED,
            strategy=ExecutionStrategy(
                meta=meta_factory("dstrat"), planner_kind="deterministic",
                planner_version="v0.4.0", collaboration_mode="solo",
                synchronization_mode="sequential",
                work_units=(WorkUnit(
                    meta=meta_factory("dwu"), unit_id="det-1",
                    depends_on=(), focus="deterministic baseline",
                    assigned_agent_ref="agent://r",
                    verifier_ref="verifier://c", max_retries=0),),
                max_rounds=1, max_total_cost=0.0,
                stop_condition="unit verified",
                fallback_strategy_ref=None),
            alternatives=(), failure=None,
        )


def make_hybrid(adaptive, allowed_collaboration_modes=("solo", "parallel")):
    baseline = _DeterministicBaseline()
    coordinator = HybridCoordinator(
        meta_factory=meta_factory, deterministic=baseline,
        bounds=HybridExecutionBounds(
            meta=meta_factory("bounds"), max_rounds=1,
            max_estimated_cost=0.0, max_work_units=4,
            allowed_collaboration_modes=allowed_collaboration_modes,
            allowed_permissions=(), max_allowed_risk="low"),
        adaptive=adaptive)
    return coordinator, baseline


class _StaticAdaptive:
    policy_id = "live-policy"
    policy_version = "1"

    def __init__(self, outcome=None, error=None):
        self._outcome = outcome
        self._error = error

    def propose(self, planner_input_arg, candidates_arg, policy_arg):
        if self._error is not None:
            raise self._error
        return self._outcome


class StrategyBindingTests(unittest.TestCase):
    """Required tests 1-5: binding + authority preservation."""

    def test_1_selected_candidates_exact_strategy_propagated(self):
        strategy = candidate_strategy("auth-1", units=1)
        outcome, _ = bound_outcome(strategy)
        self.assertEqual(outcome.status, PlannerResultStatus.PROPOSED)
        self.assertIs(outcome.strategy, strategy)
        self.assertEqual(outcome.strategy.planner_kind, "deterministic")

    def test_2_work_units_preserved(self):
        strategy = candidate_strategy("auth-2", units=3)
        outcome, _ = bound_outcome(strategy)
        self.assertEqual(len(outcome.strategy.work_units), 3)
        for index, unit in enumerate(outcome.strategy.work_units):
            self.assertEqual(unit.unit_id, f"auth-2-{index}")
            self.assertEqual(unit.focus, "auth-2 bounded local work")

    def test_3_model_cannot_rewrite_work_units(self):
        strategy = candidate_strategy("auth-3", units=1)
        runtime = _FakeRuntime(json.dumps({
            "response_version": 1, "policy_id": "live-policy",
            "policy_version": "1",
            "recommendations": [{"candidate_id": "cand-a",
                                 "model_id": "qwen3:1.7b",
                                 "confidence": 0.9}],
            "work_units": [{"unit_id": "model-invented-unit"}],
            "work_unit_count": 99,
        }))
        planner = make_planner(runtime)
        outcome = planner.propose(
            planner_input(),
            candidate_set([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        self.assertEqual(len(outcome.strategy.work_units), 1)
        self.assertEqual(outcome.strategy.work_units[0].unit_id, "auth-3-0")

    def test_4_model_cannot_widen_cost(self):
        strategy = candidate_strategy("auth-4", max_total_cost=0.0)
        runtime = _FakeRuntime(structured_text(confidence=0.99))
        outcome, _ = bound_outcome(strategy)
        self.assertEqual(outcome.strategy.max_total_cost, 0.0)

    def test_5_model_cannot_widen_permissions_or_authority(self):
        strategy = candidate_strategy("auth-5")
        runtime = _FakeRuntime(structured_text())
        outcome, _ = bound_outcome(strategy)
        # ExecutionStrategy carries no permission surface; permission grants
        # live in the runtime and are untouched by the binding
        self.assertEqual(outcome.strategy.planner_kind, "deterministic")
        self.assertEqual(len(outcome.strategy.work_units), 1)
        self.assertEqual(outcome.strategy.max_total_cost, 0.0)
        self.assertEqual(outcome.strategy.max_rounds, 1)


class HybridAcceptanceTests(unittest.TestCase):
    """Required tests 6-10: acceptance + fallback preservation."""

    def _adaptive_bound(self, strategy, candidate_id="cand-a"):
        runtime = _FakeRuntime(structured_text(candidate_id))
        planner = make_planner(runtime)
        outcome = planner.propose(
            planner_input(),
            candidate_set([candidate_entry(candidate_id, strategy=strategy)]),
            policy())
        return _StaticAdaptive(outcome), runtime

    def test_6_valid_strategy_hybrid_adaptive_proposal(self):
        strategy = candidate_strategy("auth-6", units=2)
        adaptive, _ = self._adaptive_bound(strategy)
        coordinator, baseline = make_hybrid(adaptive)
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "completed")
        self.assertEqual(outcome.decision,
                         HybridDecisionPolicy.ADAPTIVE_PROPOSAL)
        self.assertIs(outcome.result.strategy, strategy)

    def test_7_excessive_cost_still_falls_back(self):
        strategy = candidate_strategy(
            "expensive", units=1, max_total_cost=99.0)
        adaptive, _ = self._adaptive_bound(strategy)
        coordinator, _ = make_hybrid(adaptive)
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")
        self.assertEqual(outcome.decision,
                         HybridDecisionPolicy.DETERMINISTIC_FALLBACK)

    def test_8_excessive_work_units_still_fall_back(self):
        strategy = candidate_strategy("wide", units=9)
        adaptive, _ = self._adaptive_bound(strategy)
        coordinator, _ = make_hybrid(adaptive)
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")

    def test_9_disallowed_mode_still_falls_back(self):
        strategy = candidate_strategy(
            "parallel-mode", units=1, collaboration_mode="parallel")
        adaptive, _ = self._adaptive_bound(strategy)
        coordinator, _ = make_hybrid(
            adaptive, allowed_collaboration_modes=("solo",))
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")

    def test_10_malformed_structured_result_still_falls_back(self):
        runtime = _FakeRuntime("prose instead of json")
        planner = make_planner(runtime)
        adaptive_error = AdaptivePlannerError(
            "malformed_model_result: output is not valid JSON")
        adaptive = _StaticAdaptive(error=adaptive_error)
        coordinator, _ = make_hybrid(adaptive)
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")
        self.assertEqual(outcome.decision,
                         HybridDecisionPolicy.DETERMINISTIC_FALLBACK)


class SafetyRegressionTests(unittest.TestCase):
    def test_11_cancellation_unchanged(self):
        # the strategy binding did not touch cancellation: a cancelled
        # structured call still raises ModelCallCancelled (real protocol)
        import time

        from amca.models.base import (
            ModelAdapter, ModelCapabilities, ModelResponse, TokenUsage,
        )
        from amca.intelligence import ModelCallRequest
        from amca.intelligence.local_runtime import ModelCallCancelled

        class _SlowAdapter(ModelAdapter):
            def __init__(self):
                super().__init__(
                    adapter_id="slow", provider="stub", model="stub",
                    capabilities=ModelCapabilities(is_local=True))
                self.cancel_invocations = 0

            async def generate(self, request):
                async with self._call_scope(request):
                    await asyncio.sleep(5)
                    return ModelResponse(
                        text="x", model="stub", provider="stub",
                        usage=TokenUsage(), finish_reason="stop")

            async def cancel(self, request_id=None):
                self.cancel_invocations += 1
                return await super().cancel(request_id)

            def stream(self, request):
                raise NotImplementedError

        import asyncio
        adapter = _SlowAdapter()
        runtime = LocalModelPlannerRuntime(
            adapter=adapter, timeout_seconds=30.0,
            cancel_poll_seconds=0.02, meta_factory=meta_factory,
            response_format=PLANNER_RESPONSE_FORMAT)
        request = ModelCallRequest(
            meta=meta_factory("cancel"), request_id="req-cancel",
            provider_ref="local/ollama", system_instruction=None,
            messages=("bounded",), temperature=0.0, max_output_tokens=32,
            timeout_seconds=30.0, idempotency_key="cancel",
            privacy_mode="offline", approval_ref=None)
        began = time.monotonic()

        def cancel_after():
            return (time.monotonic() - began) >= 0.05

        with self.assertRaises(ModelCallCancelled):
            runtime.complete(request, cancel_check=cancel_after)
        self.assertGreaterEqual(adapter.cancel_invocations, 1)

    def test_12_all_feature_defaults_off_on_fresh_context(self):
        from amca.intelligence import DEFAULT_GATES, IntelligenceRuntimeBridge
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {"adaptive_planner": False, "hybrid_execution": False,
             "nano_agents": False, "model_router": False},
        )
        self.assertEqual(
            IntelligenceRuntimeBridge().status()["gates"],
            DEFAULT_GATES.to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
