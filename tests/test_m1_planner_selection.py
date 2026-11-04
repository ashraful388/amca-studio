"""M1 Adaptive Planner Foundation tests.

Focused coverage required by the issued M1 work order: the adaptive planner
abstraction layer exists, the planner selection interface behaves, the
deterministic fallback path works, feature gates stay OFF, evidence hooks
capture only whitelisted metadata, and no model calls / v0.4.0 behavior
changes are introduced.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    AdaptivePlannerError,
    AdaptivePlannerUnavailable,
    CandidateSet,
    ContractError,
    ContractErrorCode,
    ExecutionStrategy,
    FeatureGateRegistry,
    PlannerEvaluationStatus,
    PlannerInvocation,
    PlannerKind,
    PlannerResult,
    PlannerResultStatus,
    PlannerSelection,
    PlannerSelectionMode,
    PolicyContext,
    Sensitivity,
    WorkUnit,
    invoke_selected_planner,
    select_planner,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta(record_id="rec-m1", correlation="corr-m1"):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id=correlation,
        producer="m1-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def work_unit(unit_id="unit-1"):
    return WorkUnit(
        meta=meta("wu-" + unit_id),
        unit_id=unit_id,
        depends_on=(),
        focus="summarize sources",
        assigned_agent_ref="agent://researcher",
        verifier_ref="verifier://citation-check",
        max_retries=0,
    )


def deterministic_strategy():
    return PlannerResult(
        meta=meta("res-det"),
        request_ref="req-1",
        status=PlannerResultStatus.PROPOSED,
        strategy=ExecutionStrategy(
            meta=meta("strat-det"),
            planner_kind="deterministic",
            planner_version="v0.4.0",
            collaboration_mode="solo",
            synchronization_mode="sequential",
            work_units=(work_unit(),),
            max_rounds=1,
            max_total_cost=0.0,
            stop_condition="unit verified",
            fallback_strategy_ref=None,
        ),
        alternatives=(),
        failure=None,
    )


class FakeDeterministicPlanner:
    """Test double standing in for the existing deterministic authority."""

    def __init__(self):
        self.calls = 0

    def propose(self, planner_input):
        self.calls += 1
        return deterministic_strategy()


class FakeAdaptivePlanner:
    """Test double implementing the adaptive interface shape."""

    policy_id = "adaptive-policy-test"
    policy_version = "1"

    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.calls = 0

    def propose(self, planner_input, candidates, policy_context):
        self.calls += 1
        if self.behavior == "unavailable":
            raise AdaptivePlannerUnavailable("policy envelope unavailable")
        if self.behavior == "error":
            raise AdaptivePlannerError("ranking failed")
        if self.behavior == "contract_error":
            raise ContractError(ContractErrorCode.INVALID, "bad proposal")
        return deterministic_strategy()


def planner_input():
    from amca.intelligence import PlannerInput
    return PlannerInput(
        meta=meta("pin-1"),
        request_id="req-1",
        project_ref="project://demo",
        goal_ref="goal://demo-1",
        state_version=12,
        eligible_collaboration_modes=("solo",),
        budget_remaining=0.0,
        budget_hard_limit=True,
        feature_gates=DEFAULT_GATES.to_dict(),
    )


class PlannerSelectionTests(unittest.TestCase):
    def test_default_gates_select_deterministic(self):
        selection = select_planner(
            gates=DEFAULT_GATES, meta=meta(), request_ref="req-1"
        )
        self.assertEqual(selection.selected, PlannerKind.DETERMINISTIC)
        self.assertFalse(selection.fallback)
        self.assertFalse(selection.adaptive_gate_enabled)
        self.assertIsNone(selection.fallback_reason)

    def test_adaptive_disabled_selects_deterministic(self):
        registry = FeatureGateRegistry(adaptive_planner=False)
        selection = select_planner(
            gates=registry, meta=meta(), request_ref="req-1"
        )
        self.assertEqual(selection.selected, PlannerKind.DETERMINISTIC)
        self.assertIn("gate disabled", selection.reason)

    def test_explicit_adaptive_request_fails_safely_while_gate_off(self):
        with self.assertRaises(ContractError) as ctx:
            select_planner(
                gates=DEFAULT_GATES,
                meta=meta(),
                request_ref="req-1",
                requested_mode=PlannerSelectionMode.ADAPTIVE,
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_unknown_planner_mode_fails_safely(self):
        with self.assertRaises(ContractError) as ctx:
            select_planner(
                gates=DEFAULT_GATES,
                meta=meta(),
                request_ref="req-1",
                requested_mode="teleport",
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)

    def test_enabled_gate_selects_adaptive_interface_only(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(
            gates=registry, meta=meta(), request_ref="req-1"
        )
        self.assertEqual(selection.selected, PlannerKind.ADAPTIVE)
        self.assertTrue(selection.adaptive_gate_enabled)
        self.assertFalse(selection.fallback)

    def test_identical_inputs_produce_identical_selections(self):
        first = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        second = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        self.assertEqual(first, second)
        self.assertEqual(first.content_digest(), second.content_digest())

    def test_selection_round_trips(self):
        selection = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        restored = PlannerSelection.from_dict(selection.to_dict())
        self.assertEqual(restored, selection)


class DeterministicFallbackTests(unittest.TestCase):
    def test_gate_off_invokes_deterministic_only(self):
        deterministic = FakeDeterministicPlanner()
        adaptive = FakeAdaptivePlanner()
        selection = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=deterministic,
            adaptive=adaptive,
            meta=meta("inv-1"),
        )
        self.assertEqual(invocation.planner_used, PlannerKind.DETERMINISTIC)
        self.assertFalse(invocation.used_fallback)
        self.assertEqual(deterministic.calls, 1)
        self.assertEqual(adaptive.calls, 0)
        self.assertEqual(
            invocation.evidence.evaluation_status,
            PlannerEvaluationStatus.NOT_EVALUATED,
        )

    def test_adaptive_unavailable_falls_back_to_deterministic(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        deterministic = FakeDeterministicPlanner()
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=deterministic,
            adaptive=None,
            meta=meta("inv-2"),
        )
        self.assertTrue(invocation.used_fallback)
        self.assertEqual(invocation.planner_used, PlannerKind.DETERMINISTIC)
        self.assertEqual(
            invocation.evidence.evaluation_status, PlannerEvaluationStatus.FELL_BACK
        )
        self.assertIn("not configured", invocation.evidence.fallback_reason)
        self.assertEqual(deterministic.calls, 1)

    def test_adaptive_error_falls_back_to_deterministic(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        deterministic = FakeDeterministicPlanner()
        adaptive = FakeAdaptivePlanner(behavior="error")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=deterministic,
            adaptive=adaptive,
            meta=meta("inv-3"),
        )
        self.assertTrue(invocation.used_fallback)
        self.assertIn("adaptive planner failed", invocation.evidence.fallback_reason)
        self.assertEqual(deterministic.calls, 1)
        self.assertEqual(adaptive.calls, 1)

    def test_adaptive_unavailable_exception_falls_back(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=FakeDeterministicPlanner(),
            adaptive=FakeAdaptivePlanner(behavior="unavailable"),
            meta=meta("inv-4"),
        )
        self.assertTrue(invocation.used_fallback)
        self.assertIn("unavailable", invocation.evidence.fallback_reason)

    def test_adaptive_contract_error_falls_back(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=FakeDeterministicPlanner(),
            adaptive=FakeAdaptivePlanner(behavior="contract_error"),
            meta=meta("inv-5"),
        )
        self.assertTrue(invocation.used_fallback)

    def test_adaptive_success_is_returned_without_fallback(self):
        registry = FeatureGateRegistry(adaptive_planner=True)
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=FakeDeterministicPlanner(),
            adaptive=FakeAdaptivePlanner(behavior="ok"),
            meta=meta("inv-6"),
        )
        self.assertFalse(invocation.used_fallback)
        self.assertEqual(invocation.planner_used, PlannerKind.ADAPTIVE)
        self.assertEqual(
            invocation.evidence.evaluation_status,
            PlannerEvaluationStatus.EVALUATED,
        )

    def test_deterministic_errors_are_never_masked(self):
        class BrokenDeterministic:
            def propose(self, planner_input):
                raise RuntimeError("deterministic failure")

        selection = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        with self.assertRaises(RuntimeError):
            invoke_selected_planner(
                selection=selection,
                planner_input=planner_input(),
                deterministic=BrokenDeterministic(),
                meta=meta("inv-7"),
            )

    def test_invocation_round_trip(self):
        selection = select_planner(gates=DEFAULT_GATES, meta=meta(), request_ref="req-1")
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=FakeDeterministicPlanner(),
            meta=meta("inv-8"),
        )
        restored = PlannerInvocation.from_dict(invocation.to_dict())
        self.assertEqual(restored, invocation)


class FeatureGateIntegrationTests(unittest.TestCase):
    def test_all_gates_remain_off_after_operations(self):
        registry = DEFAULT_GATES
        selection = select_planner(gates=registry, meta=meta(), request_ref="req-1")
        invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=FakeDeterministicPlanner(),
            meta=meta("inv-9"),
        )
        self.assertEqual(
            registry.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )

    def test_candidate_set_and_policy_context_round_trip(self):
        candidates = CandidateSet(
            meta=meta("cand-1"),
            request_ref="req-1",
            candidates=({"kind": "solo", "eligible": True},),
        )
        context = PolicyContext(
            meta=meta("pol-1"),
            policy_id="adaptive-policy-test",
            policy_version="1",
            evaluated_envelope={"max_cloud_calls": 0},
            pinned=True,
        )
        from amca.intelligence import CandidateSet as CS, PolicyContext as PC
        self.assertEqual(CS.from_dict(candidates.to_dict()), candidates)
        self.assertEqual(PC.from_dict(context.to_dict()), context)


class EvidenceHookTests(unittest.TestCase):
    def test_strategy_metadata_is_whitelisted(self):
        from amca.intelligence import execution_strategy_metadata
        metadata = execution_strategy_metadata(deterministic_strategy())
        self.assertEqual(
            set(metadata),
            {
                "planner_kind",
                "planner_version",
                "collaboration_mode",
                "synchronization_mode",
                "work_unit_count",
                "max_rounds",
                "max_total_cost",
                "stop_condition",
            },
        )

    def test_no_hidden_reasoning_is_captured(self):
        from amca.intelligence import execution_strategy_metadata
        metadata = execution_strategy_metadata(deterministic_strategy())
        forbidden = {"reasoning", "chain_of_thought", "prompt", "model_output"}
        self.assertEqual(set(metadata) & forbidden, set())

    def test_fallback_evidence_requires_reason(self):
        from amca.intelligence import PlannerEvaluationEvidence
        with self.assertRaises(ContractError):
            PlannerEvaluationEvidence(
                meta=meta("ev-m1"),
                request_ref="req-1",
                planner_type_selected="deterministic",
                evaluation_status=PlannerEvaluationStatus.FELL_BACK,
                fallback=True,
                fallback_reason=None,
                strategy_metadata={},
            )

    def test_non_whitelisted_metadata_rejected(self):
        from amca.intelligence import PlannerEvaluationEvidence
        with self.assertRaises(ContractError) as ctx:
            PlannerEvaluationEvidence(
                meta=meta("ev-m1b"),
                request_ref="req-1",
                planner_type_selected="deterministic",
                evaluation_status=PlannerEvaluationStatus.EVALUATED,
                fallback=False,
                fallback_reason=None,
                strategy_metadata={"model_private_output": "secret"},
            )
        self.assertEqual(
            ctx.exception.code, ContractErrorCode.INTEGRITY_VIOLATION
        )


if __name__ == "__main__":
    unittest.main()
