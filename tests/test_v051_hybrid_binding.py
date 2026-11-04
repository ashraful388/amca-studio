"""v0.5.1 Hybrid/Adaptive strategy-binding tests.

Work order ZC-AMCA-V0.5.1-HYBRID-ADAPTIVE-STRATEGY-BINDING-01: when the real
Adaptive Planner selects an already-authorized candidate, that candidate's
existing deterministic-compatible ExecutionStrategy is propagated VERBATIM
into the PlannerResult so M2 Hybrid validates the genuine strategy. The
model keeps advisory authority only: it selects a candidate_id and can never
author, alter, or widen the strategy. All M2 safety checks are untouched and
every invalid path still falls back deterministically.
"""

import json
import unittest

from amca.intelligence import (
    CandidateSet,
    ContractMetadata,
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
    Sensitivity,
    WorkUnit,
)
from amca.intelligence.model_planner import (
    AdaptivePlannerError,
    PLANNER_RESPONSE_FORMAT,
)
from amca.intelligence.local_runtime import LocalModelPlannerRuntime

CREATED = "2026-08-28T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-binding",
        producer="binding-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def candidate_strategy(unit_id="u1", units=1, collaboration_mode="solo",
                       max_total_cost=0.0, max_rounds=1,
                       required_permissions=None):
    """An authoritative, deterministic-compatible local strategy."""

    return ExecutionStrategy(
        meta=meta_factory("estrat"),
        planner_kind="deterministic",
        planner_version="v0.4.0",
        collaboration_mode=collaboration_mode,
        synchronization_mode="sequential",
        work_units=tuple(
            WorkUnit(
                meta=meta_factory(f"wu-{unit_id}-{i}"),
                unit_id=f"{unit_id}-{i}",
                depends_on=(),
                focus=f"{unit_id} bounded local work",
                assigned_agent_ref="agent://r",
                verifier_ref="verifier://c",
                max_retries=0,
            )
            for i in range(units)
        ),
        max_rounds=max_rounds,
        max_total_cost=max_total_cost,
        stop_condition="unit verified",
        fallback_strategy_ref=None,
    )


def candidate_entry(candidate_id="cand-a", *, strategy=None,
                    estimated_cost=0.0, work_unit_count=1,
                    collaboration_mode="solo"):
    entry = {
        "candidate_id": candidate_id,
        "estimated_cost": estimated_cost,
        "work_unit_count": work_unit_count,
        "risk_class": "low",
        "required_permissions": (),
        "capabilities": ("chat",),
        "collaboration_mode": collaboration_mode,
        "synchronization_mode": "sequential",
        "label": candidate_id,
    }
    if strategy is not None:
        entry["execution_strategy"] = strategy
    return entry


def candidates(entries):
    return CandidateSet(
        meta=meta_factory("cands"), request_ref="req-1",
        candidates=tuple(entries),
    )


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


def recommendation(candidate_id="cand-a", confidence=0.9):
    return [
        {"candidate_id": candidate_id, "model_id": "qwen3:1.7b",
         "confidence": confidence, "reason_code": "bounded_local_choice"},
    ]


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


def structured_text(candidate_id="cand-a", confidence=0.9):
    return json.dumps({
        "response_version": 1, "policy_id": "live-policy",
        "policy_version": "1",
        "recommendations": recommendation(candidate_id, confidence),
    })


def make_planner(runtime):
    return ModelBackedAdaptivePlanner(
        runtime=runtime, meta_factory=meta_factory,
        policy_id="live-policy", policy_version="1")


