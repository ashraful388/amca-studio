"""M5.0 Intelligence Runtime Integration Seam tests.

Focused coverage required by the issued M5.0 work order: bridge
initialization, all-gates-OFF deterministic equivalence, planner selection
with adaptive OFF (no model-backed invocation), Hybrid/Nano/router paths
never activated while OFF, explicit disabled-feature requests fail closed,
cancellation propagation, deterministic planner error behavior, unchanged
existing model-routing behavior, bridge status snapshot with no sensitive
information, no duplicate execution authority, and the single production
seam attachment in ServiceContext.
"""

import unittest

from amca.intelligence import (
    BRIDGE_OPERATIONS,
    BRIDGE_SEAM_ID,
    BRIDGE_VERSION,
    DEFAULT_GATES,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    IntelligenceRuntimeBridge,
    PlannerResult,
    PlannerResultStatus,
    PlannerSelectionMode,
    Sensitivity,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m50",
        producer="m5.0-test",
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
        eligible_collaboration_modes=("solo",),
        budget_remaining=0.0,
        budget_hard_limit=True,
        feature_gates=DEFAULT_GATES.to_dict(),
    )


def deterministic_strategy_result():
    from amca.intelligence import ExecutionStrategy, WorkUnit
    return PlannerResult(
        meta=meta_factory("res-det"),
        request_ref="req-1",
        status=PlannerResultStatus.PROPOSED,
        strategy=ExecutionStrategy(
            meta=meta_factory("strat-det"),
            planner_kind="deterministic",
            planner_version="v0.4.0",
            collaboration_mode="solo",
            synchronization_mode="sequential",
            work_units=(
                WorkUnit(
                    meta=meta_factory("wu"),
                    unit_id="unit-1",
                    depends_on=(),
                    focus="summarize",
                    assigned_agent_ref="agent://r",
                    verifier_ref="verifier://c",
                    max_retries=0,
                ),
            ),
            max_rounds=1,
            max_total_cost=0.0,
            stop_condition="verified",
            fallback_strategy_ref=None,
        ),
        alternatives=(),
        failure=None,
    )


class DeterministicStub:
    def __init__(self):
        self.calls = 0

    def propose(self, planner_input):
        self.calls += 1
        return deterministic_strategy_result()


class AdaptiveSpy:
    def __init__(self):
        self.calls = 0
        self.policy_id = "spy"
        self.policy_version = "1"

    def propose(self, planner_input, candidates, policy_context):
        self.calls += 1
        raise AssertionError("model-backed planner must not be invoked")


class BridgeInitializationTests(unittest.TestCase):
    def test_bridge_initializes_with_defaults(self):
        bridge = IntelligenceRuntimeBridge()
        self.assertEqual(bridge.SEAM_ID, BRIDGE_SEAM_ID)
        self.assertEqual(bridge.VERSION, BRIDGE_VERSION)
        status = bridge.status()
        self.assertEqual(status["seam_id"], BRIDGE_SEAM_ID)
        self.assertEqual(
            status["gates"],
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )

    def test_single_bounded_operation_surface(self):
        bridge = IntelligenceRuntimeBridge()
        public = {
            name
            for name in dir(bridge)
            if not name.startswith("_")
        }
        # M5.4/M5.4.1 added validate_request, nano_spawn, security_context,
        # and require_mutation_ready to the bridge surface.
        self.assertTrue(
            set(BRIDGE_OPERATIONS).issubset(public),
            f"missing: {set(BRIDGE_OPERATIONS) - public}",
        )

    def test_no_duplicate_execution_authority(self):
        # the bridge must expose no method that executes research runs
        bridge = IntelligenceRuntimeBridge()
        for name in dir(bridge):
            if name.startswith("_"):
                continue
            self.assertFalse(
                name.startswith("execute"),
                f"bridge must not expose execution authority: {name}",
            )
            self.assertFalse(
                name.startswith("run"),
                f"bridge must not expose execution authority: {name}",
            )


class PlannerSeamTests(unittest.TestCase):
    def test_deterministic_planner_selected_with_adaptive_off(self):
        bridge = IntelligenceRuntimeBridge()
        selection = bridge.resolve_planner("req-1")
        self.assertEqual(selection.selected.value, "deterministic")
        self.assertFalse(selection.adaptive_gate_enabled)

    def test_adaptive_planner_not_called_while_off(self):
        bridge = IntelligenceRuntimeBridge()
        adaptive = AdaptiveSpy()
        deterministic = DeterministicStub()
        selection = bridge.resolve_planner("req-1")
        invocation = bridge.invoke_planner(
            selection,
            planner_input(),
            deterministic=deterministic,
            adaptive=adaptive,
        )
        self.assertEqual(adaptive.calls, 0)
        self.assertEqual(deterministic.calls, 1)
        self.assertFalse(invocation.used_fallback)

    def test_deterministic_planner_error_behavior_unchanged(self):
        class Broken:
            def propose(self, planner_input):
                raise RuntimeError("deterministic failure")

        bridge = IntelligenceRuntimeBridge()
        selection = bridge.resolve_planner("req-1")
        with self.assertRaises(RuntimeError):
            bridge.invoke_planner(
                selection,
                planner_input(),
                deterministic=Broken(),
            )

    def test_no_extra_invocation_merely_because_bridge_exists(self):
        bridge = IntelligenceRuntimeBridge()
        deterministic = DeterministicStub()
        # initializing and querying the bridge performs zero planning work
        bridge.status()
        bridge.hybrid_status()
        bridge.nano_context()
        bridge.resolve_model_route()
        self.assertEqual(deterministic.calls, 0)


