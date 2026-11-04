"""20BO synthetic tests for the local-qwen service-route vector.

Cold-load / delayed-response behavior is simulated with stubbed HTTP so no
real Ollama is required to prove gate semantics:

- probe negative -> BLOCKED_ENVIRONMENT, service route never invoked;
- delayed completion within the bounded cold-start window -> PASS
  (cold-load tolerance, no fixed short generation timeout);
- service route reports failed -> FAIL;
- bounded window exhausted without terminal state -> BLOCKED_ENVIRONMENT
  (never PASS);
- service rejects the run -> FAIL (genuine service-route failure, never a
  silently-skipped check);
- the 31 mandatory-vector semantics are untouched.

Tests/tooling only.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "final_zero_cost_acceptance",
    SOFTWARE_ROOT / "packaging" / "final_zero_cost_acceptance.py",
)
fzc = importlib.util.module_from_spec(_spec)
sys.modules["final_zero_cost_acceptance"] = fzc
_spec.loader.exec_module(fzc)


def make_harness(timeout_seconds: int, script):
    """Harness with stubbed probe/http driven by `script` (list of returns)."""

    class StubHarness(fzc.FinalZeroCostAcceptanceHarness):
        def __init__(self):
            self.local_model_timeout_seconds = timeout_seconds
            self.calls = []

        def _classify_local_environment(self):
            return "ENVIRONMENT_READY"

        def _probe_ollama_qwen(self) -> bool:
            return script["probe_available"]

        def _http(self, path, method="GET", body=None, **kw):
            self.calls.append((method, path))
            if method == "POST" and path.endswith("/agent-runs"):
                item = script["post"]
                self.calls[-1] = (method, path, item)
                return item
            if "/status" in path:
                item = script["status_sequence"].pop(0) if script["status_sequence"] else (200, {"status": "running"}, "")
                return item
            return (404, {}, "")

    return StubHarness()


class ColdLoadToleranceTests(unittest.TestCase):
    def test_delayed_completion_within_bounded_window_passes(self):
        harness = make_harness(300, {
            "probe_available": True,
            "post": (201, {"agent_run": {"organization_id": "agent-x"}}, ""),
            "status_sequence": [
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "completed"}, ""),  # cold load finally done
            ],
        })
        result, detail, reason = harness._run_local_qwen_service_vector("proj-x", "ENVIRONMENT_READY")
        self.assertEqual("PASS", result)
        self.assertIn("service/runtime route", detail)

    def test_delayed_polls_still_running_then_completed(self):
        harness = make_harness(300, {
            "probe_available": True,
            "post": (200, {"agent_run": {"organization_id": "agent-y"}}, ""),
            "status_sequence": [
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "completed"}, ""),
            ],
        })
        result, _, reason = harness._run_local_qwen_service_vector("proj-y", "ENVIRONMENT_READY")
        self.assertEqual("PASS", result)


class FailureSemanticsTests(unittest.TestCase):
    def test_service_failed_terminal_state_fails(self):
        harness = make_harness(300, {
            "probe_available": True,
            "post": (201, {"agent_run": {"organization_id": "agent-z"}}, ""),
            "status_sequence": [(200, {"status": "failed"}, "")],
        })
        result, _, reason = harness._run_local_qwen_service_vector("proj-z", "ENVIRONMENT_READY")
        self.assertEqual("FAIL", result)

    def test_service_rejecting_run_fails_never_blocked(self):
        harness = make_harness(300, {
            "probe_available": True,
            "post": (400, {"error": "bad request"}, ""),
            "status_sequence": [],
        })
        result, detail, reason = harness._run_local_qwen_service_vector("proj-r", "ENVIRONMENT_READY")
        self.assertEqual("FAIL", result)
        self.assertEqual("SERVICE_RUN_START_REJECTED", reason)

    def test_bounded_window_exhaustion_is_blocked_never_pass(self):
        harness = make_harness(30, {
            "probe_available": True,
            "post": (201, {"agent_run": {"organization_id": "agent-t"}}, ""),
            "status_sequence": [
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
                (200, {"status": "running"}, ""),
            ],
        })
        result, detail, reason = harness._run_local_qwen_service_vector("proj-t", "ENVIRONMENT_READY")
        self.assertEqual("BLOCKED_ENVIRONMENT", result)
        self.assertIn("did not reach a terminal state", detail)

    def test_probe_negative_blocks_without_service_invocation(self):
        harness = make_harness(300, {
            "probe_available": False,
            "post": (201, {"agent_run": {"organization_id": "agent-n"}}, ""),
            "status_sequence": [],
        })
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-n", "MODEL_NOT_PRESENT"
        )
        self.assertEqual("BLOCKED_ENVIRONMENT", result)
        self.assertIn("model not pulled", detail)
        # The decisive service route was never invoked.
        self.assertEqual([], harness.calls)


class MandatoryVectorSemanticsUntouchedTests(unittest.TestCase):
    def test_mandatory_vector_registry_unchanged(self):
        self.assertEqual(31, len(fzc.MANDATORY_VECTORS))
        self.assertEqual(("LOCAL_QWEN_MODEL_RUN",), fzc.ENVIRONMENT_OPTIONAL_VECTORS)
        self.assertNotIn("LOCAL_QWEN_MODEL_RUN", fzc.MANDATORY_VECTORS)

    def test_bounded_timeout_is_enforced_not_unbounded(self):
        # Real constructor: the timeout is clamped to a positive bound.
        harness = fzc.FinalZeroCostAcceptanceHarness(
            local_model_timeout_seconds=-5
        )
        self.assertEqual(30, harness.local_model_timeout_seconds)
        harness_ok = fzc.FinalZeroCostAcceptanceHarness(
            local_model_timeout_seconds=600
        )
        self.assertEqual(600, harness_ok.local_model_timeout_seconds)


if __name__ == "__main__":
    unittest.main()
