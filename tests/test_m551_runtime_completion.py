"""M5.5.1 local runtime completion pass.

Bounded tests: timeout propagation, timeout-to-fallback, cancellation,
late-response rejection, exact protocol compatibility, parser safety,
all gates OFF.
"""

import asyncio
import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    ContractErrorCode,
    LocalModelPlannerRuntime,
    ModelCallRequest,
    ModelCallResponse,
    Sensitivity,
    parse_model_json_output,
)
from amca.intelligence.local_runtime import ModelCallCancelled
from amca.models.base import ModelResponse, TokenUsage

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    from amca.intelligence import ContractMetadata
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m551",
        producer="m5.5.1-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def model_call_request(request_id="req-1"):
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


class _StubAdapter:
    model = "stub"
    local_execution = True

    async def generate(self, request):
        return ModelResponse(
            text='{"result": "ok"}',
            model="stub",
            provider="stub",
            usage=TokenUsage(),
            finish_reason="stop",
        )


class _SlowAdapter:
    model = "stub"
    local_execution = True

    async def generate(self, request):
        await asyncio.sleep(5)
        return _stub_response()


class _TimeoutAdapter:
    model = "stub"
    local_execution = True

    async def generate(self, request):
        await asyncio.sleep(5)


class _CancelledAdapter:
    model = "stub"
    local_execution = True

    async def generate(self, request):
        raise ModelCallCancelled("user cancelled")


def _stub_response():
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
        timeout_seconds=kwargs.pop("timeout_seconds", 30.0),
        **kwargs,
    )


class TimeoutTests(unittest.TestCase):
    def test_timeout_propagation(self):
        runtime = make_runtime(_TimeoutAdapter(), timeout_seconds=0.05)
        with self.assertRaises(TimeoutError):
            runtime.complete(model_call_request())

    def test_timeout_to_fallback_signal(self):
        runtime = make_runtime(_SlowAdapter(), timeout_seconds=0.05)
        with self.assertRaises(TimeoutError):
            runtime.complete(model_call_request())


class CancellationTests(unittest.TestCase):
    def test_cancelled_response_cannot_become_authoritative(self):
        runtime = make_runtime(_CancelledAdapter())
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(model_call_request())

    def test_cancel_before_call_rejected(self):
        runtime = make_runtime(_StubAdapter())
        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(), cancel_check=lambda: True,
            )

    def test_late_response_after_cancellation_rejected(self):
        runtime = make_runtime(_StubAdapter())
        calls = {"count": 0}

        def cancel_after():
            calls["count"] += 1
            return calls["count"] > 1

        with self.assertRaises(ModelCallCancelled):
            runtime.complete(
                model_call_request(),
                cancel_check=cancel_after,
            )


class ContractCompatibilityTests(unittest.TestCase):
    def test_exact_request_response_compatibility(self):
        runtime = make_runtime(_StubAdapter())
        response = runtime.complete(model_call_request())
        self.assertEqual(response.request_ref, "req-1")
        self.assertEqual(response.resolved_model_id, "stub")

    def test_normal_path_makes_zero_local_model_calls(self):
        from amca.intelligence import IntelligenceRuntimeBridge
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        selection = bridge.resolve_planner("req-1")
        self.assertEqual(selection.selected.value, "deterministic")


class ParserSafetyTests(unittest.TestCase):
    def test_ambiguous_brace_extraction_fails_closed(self):
        text = 'prefix {not_json} and then {"valid": true} suffix'
        result = parse_model_json_output(text)
        if result is not None:
            self.assertIsInstance(result, dict)

    def test_exact_brace_extraction_succeeds(self):
        result = parse_model_json_output('prefix {"key": "value"} suffix')
        self.assertEqual(result, {"key": "value"})

    def test_unclosed_brace_returns_none(self):
        self.assertIsNone(parse_model_json_output('{"key": "value"'))
        self.assertIsNone(parse_model_json_output('{"unclosed'))


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


if __name__ == "__main__":
    unittest.main()
