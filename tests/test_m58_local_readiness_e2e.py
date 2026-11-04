"""M5.8 live local intelligence readiness E2E tests.

Required coverage (manager work order
ZC-AMCA-V0.5.0-M5.8-LIVE-LOCAL-INTELLIGENCE-READINESS-E2E-01): all 22
required scenarios. Automated tests use deterministic fakes for the local
runtime; the truthful attempt against the REAL loopback Ollama endpoint is
asserted separately and reported in the validation evidence document.
"""

import asyncio
import json
import unittest

from amca.agent_runtime import AgentTaskStatus, NanoScheduler
from amca.intelligence import (
    DEFAULT_GATES,
    AdaptivePlannerError,
    CandidateSet,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    HybridCoordinator,
    HybridDecisionPolicy,
    HybridExecutionBounds,
    IntelligenceRuntimeBridge,
    LocalModelPlannerRuntime,
    LocalReadinessService,
    ModelBackedAdaptivePlanner,
    ModelCallRequest,
    ModelCallResponse,
    PlannerInput,
    PlannerResult,
    PlannerResultStatus,
    PolicyContext,
    RoutingCandidate,
    RoutingPolicy,
    Sensitivity,
    WorkUnit,
    ExecutionStrategy,
    route_with_fallback,
)
from amca.intelligence.local_readiness import (
    NOT_READY,
    READY,
    REASON_NO_MODEL,
    REASON_STRUCTURED_UNVERIFIED,
    REASON_UNREACHABLE,
    UNKNOWN,
)
from amca.local_runtime import LocalModelRecord
from amca.models.base import (
    ModelAdapter,
    ModelCapabilities,
    ModelResponse,
    TokenUsage,
)
from amca.models.ollama import OllamaAdapter

CREATED = "2026-08-27T00:00:00+00:00"
PREFERRED = "qwen3:1.7b"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m58",
        producer="m5.8-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def local_record(name):
    return LocalModelRecord(
        provider="ollama",
        model=name,
        digest=f"digest-{name}",
        size_bytes=1024,
        modified_at=None,
        family="qwen",
        parameter_size="1.7b",
        quantization_level="q4",
        model_format="gguf",
        observed_at=CREATED,
    )


def inventory_fn(models=()):
    def _read(base_url, timeout=2.0):
        return tuple(local_record(name) for name in models)
    return _read


def failing_inventory(base_url, timeout=2.0):
    raise OSError("connection refused (fake)")


class _StructuredAdapter(ModelAdapter):
    """Real local adapter returning a canned (or malformed) output."""

    def __init__(self, text='{"ok": true}', model="stub:latest", delay=0.0):
        super().__init__(
            adapter_id="local-stub", provider="stub", model=model,
            capabilities=ModelCapabilities(is_local=True),
        )
        self._text = text
        self._model = model
        self._delay = delay
        self.cancel_invocations = 0

    async def generate(self, request):
        async with self._call_scope(request):
            if self._delay:
                await asyncio.sleep(self._delay)
            return ModelResponse(
                text=self._text, model=self._model, provider="stub",
                usage=TokenUsage(), finish_reason="stop",
            )

    async def cancel(self, request_id=None):
        self.cancel_invocations += 1
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


def ready_service(**kwargs):
    service = LocalReadinessService(
        inventory_fn=inventory_fn((PREFERRED, "llama3:8b")),
        preferred_model=PREFERRED,
        meta_factory=meta_factory,
        **kwargs,
    )
    return service


def attach(bridge, service):
    bridge.attach_local_readiness(service)
    return service