class StrategyBindingTests(unittest.TestCase):
    def test_selected_candidates_exact_strategy_propagated(self):
        strategy = candidate_strategy("auth-1")
        runtime = _FakeRuntime(structured_text())
        planner = make_planner(runtime)
        outcome = planner.propose(
            planner_input(), candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        self.assertEqual(outcome.status, PlannerResultStatus.PROPOSED)
        # verbatim binding: the exact authoritative strategy object
        self.assertIs(outcome.strategy, strategy)
        self.assertEqual(outcome.strategy.planner_kind, "deterministic")
        self.assertEqual(len(outcome.strategy.work_units), 1)

    def test_work_units_preserved(self):
        strategy = candidate_strategy("auth-2", units=3)
        runtime = _FakeRuntime(structured_text())
        outcome = make_planner(runtime).propose(
            planner_input(),
            candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        self.assertEqual(len(outcome.strategy.work_units), 3)
        for index, unit in enumerate(outcome.strategy.work_units):
            self.assertEqual(unit.unit_id, f"auth-2-{index}")

    def test_model_cannot_rewrite_work_units(self):
        strategy = candidate_strategy("auth-3", units=1)
        # the model's raw output carries conflicting structure hints; the
        # parser ignores everything but the bounded recommendation fields
        # and the strategy binding comes from the local candidate entry
        runtime = _FakeRuntime(json.dumps({
            "response_version": 1, "policy_id": "live-policy",
            "policy_version": "1",
            "recommendations": recommendation(),
            "work_units": [{"unit_id": "model-invented"}],
            "work_unit_count": 99,
        }))
        outcome = make_planner(runtime).propose(
            planner_input(),
            candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        self.assertEqual(len(outcome.strategy.work_units), 1)
        self.assertNotEqual(
            outcome.strategy.work_units[0].unit_id, "model-invented")

    def test_model_cannot_widen_cost(self):
        strategy = candidate_strategy("auth-4", max_total_cost=0.0)
        runtime = _FakeRuntime(structured_text(confidence=0.99))
        outcome = make_planner(runtime).propose(
            planner_input(),
            candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        self.assertEqual(outcome.strategy.max_total_cost, 0.0)

    def test_model_cannot_widen_permissions_or_authority(self):
        strategy = candidate_strategy("auth-5")
        runtime = _FakeRuntime(structured_text())
        planner = make_planner(runtime)
        outcome = planner.propose(
            planner_input(),
            candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        # no permissions exist to widen: the strategy keeps exactly the
        # locally authorized shape (ExecutionStrategy has no permission
        # surface; permission grants live in the runtime, untouched here)
        self.assertEqual(outcome.strategy.planner_kind, "deterministic")
        self.assertEqual(outcome.strategy.max_total_cost, 0.0)
        self.assertEqual(len(outcome.strategy.work_units), 1)


class HybridAcceptanceTests(unittest.TestCase):
    def _bound_planner_outcome(self, strategy):
        runtime = _FakeRuntime(structured_text())
        planner = make_planner(runtime)
        outcome = planner.propose(
            planner_input(),
            candidates([candidate_entry("cand-a", strategy=strategy)]),
            policy())
        return outcome

    def _hybrid(self, adaptive):
        baseline_calls = {"count": 0}

        class Baseline:
            def propose(self, planner_input_arg):
                baseline_calls["count"] += 1
                from amca.intelligence import PlannerResult
                return PlannerResult(
                    meta=meta_factory("det"), request_ref="req-1",
                    status=PlannerResultStatus.PROPOSED,
                    strategy=ExecutionStrategy(
                        meta=meta_factory("dstrat"),
                        planner_kind="deterministic",
                        planner_version="v0.4.0",
                        collaboration_mode="solo",
                        synchronization_mode="sequential",
                        work_units=(WorkUnit(
                            meta=meta_factory("dwu"), unit_id="det-1",
                            depends_on=(), focus="deterministic baseline",
                            assigned_agent_ref="agent://r",
                            verifier_ref="verifier://c", max_retries=0),),
                        max_rounds=1, max_total_cost=0.0,
                        stop_condition="unit verified",
                        fallback_strategy_ref=None),
                    alternatives=(), failure=None)

        coordinator = HybridCoordinator(
            meta_factory=meta_factory, deterministic=Baseline(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("bounds"), max_rounds=1,
                max_estimated_cost=0.0, max_work_units=4,
                allowed_collaboration_modes=("solo",),
                allowed_permissions=(), max_allowed_risk="low"),
            adaptive=adaptive)
        return coordinator, baseline_calls

    def test_valid_strategy_hybrid_adaptive_proposal(self):
        strategy = candidate_strategy("auth-6", units=2)
        adaptive_outcome = self._bound_planner_outcome(strategy)
        self.assertEqual(adaptive_outcome.status,
                         PlannerResultStatus.PROPOSED)

        class Adaptive:
            policy_id = "live-policy"
            policy_version = "1"

            def propose(self, pi, c, pc):
                return adaptive_outcome

        coordinator, baseline_calls = self._hybrid(Adaptive())
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "completed")
        self.assertEqual(outcome.decision,
                         HybridDecisionPolicy.ADAPTIVE_PROPOSAL)
        # the genuine (bound) strategy reached the hybrid unchanged
        self.assertIs(outcome.result.strategy, strategy)

    def test_excessive_cost_still_falls_back(self):
        strategy = candidate_strategy(
            "expensive", units=1, max_total_cost=99.0)
        adaptive_outcome = self._bound_planner_outcome(strategy)

        class Adaptive:
            policy_id = "live-policy"
            policy_version = "1"

            def propose(self, pi, c, pc):
                return adaptive_outcome

        coordinator, _ = self._hybrid(Adaptive())
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")
        self.assertEqual(outcome.decision,
                         HybridDecisionPolicy.DETERMINISTIC_FALLBACK)

    def test_excessive_work_units_still_fall_back(self):
        strategy = candidate_strategy("wide", units=9)
        adaptive_outcome = self._bound_planner_outcome(strategy)

        class Adaptive:
            policy_id = "live-policy"
            policy_version = "1"

            def propose(self, pi, c, pc):
                return adaptive_outcome

        coordinator, _ = self._hybrid(Adaptive())
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")

    def test_disallowed_mode_still_falls_back(self):
        strategy = candidate_strategy(
            "parallel-mode", units=1, collaboration_mode="parallel")
        adaptive_outcome = self._bound_planner_outcome(strategy)

        class Adaptive:
            policy_id = "live-policy"
            policy_version = "1"

            def propose(self, pi, c, pc):
                return adaptive_outcome

        coordinator, _ = self._hybrid(Adaptive())
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")

    def test_malformed_structured_result_still_falls_back(self):
        runtime = _FakeRuntime("prose instead of json")
        planner = make_planner(runtime)
        with self.assertRaises(AdaptivePlannerError):
            planner.propose(planner_input(),
                            candidates([candidate_entry("cand-a")]), policy())


class SafetyRegressionTests(unittest.TestCase):
    def test_malformed_parsed_result_fails_closed(self):
        runtime = _FakeRuntime('{"ok": true}')
        planner = make_planner(runtime)
        with self.assertRaises(AdaptivePlannerError):
            planner.propose(planner_input(),
                            candidates([candidate_entry("cand-a")]), policy())

    def test_unknown_candidate_rejected(self):
        runtime = _FakeRuntime(structured_text(candidate_id="model-invented"))
        planner = make_planner(runtime)
        with self.assertRaises(AdaptivePlannerError) as ctx:
            planner.propose(
                planner_input(),
                candidates([candidate_entry("cand-a")]), policy())
        self.assertIn("unknown_candidate", str(ctx.exception))

    def test_all_feature_defaults_off_on_fresh_context(self):
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
