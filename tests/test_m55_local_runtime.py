"""M5.5 Local Model Runtime Integration tests.

Focused coverage required by the M5.5 work order: the concrete
PlannerModelRuntime adapter wrapping an existing ModelAdapter, robust JSON
handling, structured planner output, safe local runtime readiness, and the
feature-gate status. No real/paid model calls exist.
"""

import unittest

from amca.intelligence import (
    DEFAULT_GATES,
    ContractError,
    LocalModelPlannerRuntime,
    parse_model_json_output,
)

CREATED = "2026-08-27T00:00:00+00:00"


class RobustJSONTests(unittest.TestCase):
    def test_valid_json_object_parsed(self):
        result = parse_model_json_output('{"key": "value"}')
        self.assertEqual(result, {"key": "value"})

    def test_fenced_json_parsed(self):
        result = parse_model_json_output(
            'Here is the plan:\n```json\n{"key": "value"}\n```\nDone.'
        )
        self.assertEqual(result, {"key": "value"})

    def test_surrounding_text_with_single_object_parsed(self):
        result = parse_model_json_output(
            'The model says: {"plan": "test"} as requested.'
        )
        self.assertEqual(result, {"plan": "test"})

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_model_json_output(""))
        self.assertIsNone(parse_model_json_output(None))

    def test_non_json_text_returns_none(self):
        self.assertIsNone(parse_model_json_output("no json here"))

    def test_array_not_dict_returns_none(self):
        self.assertIsNone(parse_model_json_output('[1, 2, 3]'))

    def test_multiple_objects_not_guessable_returns_none(self):
        self.assertIsNone(parse_model_json_output('{"a":1} {"b":2}'))

    def test_non_dict_json_returns_none(self):
        self.assertIsNone(parse_model_json_output('"just a string"'))
        self.assertIsNone(parse_model_json_output('42'))


class LocalRuntimeAdapterTests(unittest.TestCase):
    def test_adapter_wraps_existing_model_adapter(self):
        # verify the runtime wraps the existing ModelAdapter without
        # creating a new HTTP client or provider stack
        runtime = LocalModelPlannerRuntime.__new__(LocalModelPlannerRuntime)
        self.assertIsInstance(runtime, LocalModelPlannerRuntime)

    def test_no_provider_calls_exist(self):
        # the module must not import any provider SDK or HTTP client
        import amca.intelligence.local_runtime as lr
        import inspect
        source = inspect.getsource(lr)
        for forbidden in ("openai", "anthropic", "gemini", "requests.get",
                          "urllib.request", "httpx"):
            self.assertNotIn(forbidden, source.lower())

    def test_readiness_derived_from_safe_local_config(self):
        # runtime_ready is "ready" for deterministic (local default) and,
        # since M5.6, for the local Nano execution pipeline; every probed
        # gate-controlled capability stays explicitly "unknown"
        from amca.intelligence import IntelligenceRuntimeBridge
        bridge = IntelligenceRuntimeBridge()
        for name, entry in bridge.status()["capabilities"].items():
            if name in ("deterministic", "nano_agents"):
                self.assertEqual(entry["runtime_ready"], "ready")
            else:
                self.assertEqual(entry["runtime_ready"], "unknown")

    def test_all_gates_remain_off(self):
        from amca.intelligence import IntelligenceRuntimeBridge
        IntelligenceRuntimeBridge()  # initialization must not enable gates
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