class ReadinessDerivationTests(unittest.TestCase):
    def test_local_runtime_ready(self):
        service = ready_service()
        report = service.refresh(
            structured_probe=True,
            adapter_factory=lambda model: _StructuredAdapter(model=model),
        )
        self.assertTrue(report.runtime_reachable)
        self.assertTrue(report.live)
        self.assertIn(PREFERRED, report.models_detected)
        self.assertEqual(report.selected_model, PREFERRED)
        for capability in ("adaptive_planner", "model_router",
                           "hybrid_execution", "nano_agents",
                           "deterministic"):
            self.assertEqual(report.readiness[capability], READY)

    def test_runtime_unreachable(self):
        service = LocalReadinessService(
            inventory_fn=failing_inventory, meta_factory=meta_factory)
        report = service.refresh()
        self.assertFalse(report.runtime_reachable)
        self.assertFalse(report.live)
        for capability in ("adaptive_planner", "model_router",
                           "hybrid_execution"):
            self.assertEqual(report.readiness[capability], NOT_READY)
            self.assertEqual(
                report.reasons[capability], REASON_UNREACHABLE)

    def test_no_eligible_model(self):
        service = LocalReadinessService(
            inventory_fn=inventory_fn(()), meta_factory=meta_factory)
        report = service.refresh()
        self.assertTrue(report.runtime_reachable)
        self.assertEqual(report.readiness["model_router"], NOT_READY)
        self.assertEqual(report.reasons["model_router"], REASON_NO_MODEL)

    def test_router_readiness_depends_on_inventory(self):
        with_inventory = ready_service().refresh()
        self.assertEqual(with_inventory.readiness["model_router"], READY)
        without = LocalReadinessService(
            inventory_fn=inventory_fn(()), meta_factory=meta_factory
        ).refresh()
        self.assertEqual(without.readiness["model_router"], NOT_READY)

    def test_adaptive_readiness_states(self):
        # unverified (no structured probe): unknown
        unverified = ready_service().refresh()
        self.assertEqual(unverified.readiness["adaptive_planner"], UNKNOWN)
        self.assertEqual(
            unverified.reasons["adaptive_planner"],
            REASON_STRUCTURED_UNVERIFIED,
        )
        # verified through the existing structured path: ready
        verified = ready_service().refresh(
            structured_probe=True,
            adapter_factory=lambda model: _StructuredAdapter(model=model),
        )
        self.assertEqual(verified.readiness["adaptive_planner"], READY)
        # structured probe ran but output was malformed: not_ready
        malformed = ready_service().refresh(
            structured_probe=True,
            adapter_factory=lambda model: _StructuredAdapter(text="not json"),
        )
        self.assertEqual(malformed.readiness["adaptive_planner"], NOT_READY)
        self.assertEqual(
            malformed.reasons["adaptive_planner"],
            REASON_STRUCTURED_UNVERIFIED,
        )

    def test_hybrid_readiness_depends_on_adaptive(self):
        ready = ready_service().refresh(
            structured_probe=True,
            adapter_factory=lambda model: _StructuredAdapter(model=model),
        )
        self.assertEqual(ready.readiness["hybrid_execution"], READY)
        blocked = LocalReadinessService(
            inventory_fn=failing_inventory, meta_factory=meta_factory
        ).refresh()
        self.assertEqual(blocked.readiness["hybrid_execution"], NOT_READY)

    def test_nano_remains_ready(self):
        for service in (
            ready_service(),
            LocalReadinessService(
                inventory_fn=failing_inventory, meta_factory=meta_factory),
            LocalReadinessService(
                inventory_fn=inventory_fn(()), meta_factory=meta_factory),
        ):
            report = service.refresh()
            self.assertEqual(report.readiness["nano_agents"], READY)

    def test_structured_probe_uses_bounded_reasoning_budget(self):
        """M5.9 live-validation regression: reasoning models (qwen3) need
        visible-token budget for <think> before the structured answer; the
        bounded probe must send its dedicated budget/timeout, not the
        64-token/2s values that made the live probe fail with
        structured_output_unverified."""

        captured = {}

        class _CapturingAdapter(_StructuredAdapter):
            async def generate(self, request):
                captured["max_output_tokens"] = request.max_output_tokens
                captured["timeout_seconds"] = request.timeout_seconds
                return await super().generate(request)

        service = ready_service()
        report = service.refresh(
            structured_probe=True,
            adapter_factory=lambda model: _CapturingAdapter(model=model),
        )
        self.assertEqual(report.readiness["adaptive_planner"], READY)
        self.assertEqual(captured["max_output_tokens"], 512)
        self.assertEqual(captured["timeout_seconds"], 90.0)


def _ready_bridge():
    bridge = IntelligenceRuntimeBridge(
        meta_factory=meta_factory, bootstrap_mode="tauri_injected")
    attach(bridge, ready_service())
    bridge.refresh_local_readiness(
        structured_probe=True,
        adapter_factory=lambda model: _StructuredAdapter(model=model),
    )
    return bridge





