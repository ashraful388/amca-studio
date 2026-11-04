"""M1.2 model-backed planner adapter tests.

Focused coverage required by the issued M1.2 work order. All model runtimes
here are fake/stub adapters — no paid or real provider calls exist anywhere.
"""

import json
import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    AdaptivePlannerError,
    AdaptivePlannerUnavailable,
    CandidateSet,
    ContractMetadata,
    ModelBackedAdaptivePlanner,
    ModelCallCancelled,
    ModelCallRequest,
    ModelCallResponse,
    ModelPlannerEvidence,
    PlannerResultStatus,
    PolicyContext,
    Sensitivity,
    invoke_selected_planner,
    select_planner,
)

CREATED = "2026-08-27T00:00:00+00:00"
LOCAL_CANDIDATES = (
    {"candidate_id": "cand-a", "label": "solo fast", "estimated_cost": 0.0,
     "work_unit_count": 1, "risk_class": "low", "required_permissions": ()},
    {"candidate_id": "cand-b", "label": "parallel", "estimated_cost": 0.0,
     "work_unit_count": 2, "risk_class": "low", "required_permissions": ()},
    {"candidate_id": "cand-forbidden", "label": "cloud", "estimated_cost": 9.0,
     "work_unit_count": 3, "risk_class": "high", "required_permissions": ("network",)},
)


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m12",
        producer="m1.2-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def planner_input():
    from amca.intelligence import PlannerInput
    return PlannerInput(
        meta=meta_factory("pin"),
        request_id="req-1",
        project_ref="project://demo",
        goal_ref="goal://demo-1",
        state_version=12,
        eligible_collaboration_modes=("solo", "parallel"),
        budget_remaining=0.0,
        budget_hard_limit=True,
        feature_gates=DEFAULT_GATES.to_dict(),
    )


def candidate_set():
    return CandidateSet(
        meta=meta_factory("cands"),
        request_ref="req-1",
        candidates=LOCAL_CANDIDATES,
    )


def policy_context():
    return PolicyContext(
        meta=meta_factory("policy"),
        policy_id="m1.2-policy",
        policy_version="1",
        evaluated_envelope={
            "max_total_cost": 5.0,
            "max_work_units": 4,
            "allowed_permissions": ("local_search",),
            "max_allowed_risk": "low",
        },
        pinned=True,
    )


def model_payload(recommendations, usage=None, response_version=1):
    payload = {
        "response_version": response_version,
        "policy_id": "m1.2-policy",
        "policy_version": "1",
        "recommendations": recommendations,
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload)


class StubRuntime:
    """Fake model runtime returning a canned structured response."""

    def __init__(self, text=None, behavior="ok"):
        self.behavior = behavior
        self.text = text
        self.requests = []

    def complete(self, request: ModelCallRequest) -> ModelCallResponse:
        self.requests.append(request)
        if self.behavior == "cancelled":
            raise ModelCallCancelled("user cancelled the model call")
        if self.behavior == "timeout":
            raise TimeoutError("model call exceeded deadline")
        if self.behavior == "provider_error":
            raise RuntimeError("runtime socket failure")
        return ModelCallResponse(
            meta=meta_factory("mres"),
            request_ref=request.request_id,
            provider_ref="stub/local",
            resolved_model_id="stub-model:latest",
            text_output=self.text,
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=12.0,
            cost=None,
            local_execution=True,
            failure_category=None,
        )


def adapter(runtime):
    return ModelBackedAdaptivePlanner(
        runtime=runtime,
        meta_factory=meta_factory,
        policy_id="m1.2-policy",
        policy_version="1",
    )


