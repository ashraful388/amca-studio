"""M5.3 end-to-end intelligence validation (cross-component integration).

Integration tests required by the M5.3 End-to-End Intelligence Validation
milestone (zcode/amca-v0.5.0-m5-3-end-to-end-intelligence-validation-01).
Unlike the per-milestone suites (M0-M5.2) which test each subsystem in
isolation, these scenarios drive the COMBINED path through the single
integration seam:

    IntelligenceRuntimeBridge -> planner selection (M1)
                              -> model-backed adapter (M1.2)
                              -> proposal engine (M1.1)
                              -> hybrid coordinator (M2)
                              -> nano governance (M3)
                              -> model router (M4)

covering authority containment, budget containment, cancellation, evidence
privacy, Studio/backend state consistency, and determinism across
components. Everything runs on fake/stub planners and runtimes - no
provider, model, or paid call exists. These are integration tests and new
validation evidence only: no production feature changes, no enablement,
all four gates default OFF.
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
    HybridCoordinator,
    HybridDecisionPolicy,
    HybridExecutionBounds,
    IntelligenceRuntimeBridge,
    ModelBackedAdaptivePlanner,
    ModelCallCancelled,
    ModelCallRequest,
    ModelCallResponse,
    PlannerInput,
    PlannerResult,
    PlannerResultStatus,
    PolicyContext,
    RoutingCandidate,
    RoutingOutcomeStatus,
    RoutingPolicy,
    Sensitivity,
    WorkUnit,
    invoke_selected_planner,
    route,
    route_with_fallback,
    select_planner,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m53",
        producer="m5.3-e2e",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def planner_input(request_id="req-1"):
    return PlannerInput(
        meta=meta_factory("pin"),
        request_id=request_id,
        project_ref="project://demo",
        goal_ref="goal://demo-1",
        state_version=12,
        eligible_collaboration_modes=("solo", "parallel"),
        budget_remaining=0.0,
        budget_hard_limit=True,
        feature_gates=DEFAULT_GATES.to_dict(),
    )


def strategy(planner_kind="deterministic", max_total_cost=0.0,
             collaboration_mode="solo"):
    return ExecutionStrategy(
        meta=meta_factory("strat"),
        planner_kind=planner_kind,
        planner_version="v0.4.0",
        collaboration_mode=collaboration_mode,
        synchronization_mode="sequential",
        work_units=(
            WorkUnit(
                meta=meta_factory("wu"),
                unit_id="unit-1",
                depends_on=(),
                focus="summarize sources",
                assigned_agent_ref="agent://r",
                verifier_ref="verifier://c",
                max_retries=0,
            ),
        ),
        max_rounds=1,
        max_total_cost=max_total_cost,
        stop_condition="unit verified",
        fallback_strategy_ref=None,
    )


def result(planner_kind="deterministic", max_total_cost=0.0,
           collaboration_mode="solo"):
    return PlannerResult(
        meta=meta_factory("res"),
        request_ref="req-1",
        status=PlannerResultStatus.PROPOSED,
        strategy=strategy(planner_kind, max_total_cost, collaboration_mode),
        alternatives=(),
        failure=None,
    )


class DeterministicStub:
    def __init__(self):
        self.calls = 0

    def propose(self, planner_input):
        self.calls += 1
        return result()


class FakeModelRuntime:
    """Fake model runtime returning a canned structured recommendation."""

    def __init__(self, payload_text):
        self.payload_text = payload_text
        self.calls = 0

    def complete(self, request: ModelCallRequest) -> ModelCallResponse:
        self.calls += 1
        return ModelCallResponse(
            meta=meta_factory("mres"),
            request_ref=request.request_id,
            provider_ref="stub/local",
            resolved_model_id="stub-model:latest",
            text_output=self.payload_text,
            finish_reason="stop",
            input_tokens=10,
            output_tokens=5,
            latency_ms=5.0,
            cost=None,
            local_execution=True,
            failure_category=None,
        )


def model_backed_adapter(runtime):
    return ModelBackedAdaptivePlanner(
        runtime=runtime,
        meta_factory=meta_factory,
        policy_id="e2e-policy",
        policy_version="1",
    )


def recommend_payload(recommendations, usage=None):
    import json
    payload = {
        "response_version": 1,
        "policy_id": "e2e-policy",
        "policy_version": "1",
        "recommendations": recommendations,
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload)


def hybrid_bridge(coordinator):
    return IntelligenceRuntimeBridge(
        gates=FeatureGateRegistry(hybrid_execution=True),
        meta_factory=meta_factory,
    ), coordinator


class AllGatesOffTests(unittest.TestCase):
    def test_all_gates_off_production_behavior_is_deterministic_only(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        selection = bridge.resolve_planner("req-1")
        self.assertEqual(selection.selected.value, "deterministic")
        self.assertEqual(
            bridge.resolve_model_route()["path"],
            "existing_v0_4_model_selection",
        )
        self.assertFalse(bridge.hybrid_status()["enabled"])
        self.assertFalse(bridge.nano_context()["enabled"])
        self.assertEqual(
            bridge.status()["gates"],
            DEFAULT_GATES.to_dict(),
        )


class AdaptiveE2ETests(unittest.TestCase):
    def _selection_with_adaptive_gate(self):
        return select_planner(
            gates=FeatureGateRegistry(adaptive_planner=True),
            meta=meta_factory("sel"),
            request_ref="req-1",
        )

    def test_adaptive_fake_runtime_success_end_to_end(self):
        runtime = FakeModelRuntime(recommend_payload(
            [{"candidate_id": "cand-a", "model_id": "stub:latest",
              "confidence": 0.9}]
        ))
        adapter = model_backed_adapter(runtime)
        candidates = CandidateSet(
            meta=meta_factory("cands"),
            request_ref="req-1",
            candidates=(
                {"candidate_id": "cand-a", "estimated_cost": 0.0,
                 "work_unit_count": 1, "risk_class": "low",
                 "required_permissions": (), "capabilities": ("chat",)},
            ),
        )
        policy = PolicyContext(
            meta=meta_factory("pol"),
            policy_id="e2e-policy",
            policy_version="1",
            evaluated_envelope={"max_total_cost": 5.0, "max_work_units": 4},
            pinned=True,
        )
        outcome = adapter.propose(planner_input(), candidates, policy)
        self.assertEqual(outcome.status, PlannerResultStatus.PROPOSED)
        self.assertEqual(outcome.strategy.planner_kind, "model_backed_adaptive")
        # no provider/model call: the runtime is a local fake
        self.assertTrue(runtime.calls >= 1)

    def test_adaptive_constraint_rejection_keeps_local_authority(self):
        runtime = FakeModelRuntime(recommend_payload(
            [{"candidate_id": "cand-expensive", "model_id": "stub:latest",
              "confidence": 0.99}]
        ))
        adapter = model_backed_adapter(runtime)
        candidates = CandidateSet(
            meta=meta_factory("cands"),
            request_ref="req-1",
            candidates=(
                {"candidate_id": "cand-expensive", "estimated_cost": 99.0,
                 "work_unit_count": 1, "risk_class": "low",
                 "required_permissions": ()},
            ),
        )
        policy = PolicyContext(
            meta=meta_factory("pol"),
            policy_id="e2e-policy",
            policy_version="1",
            evaluated_envelope={"max_total_cost": 1.0, "max_work_units": 4},
            pinned=True,
        )
        outcome = adapter.propose(planner_input(), candidates, policy)
        # local M1.1 constraints stay authoritative over the model ranking
        self.assertEqual(outcome.status, PlannerResultStatus.BLOCKED)

    def test_adaptive_failure_falls_back_through_the_m1_seam(self):
        class FailingRuntime:
            def complete(self, request):
                raise AdaptivePlannerUnavailable("runtime down")

        adapter = model_backed_adapter(FailingRuntime())
        from amca.intelligence import AdaptivePlannerUnavailable as APU
        selection = self._selection_with_adaptive_gate()
        deterministic = DeterministicStub()
        invocation = invoke_selected_planner(
            selection=selection,
            planner_input=planner_input(),
            deterministic=deterministic,
            adaptive=adapter,
            meta=meta_factory("inv"),
        )
        self.assertTrue(invocation.used_fallback)
        self.assertEqual(deterministic.calls, 1)


class HybridE2ETests(unittest.TestCase):
    def _coordinator(self, adaptive=None):
        return HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("bounds"),
                max_rounds=2,
                max_estimated_cost=5.0,
                max_work_units=4,
                allowed_collaboration_modes=("solo", "parallel"),
                allowed_permissions=(),
                max_allowed_risk="medium",
            ),
            adaptive=adaptive,
        )

    def test_hybrid_accepted_refinement_end_to_end(self):
        coordinator = self._coordinator(
            adaptive=_FakeAdaptive(result("adaptive", max_total_cost=0.5))
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        self.assertEqual(outcome.state.value, "completed")
        self.assertEqual(
            outcome.decision, HybridDecisionPolicy.ADAPTIVE_PROPOSAL
        )

    def test_hybrid_fallback_returns_untouched_baseline(self):
        coordinator = self._coordinator(
            adaptive=_FakeAdaptive(error=AdaptivePlannerError("boom"))
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        self.assertEqual(outcome.state.value, "fallback")
        self.assertEqual(
            outcome.result.strategy.planner_kind, "deterministic"
        )


class _FakeAdaptive:
    def __init__(self, the_result=None, error=None):
        self.the_result = the_result
        self.error = error
        self.policy_id = "e2e"
        self.policy_version = "1"

    def propose(self, planner_input, candidates, policy_context):
        if self.error is not None:
            raise self.error
        return self.the_result


class NanoE2ETests(unittest.TestCase):
    def _registry(self, **kwargs):
        from amca.agent_fabric import ResourceEnvelope
        from amca.intelligence import NanoRunRegistry
        return NanoRunRegistry(
            meta_factory=meta_factory,
            run_ref="run-1",
            envelope=kwargs.pop("envelope", ResourceEnvelope(max_manager_depth=3)),
            allowed_capabilities=("citation_check",),
            **kwargs,
        )

    def _spawn(self, reg, proposal_id, parent="task:root", parent_permissions=("local_search",)):
        from amca.intelligence import NanoSpawnProposal
        return reg.authorize_spawn(
            NanoSpawnProposal(
                meta=meta_factory(proposal_id),
                proposal_id=proposal_id,
                run_ref="run-1",
                parent_task_id=parent,
                parent_depth=0,
                task_purpose="verify claims",
                task_type="verification",
                requested_child_count=1,
                capabilities=("citation_check",),
                permissions=("local_search",),
                expected_cost=0.0,
                correlation_id="corr-m53",
            ),
            parent_permissions=parent_permissions,
            gates=FeatureGateRegistry(nano_agents=True),
        )

    def test_nano_bounded_spawn_end_to_end(self):
        reg = self._registry()
        assignment = self._spawn(reg, "p1")
        self.assertEqual(assignment.depth, 1)
        self.assertEqual(assignment.permissions, ("local_search",))
        # authority only shrank: granted permissions are the intersection
        self.assertTrue(
            set(assignment.permissions).issubset({"local_search"})
        )

    def test_nano_privilege_rejection(self):
        reg = self._registry()
        from amca.intelligence import NanoSpawnProposal
        with self.assertRaises(ContractError) as ctx:
            reg.authorize_spawn(
                NanoSpawnProposal(
                    meta=meta_factory("p-esc"),
                    proposal_id="p-esc",
                    run_ref="run-1",
                    parent_task_id="task:root",
                    parent_depth=0,
                    task_purpose="x",
                    task_type="verification",
                    requested_child_count=1,
                    capabilities=("citation_check",),
                    permissions=("network",),
                    expected_cost=0.0,
                    correlation_id="corr-m53",
                ),
                parent_permissions=("local_search",),
                gates=FeatureGateRegistry(nano_agents=True),
            )
        self.assertIn("not authorized", str(ctx.exception))


class RouterE2ETests(unittest.TestCase):
    def _candidates(self):
        return (
            RoutingCandidate(
                meta=meta_factory("rc1"), candidate_id="local-a",
                provider="local/ollama", model="a", local_execution=True,
                capabilities=("chat",), estimated_cost=0.0, latency_ms=10.0,
                available=True,
            ),
            RoutingCandidate(
                meta=meta_factory("rc2"), candidate_id="local-b",
                provider="local/ollama", model="b", local_execution=True,
                capabilities=("chat",), estimated_cost=0.5, latency_ms=5.0,
                available=True,
            ),
            RoutingCandidate(
                meta=meta_factory("rc3"), candidate_id="api-c",
                provider="api/byok", model="c", local_execution=False,
                capabilities=("chat",), estimated_cost=0.2, latency_ms=1.0,
                available=True,
            ),
        )

    def test_router_authorized_selection_excludes_cloud_policy(self):
        outcome = route(
            meta=meta_factory("pol-x"),
            request_ref="req-1",
            candidates=self._candidates(),
            policy=RoutingPolicy(
                meta=meta_factory("pol"),
                allowed_providers=("local/ollama",),
                required_capabilities=("chat",),
                local_only=True,
                cloud_disabled=True,
                max_budget=1.0,
                strict_unknown_cost=True,
                maximum=3,
            ),
            gates=FeatureGateRegistry(model_router=True),
        )
        self.assertEqual(outcome.status, RoutingOutcomeStatus.ROUTED)
        self.assertEqual(outcome.selected.provider, "local/ollama")

    def test_router_bounded_fallback_and_blocked(self):
        strict = RoutingPolicy(
            meta=meta_factory("p1"), allowed_providers=("locked/",),
            required_capabilities=("chat",), local_only=False,
            cloud_disabled=False, max_budget=0.0, strict_unknown_cost=False,
            maximum=1,
        )
        relaxed = RoutingPolicy(
            meta=meta_factory("p2"), allowed_providers=("local/ollama",),
            required_capabilities=("chat",), local_only=True,
            cloud_disabled=True, max_budget=1.0, strict_unknown_cost=False,
            maximum=1,
        )
        outcome, index = route_with_fallback(
            meta=meta_factory("pol-x"),
            request_ref="req-1",
            candidates=self._candidates(),
            policies=(strict, relaxed),
            gates=FeatureGateRegistry(model_router=True),
        )
        self.assertEqual(outcome.status, RoutingOutcomeStatus.ROUTED)
        self.assertEqual(index, 1)

        blocked, index = route_with_fallback(
            meta=meta_factory("pol-x"),
            request_ref="req-1",
            candidates=(),
            policies=(strict, relaxed),
            gates=FeatureGateRegistry(model_router=True),
        )
        self.assertEqual(blocked.status, RoutingOutcomeStatus.BLOCKED)


class CrossComponentCeilingTests(unittest.TestCase):
    def test_cross_component_authority_ceiling(self):
        # adaptive proposes above the hybrid authority bound -> REJECT/FALLBACK
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("b"),
                max_rounds=1,
                max_estimated_cost=0.5,
                max_work_units=1,
                allowed_collaboration_modes=("solo",),
                allowed_permissions=(),
                max_allowed_risk="low",
            ),
            adaptive=_FakeAdaptive(result("adaptive", max_total_cost=9.0,
                                          collaboration_mode="swarm")),
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        self.assertEqual(outcome.state.value, "fallback")
        self.assertIn("rejected", outcome.fallback_reason)

    def test_cross_component_budget_ceiling(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("b"),
                max_rounds=1,
                max_estimated_cost=0.1,
                max_work_units=1,
                allowed_collaboration_modes=("solo",),
                allowed_permissions=(),
                max_allowed_risk="low",
            ),
            adaptive=_FakeAdaptive(result("adaptive", max_total_cost=7.5)),
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        self.assertEqual(outcome.state.value, "fallback")
        self.assertIn("cost", outcome.fallback_reason)


class CancellationPrivacyRepeatabilityTests(unittest.TestCase):
    def test_end_to_end_cancellation_is_terminal(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("b"), max_rounds=1, max_estimated_cost=0.0,
                max_work_units=1, allowed_collaboration_modes=("solo",),
                allowed_permissions=(), max_allowed_risk="low",
            ),
            adaptive=_FakeAdaptive(result("adaptive")),
        )
        outcome = coordinator.run(
            request_ref="req-1",
            planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
            cancel_check=lambda: True,
        )
        self.assertEqual(outcome.state.value, "cancelled")
        self.assertIsNone(outcome.decision)

    def test_deterministic_repeatability_across_components(self):
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("b"), max_rounds=1, max_estimated_cost=0.0,
                max_work_units=1, allowed_collaboration_modes=("solo",),
                allowed_permissions=(), max_allowed_risk="low",
            ),
            adaptive=_FakeAdaptive(result("adaptive")),
        )
        first = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        second = coordinator.run(
            request_ref="req-1", planner_input=planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True),
        )
        # authoritative decisions reproduce exactly; only timing-like
        # evidence fields are absent from these records anyway
        self.assertEqual(first.state, second.state)
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(
            first.result.strategy.planner_kind,
            second.result.strategy.planner_kind,
        )

    def test_evidence_privacy_across_components(self):
        import json
        runtime = FakeModelRuntime(recommend_payload(
            [{"candidate_id": "cand-a", "model_id": "stub:latest",
              "confidence": 0.9}],
            usage={"input_tokens": 1, "output_tokens": 1},
        ))
        adapter = model_backed_adapter(runtime)
        outcome = adapter.propose(
            planner_input(),
            CandidateSet(
                meta=meta_factory("c"), request_ref="req-1",
                candidates=({"candidate_id": "cand-a"},),
            ),
            PolicyContext(
                meta=meta_factory("pc"), policy_id="e2e",
                policy_version="1", evaluated_envelope={}, pinned=False,
            ),
        )
        evidence = adapter.last_evidence.to_dict()
        serialized = json.dumps(evidence).lower()
        for word in ("api_key", "authorization", "prompt", "secret",
                     "password", "chain_of_thought"):
            self.assertNotIn(word, serialized)

    def test_studio_backend_state_consistency(self):
        # Studio reads the bridge; the bridge reads the same gate registry
        # the validators use - the states must agree exactly
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        snapshot = bridge.status()
        for name, entry in snapshot["capabilities"].items():
            gate_name = {
                "deterministic": None,
                "adaptive_planner": "adaptive_planner",
                "hybrid_execution": "hybrid_execution",
                "nano_agents": "nano_agents",
                "model_router": "model_router",
            }.get(name)
            if gate_name is None:
                continue
            self.assertEqual(
                entry["enabled"],
                DEFAULT_GATES.is_enabled(gate_name),
            )

    def test_no_mutation_path_exists_on_the_bridge(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        for name in dir(bridge):
            if name.startswith("_"):
                continue
            self.assertNotIn(
                "enable", name.lower(), "no enable/mutation op may exist"
            )
            self.assertNotIn(
                "set_gate", name.lower(), "no gate mutation op may exist"
            )


if __name__ == "__main__":
    unittest.main()
