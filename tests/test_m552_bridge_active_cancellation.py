"""M5.5.2 bridge active-cancellation closeout tests.

Required coverage (manager work order
ZC-AMCA-V0.5.0-M5.5.2-BRIDGE-ACTIVE-CANCELLATION-CLOSEOUT-01):

1. bridge resolver returns a LocalModelPlannerRuntime for an authorized
   adapter;
2. resolver performs no model call;
3. Adaptive-OFF normal path never resolves it;
4. in-flight cancellation invokes existing ModelAdapter.cancel;
5. cancellation result is non-authoritative;
6. late response after active cancellation is rejected;
7. timeout/cancellation race fails safely;
8. all gates remain OFF.

The active-cancellation tests run against a REAL ``ModelAdapter`` subclass
so the production ``cancel()`` contract (task registry + task cancellation)
is exercised exactly as shipped — no competing cancellation protocol exists.
"""

import asyncio
import time
import unittest
from unittest import mock

from amca.intelligence import (
    DEFAULT_GATES,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    IntelligenceRuntimeBridge,
    PlannerResult,
    PlannerResultStatus,
    Sensitivity,
)
from amca.intelligence.local_runtime import (
    LocalModelPlannerRuntime,
    ModelCallCancelled,
)
from amca.models.base import ModelAdapter, ModelResponse, TokenUsage

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m552",
        producer="m5.5.2-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def model_call_request(request_id="req-1"):
    from amca.intelligence import ModelCallRequest
    return ModelCallRequest(
        meta=meta_factory("mcr"),
        request_id=request_id,
        provider_ref="stub/local",
        system_instruction=None,
        messages=("test prompt",),
        temperature=0.0,
        max_output_tokens=256,
        timeout_seconds=30.0,
        idempotency_key="idem-1",
        privacy_mode="offline",
        approval_ref=None,
    )


class _RealSleepAdapter(ModelAdapter):
    """Real ModelAdapter exercising the production cancel() contract.

    ``generate`` registers its task through the shipped ``_call_scope`` and
    ``cancel`` delegates to the real base implementation, so an in-flight
    cancellation goes through exactly the mechanism the rest of AMCA uses.
    """

    def __init__(self, delay=5.0):
        super().__init__(adapter_id="sleep-stub", provider="stub", model="stub")
        self._delay = delay
        self.cancel_invocations = 0
        self.cancel_request_ids = []

    async def generate(self, request):
        async with self._call_scope(request):
            await asyncio.sleep(self._delay)
            return ModelResponse(
                text='{"result": "ok"}',
                model="stub",
                provider="stub",
                usage=TokenUsage(),
                finish_reason="stop",
            )

    async def cancel(self, request_id=None):
        self.cancel_invocations += 1
        self.cancel_request_ids.append(request_id)
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError("streaming is unused in M5.5.2 tests")


class _AbortingAdapter(ModelAdapter):
    """Adapter that raises its own cancellation signal mid-flight."""

    def __init__(self, delay=0.05):
        super().__init__(adapter_id="abort-stub", provider="stub", model="stub")
        self._delay = delay

    async def generate(self, request):
        async with self._call_scope(request):
            await asyncio.sleep(self._delay)
            raise ModelCallCancelled("adapter aborted the call")

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError("streaming is unused in M5.5.2 tests")


class _CountingAdapter(ModelAdapter):
    """Adapter whose generate must never be called by the resolver."""

    def __init__(self):
        super().__init__(adapter_id="counting", provider="stub", model="stub")
        self.generate_calls = 0

    async def generate(self, request):
        self.generate_calls += 1
        raise AssertionError("resolver must not make a model call")

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError("streaming is unused in M5.5.2 tests")


class _ProtocolLessAdapter:
    """Duck-typed adapter with NO cancel protocol (interface limitation)."""

    model = "stub"
    local_execution = True

    def __init__(self, delay=5.0):
        self._delay = delay

    async def generate(self, request):
        await asyncio.sleep(self._delay)
        return ModelResponse(
            text='{"result": "ok"}',
            model="stub",
            provider="stub",
            usage=TokenUsage(),
            finish_reason="stop",
        )


def make_runtime(adapter, **kwargs):
    return LocalModelPlannerRuntime(
        adapter=adapter,
        meta_factory=meta_factory,
        timeout_seconds=kwargs.pop("timeout_seconds", 5.0),
        cancel_poll_seconds=kwargs.pop("cancel_poll_seconds", 0.005),
        **kwargs,
    )


def cancel_after(seconds):
    start = time.monotonic()
    return lambda: (time.monotonic() - start) >= seconds


