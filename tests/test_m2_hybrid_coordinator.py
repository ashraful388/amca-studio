"""M2 Hybrid Execution Foundation tests.

Focused coverage required by the issued M2 work order: audited state machine
with fail-closed transitions, deterministic-first sessions, bounded adaptive
phase integrated with the M1 selection seam, gate semantics (explicit Hybrid
while OFF refuses), deterministic inspectable decision policy, budget bounds
enforcement (REJECT/FALLBACK), terminal cancellation, and the required
failure/fallback behaviors.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    AdaptivePlannerError,
    AdaptivePlannerUnavailable,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    ExecutionStrategy,
    FeatureGateRegistry,
    HYBRID_TRANSITIONS,
    HybridCoordinator,
    HybridDecisionPolicy,
    HybridExecutionBounds,
    HybridOutcome,
    HybridSessionState,
    PlannerResult,
    PlannerResultStatus,
    Sensitivity,
    validate_hybrid_transition,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m2",
        producer="m2-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def meta(record_id="rec-m2"):
    return meta_factory(record_id)


def bounds(**overrides):
    fields = dict(
        meta=meta("bounds"),
        max_rounds=2,
        max_estimated_cost=5.0,
        max_work_units=4,
        allowed_collaboration_modes=("solo", "parallel"),
        allowed_permissions=(),
        max_allowed_risk="medium",
    )
    fields.update(overrides)
    return HybridExecutionBounds(**fields)


def strategy(planner_kind="adaptive", max_rounds=1, max_total_cost=1.0,
             collaboration_mode="parallel", work_units=()):
    return ExecutionStrategy(
        meta=meta("strat-" + planner_kind),
        planner_kind=planner_kind,
        planner_version="1",
        collaboration_mode=collaboration_mode,
        synchronization_mode="sequential",
        work_units=work_units,
        max_rounds=max_rounds,
        max_total_cost=max_total_cost,
        stop_condition="bounded",
        fallback_strategy_ref=None,
    )


def result(status=PlannerResultStatus.PROPOSED, kind="adaptive", **kwargs):
    if status is PlannerResultStatus.PROPOSED:
        chosen = strategy(planner_kind=kind, **kwargs) if kwargs else strategy(
            planner_kind=kind
        )
        return PlannerResult(
            meta=meta("res-" + kind),
            request_ref="req-1",
            status=status,
            strategy=chosen,
            alternatives=(),
            failure=None,
        )
    from amca.intelligence import PlannerFailure, PlannerFailureCategory
    return PlannerResult(
        meta=meta("res-" + status.value),
        request_ref="req-1",
        status=status,
        strategy=None,
        alternatives=(),
        failure=PlannerFailure(
            meta=meta("fail-" + status.value),
            category=PlannerFailureCategory.NO_FEASIBLE_STRATEGY,
            message="nothing admissible",
            retryable=False,
            deterministic_fallback=True,
        ),
    )


class DeterministicStub:
    def __init__(self, the_result=None):
        self.the_result = the_result or result(kind="deterministic")
        self.calls = 0

    def propose(self, planner_input):
        self.calls += 1
        return self.the_result


class AdaptiveStub:
    def __init__(self, the_result=None, error=None):
        self.the_result = the_result
        self.error = error
        self.calls = 0
        self.policy_id = "m2-policy"
        self.policy_version = "1"

    def propose(self, planner_input, candidates, policy_context):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.the_result


def planner_input():
    from amca.intelligence import PlannerInput
    return PlannerInput(
        meta=meta("pin"),
        request_id="req-1",
        project_ref="project://demo",
        goal_ref="goal://demo-1",
        state_version=12,
        eligible_collaboration_modes=("solo", "parallel"),
        budget_remaining=0.0,
        budget_hard_limit=True,
        feature_gates=DEFAULT_GATES.to_dict(),
    )


def hybrid_gates():
    return FeatureGateRegistry(hybrid_execution=True)


class StateMachineTests(unittest.TestCase):
    def test_happy_path_transitions_are_allowed(self):
        path = [
            HybridSessionState.INITIALIZED,
            HybridSessionState.DETERMINISTIC_BASELINE,
            HybridSessionState.ADAPTIVE_EVALUATION,
            HybridSessionState.DECISION,
            HybridSessionState.COMPLETED,
        ]
        for previous, next_state in zip(path, path[1:]):
            validate_hybrid_transition(previous, next_state)

    def test_invalid_transitions_fail_closed(self):
        invalid = [
            (HybridSessionState.INITIALIZED, HybridSessionState.COMPLETED),
            (HybridSessionState.INITIALIZED, HybridSessionState.ADAPTIVE_EVALUATION),
            (HybridSessionState.DECISION, HybridSessionState.ADAPTIVE_EVALUATION),
            (HybridSessionState.COMPLETED, HybridSessionState.DECISION),
            (HybridSessionState.CANCELLED, HybridSessionState.DECISION),
        ]
        for previous, next_state in invalid:
            with self.assertRaises(ContractError):
                validate_hybrid_transition(previous, next_state)

    def test_transition_map_is_fail_closed_by_default(self):
        for state, allowed in HYBRID_TRANSITIONS.items():
            for other in HybridSessionState:
                if other not in allowed:
                    with self.assertRaises(ContractError):
                        validate_hybrid_transition(state, other)


class GateSemanticsTests(unittest.TestCase):
    def test_explicit_hybrid_while_gate_off_fails_closed(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
        )
        with self.assertRaises(ContractError) as ctx:
            coordinator.run(
                request_ref="req-1",
                planner_input=planner_input(),
                gates=DEFAULT_GATES,
            )
        self.assertIn("hybrid_execution gate", str(ctx.exception))
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)


class DeterministicFirstTests(unittest.TestCase):
    def test_session_without_adaptive_completes_on_baseline(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
        )
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.COMPLETED)
        self.assertEqual(
            outcome.decision, HybridDecisionPolicy.DETERMINISTIC_BASELINE
        )
        self.assertEqual(
            outcome.transitions,
            (
                "initialized",
                "deterministic_baseline",
                "decision",
                "completed",
            ),
        )
        self.assertFalse(outcome.hybrid_gate_enabled is False)

    def test_no_admissible_deterministic_baseline_blocked(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(
                result(status=PlannerResultStatus.BLOCKED)
            ),
            bounds=bounds(),
            adaptive=AdaptiveStub(result()),
        )
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.BLOCKED)
        self.assertIsNone(outcome.decision)
        # adaptive evaluation never ran: deterministic-first ordering held
        self.assertEqual(outcome.transitions[-1], "blocked")


class BoundedAdaptivePhaseTests(unittest.TestCase):
    def _coordinator(self, adaptive):
        return HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
            adaptive=adaptive,
        )

    def test_adaptive_proposal_within_bounds_accepted(self):
        adaptive = AdaptiveStub(result(kind="adaptive"))
        outcome = self._coordinator(adaptive).run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.COMPLETED)
        self.assertEqual(
            outcome.decision, HybridDecisionPolicy.ADAPTIVE_PROPOSAL
        )
        self.assertEqual(outcome.result.strategy.planner_kind, "adaptive")
        self.assertIn("adaptive_evaluation", outcome.transitions)

    def test_adaptive_exceeding_cost_bound_rejected_to_fallback(self):
        adaptive = AdaptiveStub(
            result(kind="adaptive", max_total_cost=50.0)
        )
        outcome = self._coordinator(adaptive).run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertEqual(
            outcome.decision, HybridDecisionPolicy.DETERMINISTIC_FALLBACK
        )
        self.assertIn("cost", outcome.fallback_reason)
        # the deterministic safety baseline is returned unchanged
        self.assertEqual(outcome.result.strategy.planner_kind, "deterministic")

    def test_adaptive_exceeding_rounds_rejected(self):
        adaptive = AdaptiveStub(result(kind="adaptive", max_rounds=9))
        outcome = self._coordinator(adaptive).run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertIn("max_rounds", outcome.fallback_reason)

    def test_adaptive_disallowed_collaboration_mode_rejected(self):
        adaptive = AdaptiveStub(
            result(kind="adaptive", collaboration_mode="swarm")
        )
        outcome = self._coordinator(adaptive).run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertIn("collaboration mode", outcome.fallback_reason)

    def test_adaptive_non_proposed_disposition_rejected(self):
        adaptive = AdaptiveStub(
            result(status=PlannerResultStatus.BLOCKED)
        )
        outcome = self._coordinator(adaptive).run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertIn("disposition", outcome.fallback_reason)


class FailureFallbackTests(unittest.TestCase):
    def _run_with_adaptive_error(self, error):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
            adaptive=AdaptiveStub(error=error),
        )
        return coordinator.run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )

    def test_adaptive_unavailable_falls_back(self):
        outcome = self._run_with_adaptive_error(
            AdaptivePlannerUnavailable("not configured")
        )
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertIn("not configured", outcome.fallback_reason)

    def test_adaptive_error_falls_back(self):
        outcome = self._run_with_adaptive_error(AdaptivePlannerError("bad proposal"))
        self.assertEqual(outcome.state, HybridSessionState.FALLBACK)
        self.assertEqual(
            outcome.result.strategy.planner_kind, "deterministic"
        )

    def test_deterministic_failures_are_never_swallowed(self):
        class Broken:
            def propose(self, planner_input):
                raise RuntimeError("deterministic failure")

        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=Broken(),
            bounds=bounds(),
        )
        with self.assertRaises(RuntimeError):
            coordinator.run(
                request_ref="req-1",
                planner_input=planner_input(),
                gates=hybrid_gates(),
            )


class CancellationTests(unittest.TestCase):
    def test_cancellation_before_adaptive_is_terminal(self):
        adaptive = AdaptiveStub(result())
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
            adaptive=adaptive,
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=hybrid_gates(),
            cancel_check=lambda: True,
        )
        self.assertEqual(outcome.state, HybridSessionState.CANCELLED)
        self.assertIsNone(outcome.decision)
        self.assertEqual(adaptive.calls, 0)
        self.assertEqual(outcome.transitions[-1], "cancelled")

    def test_cancellation_after_adaptive_leaves_no_stale_acceptance(self):
        adaptive = AdaptiveStub(result())
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
            adaptive=adaptive,
        )
        calls = {"count": 0}

        def cancel_after_adaptive():
            calls["count"] += 1
            return calls["count"] > 2  # true only at the post-adaptive check

        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=hybrid_gates(),
            cancel_check=cancel_after_adaptive,
        )
        self.assertEqual(outcome.state, HybridSessionState.CANCELLED)
        self.assertEqual(adaptive.calls, 1)
        # the adaptive proposal was produced but NOT accepted
        self.assertIsNone(outcome.decision)

    def test_cancelled_outcome_round_trips(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=hybrid_gates(),
            cancel_check=lambda: True,
        )
        restored = HybridOutcome.from_dict(outcome.to_dict())
        self.assertEqual(restored, outcome)


class DecisionPolicyTests(unittest.TestCase):
    def test_identical_inputs_produce_identical_decisions(self):
        def run():
            coordinator = HybridCoordinator(
                meta_factory=meta_factory,
                deterministic=DeterministicStub(),
                bounds=bounds(),
                adaptive=AdaptiveStub(result(kind="adaptive")),
            )
            return coordinator.run(
                request_ref="req-1",
                planner_input=planner_input(),
                gates=hybrid_gates(),
            )

        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertEqual(first.content_digest(), second.content_digest())

    def test_outcome_round_trip_completed(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
        )
        outcome = coordinator.run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        restored = HybridOutcome.from_dict(outcome.to_dict())
        self.assertEqual(restored, outcome)


class FeatureGateTests(unittest.TestCase):
    def test_all_gates_remain_off_after_runs(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=bounds(),
            adaptive=AdaptiveStub(result()),
        )
        coordinator.run(
            request_ref="req-1", planner_input=planner_input(), gates=hybrid_gates()
        )
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