class SecureEnablementTests(unittest.TestCase):
    def _ready_bridge(self):
        return _ready_bridge()

    def test_readiness_does_not_enable_anything_automatically(self):
        bridge = self._ready_bridge()
        self.assertEqual(
            bridge.status()["gates"], DEFAULT_GATES.to_dict())
        self.assertFalse(bridge.status()["capabilities"]["model_router"]["enabled"])

    def test_status_reports_truthful_readiness_with_reasons(self):
        bridge = self._ready_bridge()
        capabilities = bridge.status()["capabilities"]
        self.assertEqual(
            capabilities["adaptive_planner"]["runtime_ready"], READY)
        self.assertEqual(
            capabilities["adaptive_planner"]["readiness_reason"], "ready")
        self.assertEqual(capabilities["nano_agents"]["runtime_ready"], READY)

    def test_secure_router_enable_after_readiness(self):
        bridge = self._ready_bridge()
        result = bridge.configure_gates(
            {"model_router": True}, contract_version=1,
            correlation_id="m58-router")
        self.assertTrue(result["gates"]["model_router"])

    def test_secure_adaptive_enable_after_readiness(self):
        bridge = self._ready_bridge()
        result = bridge.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="m58-adaptive")
        self.assertTrue(result["gates"]["adaptive_planner"])

    def test_secure_hybrid_dependency_order(self):
        bridge = self._ready_bridge()
        with self.assertRaises(ContractError) as ctx:
            bridge.configure_gates(
                {"hybrid_execution": True}, contract_version=1,
                correlation_id="m58-h1")
        self.assertEqual(
            ctx.exception.details["category"], "dependency_not_enabled")
        bridge.configure_gates(
            {"adaptive_planner": True}, contract_version=1,
            correlation_id="m58-h2")
        result = bridge.configure_gates(
            {"hybrid_execution": True}, contract_version=1,
            correlation_id="m58-h3")
        self.assertTrue(result["gates"]["hybrid_execution"])

    def test_full_enable_sequence(self):
        bridge = self._ready_bridge()
        for changes in (
            {"model_router": True},
            {"adaptive_planner": True},
            {"hybrid_execution": True},
            {"nano_agents": True},
        ):
            bridge.configure_gates(changes, contract_version=1,
                                   correlation_id="m58-seq")
        gates = bridge.status()["gates"]
        self.assertEqual(
            gates,
            {
                "adaptive_planner": True,
                "hybrid_execution": True,
                "nano_agents": True,
                "model_router": True,
            },
        )

    def test_disable_all_restores_off(self):
        bridge = self._ready_bridge()
        bridge.configure_gates(
            {"model_router": True}, contract_version=1, correlation_id="d0")
        result = bridge.disable_all_experimental(correlation_id="d1")
        self.assertEqual(
            result["gates"],
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )

    def test_unreachable_readiness_blocks_enablement(self):
        bridge = IntelligenceRuntimeBridge(
            meta_factory=meta_factory, bootstrap_mode="tauri_injected")
        attach(bridge, LocalReadinessService(
            inventory_fn=failing_inventory, meta_factory=meta_factory))
        bridge.refresh_local_readiness()
        with self.assertRaises(ContractError) as ctx:
            bridge.configure_gates(
                {"adaptive_planner": True}, contract_version=1,
                correlation_id="blk")
        self.assertEqual(
            ctx.exception.details["category"], "runtime_not_ready")


def _planner_input(request_id="req-1"):
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


def _strategy(planner_kind="deterministic"):
    return ExecutionStrategy(
        meta=meta_factory("strat"),
        planner_kind=planner_kind,
        planner_version="v0.4.0",
        collaboration_mode="solo",
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
        max_total_cost=0.0,
        stop_condition="unit verified",
        fallback_strategy_ref=None,
    )


def _result(planner_kind="deterministic"):
    return PlannerResult(
        meta=meta_factory("res"),
        request_ref="req-1",
        status=PlannerResultStatus.PROPOSED,
        strategy=_strategy(planner_kind),
        alternatives=(),
        failure=None,
    )


class _FakeModelRuntime:
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


def _recommend_payload(recommendations):
    return json.dumps({
        "response_version": 1,
        "policy_id": "e2e-policy",
        "policy_version": "1",
        "recommendations": recommendations,
    })


def _adaptive_adapter(runtime):
    return ModelBackedAdaptivePlanner(
        runtime=runtime,
        meta_factory=meta_factory,
        policy_id="e2e-policy",
        policy_version="1",
    )