class ValidRecommendationTests(unittest.TestCase):
    def test_valid_model_recommendation_yields_proposed_result(self):
        runtime = StubRuntime(
            model_payload(
                [
                    {"candidate_id": "cand-a", "model_id": "stub-model:latest",
                     "confidence": 0.9},
                    {"candidate_id": "cand-b", "model_id": "stub-model:latest",
                     "confidence": 0.5},
                ],
                usage={"input_tokens": 10, "output_tokens": 5,
                       "duration_ms": 12.0, "estimated_cost": 0.0},
            )
        )
        planner = adapter(runtime)
        result = planner.propose(planner_input(), candidate_set(), policy_context())
        self.assertEqual(result.status, PlannerResultStatus.PROPOSED)
        self.assertEqual(result.strategy.collaboration_mode, "adaptive_candidate")
        self.assertEqual(result.strategy.max_total_cost, 0.0)

    def test_model_selects_allowed_candidate_and_local_constraints_win(self):
        runtime = StubRuntime(
            model_payload(
                [
                    {"candidate_id": "cand-forbidden",
                     "model_id": "stub-model:latest", "confidence": 0.99},
                    {"candidate_id": "cand-a",
                     "model_id": "stub-model:latest", "confidence": 0.4},
                ]
            )
        )
        planner = adapter(runtime)
        result = planner.propose(planner_input(), candidate_set(), policy_context())
        # the locally inadmissible recommendation stays rejected: only cand-a
        # survives the M1.1 hard constraints
        self.assertEqual(result.status, PlannerResultStatus.PROPOSED)
        self.assertEqual(result.strategy.planner_kind, "model_backed_adaptive")
        evidence = planner.last_evidence
        self.assertEqual(evidence.outcome, "proposed")
        self.assertEqual(evidence.recommendation_count, 2)

    def test_identical_responses_yield_deterministic_results(self):
        payload = model_payload(
            [{"candidate_id": "cand-a", "model_id": "stub-model:latest",
              "confidence": 0.7}]
        )
        first = adapter(StubRuntime(payload)).propose(
            planner_input(), candidate_set(), policy_context()
        )
        second = adapter(StubRuntime(payload)).propose(
            planner_input(), candidate_set(), policy_context()
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first.strategy.content_digest(), second.strategy.content_digest()
        )


class FailClosedTests(unittest.TestCase):
    def test_unknown_candidate_rejected(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "ghost", "model_id": "m", "confidence": 0.9}]
            )
        )
        with self.assertRaises(AdaptivePlannerError) as ctx:
            adapter(runtime).propose(planner_input(), candidate_set(), policy_context())
        self.assertIn("unknown_candidate", str(ctx.exception))

    def test_locally_inadmissible_only_recommendation_blocked(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "cand-forbidden", "model_id": "m",
                  "confidence": 0.99}]
            )
        )
        result = adapter(runtime).propose(
            planner_input(), candidate_set(), policy_context()
        )
        self.assertEqual(result.status, PlannerResultStatus.BLOCKED)
        self.assertEqual(
            result.failure.category.name, "NO_FEASIBLE_STRATEGY"
        )
        self.assertTrue(result.failure.deterministic_fallback)

    def test_malformed_model_result_normalized(self):
        for bad_text in (
            "not json at all",
            json.dumps({"response_version": 1}),
            model_payload([], response_version=2),
            model_payload(
                [{"candidate_id": "", "model_id": "m", "confidence": 0.5}]
            ),
            model_payload(
                [{"candidate_id": "cand-a", "model_id": "m",
                  "confidence": float("nan")}]
            ),
            model_payload([], usage={"api_key": "sk-secret"}),
        ):
            with self.subTest(bad_text=bad_text[:40]):
                with self.assertRaises(AdaptivePlannerError) as ctx:
                    adapter(StubRuntime(bad_text)).propose(
                        planner_input(), candidate_set(), policy_context()
                    )
                self.assertIn(
                    ctx.exception.args[0].split(":")[0],
                    {
                        "malformed_model_result",
                        "unknown_response_version",
                    },
                )

    def test_non_finite_confidence_rejected(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "cand-a", "model_id": "m",
                  "confidence": float("inf")}]
            )
        )
        with self.assertRaises(AdaptivePlannerError) as ctx:
            adapter(runtime).propose(planner_input(), candidate_set(), policy_context())
        self.assertIn("malformed_model_result", str(ctx.exception))

    def test_duplicate_recommendations_rejected(self):
        runtime = StubRuntime(
            model_payload(
                [
                    {"candidate_id": "cand-a", "model_id": "m", "confidence": 0.9},
                    {"candidate_id": "cand-a", "model_id": "m", "confidence": 0.8},
                ]
            )
        )
        with self.assertRaises(AdaptivePlannerError) as ctx:
            adapter(runtime).propose(planner_input(), candidate_set(), policy_context())
        self.assertIn("duplicate recommendation", str(ctx.exception))

    def test_provider_runtime_error_normalizes_to_unavailable(self):
        with self.assertRaises(AdaptivePlannerUnavailable) as ctx:
            adapter(StubRuntime(behavior="provider_error")).propose(
                planner_input(), candidate_set(), policy_context()
            )
        self.assertIn("provider_error", str(ctx.exception))

    def test_timeout_normalized(self):
        with self.assertRaises(AdaptivePlannerUnavailable) as ctx:
            adapter(StubRuntime(behavior="timeout")).propose(
                planner_input(), candidate_set(), policy_context()
            )
        self.assertIn("timeout", str(ctx.exception))

    def test_cancellation_normalizes_to_fallback_signal(self):
        with self.assertRaises(AdaptivePlannerError) as ctx:
            adapter(StubRuntime(behavior="cancelled")).propose(
                planner_input(), candidate_set(), policy_context()
            )
        self.assertIn("model_call_cancelled", str(ctx.exception))
        # normalized cancellation leaves no partially authoritative proposal
        planner = adapter(StubRuntime(behavior="cancelled"))
        with self.assertRaises(AdaptivePlannerError):
            planner.propose(planner_input(), candidate_set(), policy_context())
        self.assertEqual(planner.last_evidence.outcome, "cancelled")

    def test_failures_route_through_m1_deterministic_fallback(self):
        runtime = StubRuntime(behavior="provider_error")
        selection = select_planner(
            gates=FeatureGateOn(), meta=meta_factory("sel"), request_ref="req-1"
        )
        from amca.intelligence import ExecutionStrategy, PlannerResult

        class DeterministicStub:
            def propose(self, planner_input):
                return PlannerResult(
                    meta=meta_factory("det"),
                    request_ref="req-1",
                    status=PlannerResultStatus.PROPOSED,
                    strategy=ExecutionStrategy(
                        meta=meta_factory("det-strat"),
                        planner_kind="deterministic",
                        planner_version="v0.4.0",
                        collaboration_mode="solo",
                        synchronization_mode="sequential",
                        work_units=(),
                        max_rounds=1,
                        max_total_cost=0.0,
                        stop_condition="deterministic",
                        fallback_strategy_ref=None,
                    ),
                    alternatives=(),
                    failure=None,
                )

        from amca.intelligence import FeatureGateRegistry
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=DeterministicStub(),
            adaptive=adapter(runtime),
            meta=meta_factory("inv"),
        )
        self.assertTrue(invocation.used_fallback)
        self.assertEqual(invocation.planner_used.value, "deterministic")