class BridgeResolverTests(unittest.TestCase):
    def test_resolver_returns_local_runtime_for_authorized_adapter(self):
        gates = DEFAULT_GATES.with_overrides(adaptive_planner=True)
        bridge = IntelligenceRuntimeBridge(
            gates=gates, meta_factory=meta_factory,
        )
        adapter = _CountingAdapter()
        runtime = bridge.resolve_local_planner_runtime(adapter=adapter)
        self.assertIsInstance(runtime, LocalModelPlannerRuntime)
        self.assertEqual(runtime.adapter_id, "counting")
        self.assertEqual(runtime.provider, "stub")
        self.assertEqual(runtime.model, "stub")

    def test_resolver_performs_no_model_call(self):
        gates = DEFAULT_GATES.with_overrides(adaptive_planner=True)
        bridge = IntelligenceRuntimeBridge(
            gates=gates, meta_factory=meta_factory,
        )
        adapter = _CountingAdapter()
        first = bridge.resolve_local_planner_runtime(adapter=adapter)
        second = bridge.resolve_local_planner_runtime(adapter=adapter)
        self.assertIsNot(first, second)
        self.assertEqual(adapter.generate_calls, 0)

    def test_resolver_refuses_while_adaptive_gate_off(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        adapter = _CountingAdapter()
        with self.assertRaises(ContractError) as ctx:
            bridge.resolve_local_planner_runtime(adapter=adapter)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertEqual(adapter.generate_calls, 0)

    def test_normal_path_never_constructs_local_runtime(self):
        constructions = {"count": 0}

        class _Guard:
            def __init__(self, *args, **kwargs):
                constructions["count"] += 1

        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        with mock.patch(
            "amca.intelligence.local_runtime.LocalModelPlannerRuntime",
            _Guard,
        ):
            selection = bridge.resolve_planner("req-1")
            self.assertEqual(selection.selected.value, "deterministic")
            bridge.status()
            bridge.hybrid_status()
        self.assertEqual(constructions["count"], 0)

    def test_adaptive_off_invoke_planner_never_resolves_runtime(self):
        constructions = {"count": 0}

        class _Guard:
            def __init__(self, *args, **kwargs):
                constructions["count"] += 1

        class _DeterministicStub:
            def __init__(self):
                self.calls = 0

            def propose(self, planner_input):
                self.calls += 1
                return _deterministic_result()

        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        deterministic = _DeterministicStub()
        with mock.patch(
            "amca.intelligence.local_runtime.LocalModelPlannerRuntime",
            _Guard,
        ):
            selection = bridge.resolve_planner("req-1")
            invocation = bridge.invoke_planner(
                selection, _planner_input(), deterministic=deterministic,
            )
            self.assertEqual(deterministic.calls, 1)
            self.assertFalse(invocation.used_fallback)
        self.assertEqual(constructions["count"], 0)


class ActiveCancellationTests(unittest.TestCase):
    def test_in_flight_cancellation_invokes_existing_adapter_cancel(self):
        adapter = _RealSleepAdapter(delay=5.0)
        runtime = make_runtime(adapter)
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(request_id="req-live"),
                cancel_check=cancel_after(0.05),
            )
        self.assertEqual(adapter.cancel_invocations, 1)
        self.assertEqual(adapter.cancel_request_ids, ["req-live"])
        self.assertTrue(runtime.last_cancel_used_adapter_protocol)

    def test_cancellation_result_is_never_authoritative(self):
        adapter = _RealSleepAdapter(delay=5.0)
        runtime = make_runtime(adapter)
        result = None
        try:
            result = runtime.complete(
                model_call_request(), cancel_check=cancel_after(0.05),
            )
        except ModelCallCancelled:
            pass
        self.assertIsNone(result)

    def test_late_response_after_active_cancellation_rejected(self):
        # adapter returns concurrently with the cancellation request: no
        # matter which side of the race wins, the response is rejected.
        adapter = _RealSleepAdapter(delay=0.0)
        runtime = make_runtime(adapter)
        calls = {"count": 0}

        def flip_after_first():
            calls["count"] += 1
            return calls["count"] >= 2

        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(), cancel_check=flip_after_first,
            )

    def test_cancellation_wins_timeout_cancellation_race(self):
        adapter = _RealSleepAdapter(delay=5.0)
        runtime = make_runtime(adapter, timeout_seconds=0.5)
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(), cancel_check=cancel_after(0.02),
            )
        self.assertTrue(runtime.last_cancel_used_adapter_protocol)

    def test_timeout_wins_timeout_cancellation_race(self):
        adapter = _RealSleepAdapter(delay=5.0)
        runtime = make_runtime(adapter, timeout_seconds=0.02)
        with self.assertRaises(TimeoutError):
            runtime.complete(
                model_call_request(), cancel_check=cancel_after(0.5),
            )
        # the timeout fired long before the cancellation deadline, so the
        # adapter cancel protocol was never engaged
        self.assertEqual(adapter.cancel_invocations, 0)
        self.assertIsNone(runtime.last_cancel_used_adapter_protocol)

    def test_adapter_raised_cancellation_signal_classified(self):
        adapter = _AbortingAdapter(delay=0.05)
        runtime = make_runtime(adapter)
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(model_call_request(), cancel_check=lambda: False)

    def test_adapter_without_cancel_protocol_reported_honestly(self):
        adapter = _ProtocolLessAdapter(delay=5.0)
        runtime = make_runtime(adapter)
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(), cancel_check=cancel_after(0.05),
            )
        # the adapter exposes no cancel protocol: the runtime could only
        # cancel its own call task, and it records that limitation
        # truthfully instead of claiming protocol-level cancellation
        self.assertFalse(runtime.last_cancel_used_adapter_protocol)


class GateTests(unittest.TestCase):
    def test_all_four_gates_remain_off(self):
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )


def _planner_input():
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


def _deterministic_result():
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


if __name__ == "__main__":
    unittest.main()