class HybridSeamTests(unittest.TestCase):
    def test_hybrid_coordinator_not_called_while_off(self):
        bridge = IntelligenceRuntimeBridge()
        factories = []

        def factory():
            factories.append(1)
            raise AssertionError("coordinator must not be constructed")

        self.assertFalse(bridge.hybrid_status()["enabled"])
        with self.assertRaises(ContractError) as ctx:
            bridge.request_hybrid_session(
                coordinator_factory=factory,
                planner_input=planner_input(),
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)
        self.assertEqual(factories, [])

    def test_explicit_disabled_feature_request_fails_closed(self):
        bridge = IntelligenceRuntimeBridge()
        with self.assertRaises(ContractError):
            bridge.resolve_planner(
                "req-1", mode=PlannerSelectionMode.ADAPTIVE
            )
        with self.assertRaises(ContractError):
            bridge.request_hybrid_session(
                coordinator_factory=lambda: None,
                planner_input=planner_input(),
            )


class NanoSeamTests(unittest.TestCase):
    def test_nano_path_not_activated_while_off(self):
        bridge = IntelligenceRuntimeBridge()
        context = bridge.nano_context()
        self.assertFalse(context["enabled"])
        self.assertIsNone(context["registry"])

        factories = []

        def registry_factory():
            factories.append(1)
            raise AssertionError("registry must not be constructed")

        with self.assertRaises(ContractError):
            bridge.nano_spawn(
                proposal=None,
                parent_permissions=("local_search",),
                registry_factory=registry_factory,
            )
        self.assertEqual(factories, [])


class RouterSeamTests(unittest.TestCase):
    def test_existing_model_routing_behavior_unchanged_while_off(self):
        bridge = IntelligenceRuntimeBridge()
        route_calls = []

        outcome = bridge.resolve_model_route(
            candidates=({"candidate_id": "c"},),
            policy=object(),
        )
        self.assertFalse(outcome["routed"])
        self.assertEqual(outcome["path"], "existing_v0_4_model_selection")
        self.assertFalse(outcome["router_invoked"])
        self.assertEqual(route_calls, [])


class CancellationRecoveryTests(unittest.TestCase):
    def test_cancellation_remains_propagated(self):
        from amca.intelligence import (
            HybridCoordinator,
            HybridExecutionBounds,
        )
        bridge = IntelligenceRuntimeBridge(
            gates=FeatureGateRegistry(hybrid_execution=True)
        )
        coordinator = HybridCoordinator(
            meta_factory=meta_factory,
            deterministic=DeterministicStub(),
            bounds=HybridExecutionBounds(
                meta=meta_factory("bounds"),
                max_rounds=1,
                max_estimated_cost=0.0,
                max_work_units=1,
                allowed_collaboration_modes=("solo",),
                allowed_permissions=(),
                max_allowed_risk="low",
            ),
        )
        outcome = bridge.request_hybrid_session(
            coordinator_factory=lambda: coordinator,
            planner_input=planner_input(),
            cancel_check=lambda: True,
        )
        self.assertEqual(outcome.state.value, "cancelled")

    def test_bridge_keeps_no_execution_state(self):
        bridge = IntelligenceRuntimeBridge()
        first = bridge.status()
        second = bridge.status()
        self.assertEqual(first, second)
        # identical repeated dispatches are stable (no hidden context)
        self.assertEqual(
            bridge.resolve_planner("req-1"), bridge.resolve_planner("req-1")
        )


class StatusSnapshotTests(unittest.TestCase):
    def test_status_contains_no_sensitive_information(self):
        bridge = IntelligenceRuntimeBridge()
        data = bridge.status()
        forbidden = {
            "api_key", "authorization", "credential", "secret",
            "password", "token", "prompt", "messages",
        }
        self.assertEqual(set(data) & forbidden, set())
        serialized = repr(data)
        for word in forbidden:
            self.assertNotIn(word, serialized.lower())


class ProductionSeamTests(unittest.TestCase):
    def test_single_seam_attached_in_service_context(self):
        # the ONE existing production file modified is service.py, and the
        # bridge is attached as a passive context attribute
        import inspect
        from amca import service
        source = inspect.getsource(service)
        self.assertIn(
            "IntelligenceRuntimeBridge", source,
            "the integration seam must be attached in service.py",
        )
        # M5.4.1: attachment passes bootstrap_mode
        self.assertGreaterEqual(
            source.count("IntelligenceRuntimeBridge("), 1
        )
        # and nowhere else in the production tree
        import pathlib
        production = pathlib.Path(__file__).resolve().parents[1] / "src" / "amca"
        hits = []
        for path in production.glob("*.py"):
            if path.name in {"service.py"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "IntelligenceRuntimeBridge" in text:
                hits.append(path.name)
        self.assertEqual(hits, [])

    def test_service_context_attaches_bridge(self):
        from amca.service import ServiceContext
        fields = {
            name: field
            for name, field in getattr(
                ServiceContext, "__dataclass_fields__"
            ).items()
        }
        self.assertIn("intelligence_runtime", fields)
        self.assertTrue(fields["intelligence_runtime"].init is False)


class FeatureGateTests(unittest.TestCase):
    def test_all_gates_remain_off_after_bridge_use(self):
        bridge = IntelligenceRuntimeBridge()
        bridge.resolve_planner("req-1")
        bridge.hybrid_status()
        bridge.nano_context()
        bridge.resolve_model_route()
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