def _candidates():
    return CandidateSet(
        meta=meta_factory("cands"),
        request_ref="req-1",
        candidates=(
            {"candidate_id": "cand-a", "estimated_cost": 0.0,
             "work_unit_count": 1, "risk_class": "low",
             "required_permissions": (), "capabilities": ("chat",)},
            {"candidate_id": "cand-b", "estimated_cost": 0.0,
             "work_unit_count": 2, "risk_class": "low",
             "required_permissions": (), "capabilities": ("chat",)},
        ),
    )


def _policy():
    return PolicyContext(
        meta=meta_factory("pol"),
        policy_id="e2e-policy",
        policy_version="1",
        evaluated_envelope={"max_total_cost": 5.0, "max_work_units": 4},
        pinned=True,
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


class _DeterministicStub:
    def __init__(self):
        self.calls = 0

    def propose(self, planner_input):
        self.calls += 1
        return _result()


def _hybrid_coordinator(adaptive):
    return HybridCoordinator(
        meta_factory=meta_factory,
        deterministic=_DeterministicStub(),
        bounds=HybridExecutionBounds(
            meta=meta_factory("b"), max_rounds=1, max_estimated_cost=0.0,
            max_work_units=4, allowed_collaboration_modes=("solo",),
            allowed_permissions=(), max_allowed_risk="medium",
        ),
        adaptive=adaptive,
    )


def _routing_candidate(candidate_id, model, provider="local/ollama"):
    return RoutingCandidate(
        meta=meta_factory("c-" + candidate_id),
        candidate_id=candidate_id,
        provider=provider,
        model=model,
        local_execution=True,
        capabilities=("chat",),
        estimated_cost=0.0,
        latency_ms=100.0,
        available=True,
    )


def _routing_policy():
    return RoutingPolicy(
        meta=meta_factory("policy"),
        allowed_providers=("local/ollama",),
        required_capabilities=("chat",),
        local_only=True,
        cloud_disabled=True,
        max_budget=1.0,
        strict_unknown_cost=False,
        maximum=3,
    )


class LocalIntelligenceSmokeTests(unittest.TestCase):
    """Sections 8-11/13-14: the secured local path with deterministic fakes."""

    def _enabled_bridge(self):
        bridge = IntelligenceRuntimeBridge(
            gates=FeatureGateRegistry(
                adaptive_planner=True, hybrid_execution=True,
                nano_agents=True, model_router=True,
            ),
            meta_factory=meta_factory,
            bootstrap_mode="tauri_injected",
        )
        return bridge

    def test_adaptive_structured_success(self):
        runtime = _FakeModelRuntime(_recommend_payload(
            [{"candidate_id": "cand-a", "model_id": "stub-model:latest",
              "confidence": 0.9}]))
        outcome = _adaptive_adapter(runtime).propose(
            _planner_input(), _candidates(), _policy())
        self.assertEqual(outcome.status, PlannerResultStatus.PROPOSED)
        self.assertEqual(outcome.strategy.planner_kind, "model_backed_adaptive")
        self.assertTrue(runtime.calls >= 1)

    def test_malformed_local_output_falls_back(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        # adaptive gate OFF at the bridge: the deterministic planner is
        # selected and malformed local output is never even consulted
        selection = bridge.resolve_planner("req-1")
        self.assertEqual(selection.selected.value, "deterministic")
        # with the adaptive gate on, malformed local output fails closed
        # (AdaptivePlannerError -> deterministic fallback at the seam)
        adapter = _adaptive_adapter(_FakeModelRuntime("not json"))
        with self.assertRaises(AdaptivePlannerError):
            adapter.propose(_planner_input(), _candidates(), _policy())
        from amca.intelligence import select_planner
        deterministic = _DeterministicStub()
        selection = select_planner(
            gates=FeatureGateRegistry(adaptive_planner=True),
            meta=meta_factory("sel"),
            request_ref="req-1",
        )
        invocation = bridge.invoke_planner(
            selection,
            _planner_input(),
            deterministic=deterministic,
            adaptive=adapter,
        )
        self.assertTrue(invocation.used_fallback)
        self.assertEqual(deterministic.calls, 1)

    def test_router_selection(self):
        bridge = self._enabled_bridge()
        result = bridge.resolve_model_route(
            request_ref="req-route",
            candidates=(_routing_candidate("c1", "qwen3:1.7b"),
                        _routing_candidate("c2", "llama3:8b")),
            policy=_routing_policy(),
        )
        self.assertTrue(result["routed"])
        outcome = result["outcome"]
        self.assertEqual(len(outcome.ranked), 2)
        self.assertIsNotNone(outcome.selected)
        first = bridge.resolve_model_route(
            request_ref="req-route",
            candidates=(_routing_candidate("c1", "qwen3:1.7b"),
                        _routing_candidate("c2", "llama3:8b")),
            policy=_routing_policy(),
        )
        # deterministic, policy-bounded selection reproduces exactly
        self.assertEqual(
            outcome.selected.candidate_id,
            first["outcome"].selected.candidate_id,
        )
        self.assertEqual(
            [c.candidate_id for c in outcome.ranked],
            [c.candidate_id for c in first["outcome"].ranked],
        )

    def test_router_yields_blocked_without_eligible_candidate(self):
        bridge = self._enabled_bridge()
        result = bridge.resolve_model_route(
            request_ref="req-none",
            candidates=(_routing_candidate(
                "cloud-only", "paid-model:latest", provider="api/paid"),),
            policy=_routing_policy(),
        )
        # none of the candidates is local/ollama: nothing is routed
        self.assertFalse(result["routed"])

    def test_hybrid_accepted_proposal(self):
        outcome = _hybrid_coordinator(
            _FakeAdaptive(_result("adaptive"))).run(
            request_ref="req-1", planner_input=_planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "completed")
        self.assertEqual(
            outcome.decision, HybridDecisionPolicy.ADAPTIVE_PROPOSAL)

    def test_hybrid_fallback(self):
        outcome = _hybrid_coordinator(
            _FakeAdaptive(error=AdaptivePlannerError("local model failed"))
        ).run(
            request_ref="req-1", planner_input=_planner_input(),
            gates=FeatureGateRegistry(hybrid_execution=True))
        self.assertEqual(outcome.state.value, "fallback")
        self.assertEqual(outcome.result.strategy.planner_kind, "deterministic")

    def test_nano_execution_while_other_features_enabled(self):
        bridge = self._enabled_bridge()
        from amca.intelligence import NanoExecutionCoordinator, NanoRunRegistry, NanoSpawnProposal

        reg = NanoRunRegistry(
            meta_factory=meta_factory, run_ref="run-m58",
            allowed_capabilities=("citation_check",),
        )
        coordinator = NanoExecutionCoordinator(
            registry=reg,
            scheduler=NanoScheduler({}, budget_usd=5.0),
            meta_factory=meta_factory,
            known_parent_task_ids=("task:root",),
        )
        assignment = reg.authorize_spawn(
            NanoSpawnProposal(
                meta=meta_factory("p"),
                proposal_id="p-m58",
                run_ref="run-m58",
                parent_task_id="task:root",
                parent_depth=0,
                task_purpose="verify citation claims",
                task_type="verification",
                requested_child_count=1,
                capabilities=("citation_check",),
                permissions=("local_search",),
                expected_cost=0.0,
                correlation_id="corr-m58",
            ),
            parent_permissions=("local_search",),
            gates=bridge._gates,
        )
        outcome = coordinator.execute([assignment], gates=bridge._gates)
        self.assertEqual(
            outcome["results"][assignment.nano_id].disposition.value,
            "complete",
        )

    def test_cancellation_through_local_runtime(self):
        adapter = _StructuredAdapter(delay=5.0)
        runtime = LocalModelPlannerRuntime(
            adapter=adapter, timeout_seconds=5.0,
            cancel_poll_seconds=0.005, meta_factory=meta_factory,
        )
        request = ModelCallRequest(
            meta=meta_factory("cancel"),
            request_id="req-cancel",
            provider_ref="local/ollama",
            system_instruction=None,
            messages=("bounded task",),
            temperature=0.0,
            max_output_tokens=32,
            timeout_seconds=5.0,
            idempotency_key="cancel",
            privacy_mode="offline",
            approval_ref=None,
        )
        start = asyncio.get_event_loop().time() if False else None
        import time
        began = time.monotonic()

        def cancel_after_start():
            return (time.monotonic() - began) >= 0.05

        from amca.intelligence.local_runtime import ModelCallCancelled
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(request, cancel_check=cancel_after_start)
        self.assertGreaterEqual(adapter.cancel_invocations, 1)

    def test_no_cloud_call(self):
        # the readiness service only ever targets the loopback Ollama API
        import inspect
        from amca.intelligence import local_readiness
        source = inspect.getsource(local_readiness).lower()
        for fragment in ("openai", "anthropic", "gemini", "api.glm", "kimi",
                         "bedrock", "requests.get", "httpx", "urllib.request"):
            self.assertNotIn(fragment, source)
        service = LocalReadinessService(meta_factory=meta_factory)
        self.assertTrue(service._base_url.startswith("http://127.0.0.1"))
        # the existing Ollama adapter is loopback-bound by construction
        adapter = OllamaAdapter(model=PREFERRED, adapter_id="probe")
        self.assertEqual(adapter.model, PREFERRED)

    def test_session_restart_returns_all_off(self):
        bridge = _ready_bridge()
        bridge.configure_gates(
            {"nano_agents": True}, contract_version=1, correlation_id="r1")
        fresh = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        self.assertEqual(fresh.status()["gates"], DEFAULT_GATES.to_dict())
        self.assertEqual(fresh.gate_snapshot()["snapshot_version"], 0)



    def test_service_context_startup_probe_verifies_structure(self):
        """M5.9 regression: the hardened (tauri_injected) desktop session
        runs the bounded structured probe at startup so Adaptive/Hybrid
        readiness is truthfully verifiable; plain dev/test contexts stay
        inventory-only (deterministic, no generation)."""

        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        import amca.service as service_module
        import amca.intelligence.local_readiness as readiness_module

        original = readiness_module.LocalReadinessService

        class _Spy(original):
            # M5.9 isolation fix: the injected startup probe runs on a
            # bounded-retry background thread (15s/45s retry delays, up to
            # the 90s structured-probe timeout) that can outlive both
            # probe.join(45) and ServiceContext.close(). Recording every
            # instance into one shared dict let a previous subtest's
            # still-running probe thread write structured_probe=True into
            # the *next* subtest and fail the non-injected assertion
            # spuriously (SUBFAILED(injected=False)). Recording per
            # instance keeps the guard exact: only the readiness service
            # owned by the context under test can satisfy or fail it.
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.refresh_kwargs = {}

            def refresh(self, **kwargs):
                self.refresh_kwargs.update(kwargs)
                return super().refresh(**kwargs)

        for injected, expected in ((True, True), (False, None)):
            with self.subTest(injected=injected):
                observed = {}
                temporary = tempfile.TemporaryDirectory(
                    ignore_cleanup_errors=True)
                try:
                    previous = os.environ.get("AMCA_SESSION_TOKEN")
                    if injected:
                        os.environ["AMCA_SESSION_TOKEN"] = (
                            "m58-spy-injected-token-0123456789abcdef-012")
                    else:
                        os.environ.pop("AMCA_SESSION_TOKEN", None)
                    with mock.patch.object(
                        readiness_module, "LocalReadinessService", _Spy
                    ):
                        context = service_module.ServiceContext(
                            root=Path(temporary.name),
                            database=Path(temporary.name) / "amca.db",
                            benchmarks=Path(temporary.name) / "benchmarks",
                        )
                        service = context.intelligence_local_readiness
                        probe = getattr(
                            context, "intelligence_readiness_probe", None)
                        if probe is not None:
                            probe.join(timeout=45)
                        observed = dict(
                            getattr(service, "refresh_kwargs", {}) or {})
                        context.close()
                finally:
                    if previous is None:
                        os.environ.pop("AMCA_SESSION_TOKEN", None)
                    else:
                        os.environ["AMCA_SESSION_TOKEN"] = previous
                    temporary.cleanup()
                if expected is None:
                    self.assertNotIn("structured_probe", observed)
                else:
                    self.assertTrue(observed.get("structured_probe"))


class RealOllamaAttemptTests(unittest.TestCase):
    def test_real_ollama_probe_is_truthful(self):
        """The REAL loopback attempt: truthful either way, never faked.

        On this machine Ollama was unreachable at validation time, so the
        report must be not_ready/local_runtime_unreachable and live=False.
        The assertions hold in both environments.
        """

        service = LocalReadinessService(meta_factory=meta_factory)
        report = service.refresh(
            structured_probe=True,
            adapter_factory=lambda model: OllamaAdapter(
                adapter_id="readiness-probe", provider="ollama",
                model=model),
        )
        self.assertIsInstance(report.runtime_reachable, bool)
        self.assertEqual(report.live, report.runtime_reachable)
        if not report.runtime_reachable:
            self.assertEqual(
                report.reasons["model_router"], REASON_UNREACHABLE)
            self.assertEqual(report.readiness["adaptive_planner"], NOT_READY)


if __name__ == "__main__":
    unittest.main()