def FeatureGateOn():
    from amca.intelligence import FeatureGateRegistry
    return FeatureGateRegistry(adaptive_planner=True)


class EvidenceTests(unittest.TestCase):
    def test_usage_cost_metadata_extracted(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "cand-a", "model_id": "stub-model:latest",
                  "confidence": 0.8}],
                usage={"input_tokens": 10, "output_tokens": 5,
                       "duration_ms": 12.0, "estimated_cost": 0.0},
            )
        )
        planner = adapter(runtime)
        planner.propose(planner_input(), candidate_set(), policy_context())
        evidence = planner.last_evidence
        self.assertIsInstance(evidence, ModelPlannerEvidence)
        self.assertEqual(evidence.model_id, "stub-model:latest")
        self.assertEqual(evidence.input_tokens, 10)
        self.assertEqual(evidence.output_tokens, 5)
        self.assertEqual(evidence.estimated_cost, 0.0)
        self.assertEqual(evidence.policy_id, "m1.2-policy")

    def test_no_sensitive_evidence_fields(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "cand-a", "model_id": "m", "confidence": 0.5}],
                usage={"estimated_cost": 0.0},
            )
        )
        planner = adapter(runtime)
        planner.propose(planner_input(), candidate_set(), policy_context())
        data = planner.last_evidence.to_dict()
        forbidden = {
            "api_key", "authorization", "prompt", "messages",
            "raw_response", "secret", "credential",
        }
        self.assertEqual(set(data) & forbidden, set())


class FeatureGateTests(unittest.TestCase):
    def test_all_four_gates_remain_off(self):
        runtime = StubRuntime(
            model_payload(
                [{"candidate_id": "cand-a", "model_id": "m", "confidence": 0.9}]
            )
        )
        planner = adapter(runtime)
        planner.propose(planner_input(), candidate_set(), policy_context())
        self.assertTrue(planner.gates_remain_off(DEFAULT_GATES))
        self.assertFalse(DEFAULT_GATES.adaptive_planner)


if __name__ == "__main__":
    unittest.main()
