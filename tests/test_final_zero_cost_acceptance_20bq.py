"""20BQ synthetic tests: structured machine-readable diagnostics for the
local-qwen vector.

Every stable reason code is exercised:
- ENVIRONMENT_UNAVAILABLE (probe negative) -> BLOCKED_ENVIRONMENT;
- COMPLETED -> PASS;
- SERVICE_FAILED (failed terminal state) -> FAIL;
- SERVICE_RUN_START_REJECTED (service refuses the run) -> FAIL;
- SERVICE_ROUTE_ERROR (unexpected service-route exception) -> FAIL;
- SERVICE_TIMEOUT (bounded window exhausted, non-terminal) ->
  BLOCKED_ENVIRONMENT with structured reason SERVICE_TIMEOUT, machine-
  distinguishable from ENVIRONMENT_UNAVAILABLE without parsing free text.

Tests/tooling only; 31 mandatory-vector semantics untouched.
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


def make_harness(timeout_seconds: int, *, probe: bool = True, post=(201, {"agent_run": {"organization_id": "agent-q"}}, ""), seq=None, post_raises=None):
    class StubHarness(fzc.FinalZeroCostAcceptanceHarness):
        def __init__(self):
            self.local_model_timeout_seconds = timeout_seconds
            self.calls = []

        def _classify_local_environment(self):
            return "ENVIRONMENT_READY" if probe else "MODEL_NOT_PRESENT"

        def _http(self, path, method="GET", body=None, **kw):
            self.calls.append((method, path))
            if method == "POST" and path.endswith("/agent-runs"):
                if post_raises is not None:
                    raise post_raises
                return post
            if "/status" in path:
                item = seq.pop(0) if seq else (200, {"status": "running"}, "")
                return item
            return (404, {}, "")

    return StubHarness()


class StructuredReasonTests(unittest.TestCase):
    def test_environment_unavailable(self):
        harness = make_harness(300, probe=False)
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-q", "MODEL_NOT_PRESENT"
        )
        self.assertEqual(("BLOCKED_ENVIRONMENT", "MODEL_NOT_PRESENT"), (result, reason))
        self.assertEqual([], harness.calls)  # decisive route never invoked

    def test_completed(self):
        harness = make_harness(300, seq=[
            (200, {"status": "running"}, ""),
            (200, {"status": "completed"}, ""),
        ])
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        self.assertEqual(("PASS", "COMPLETED"), (result, reason))

    def test_service_failed(self):
        harness = make_harness(300, seq=[
            (200, {"status": "failed"}, ""),
        ])
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_FAILED"), (result, reason))

    def test_service_run_start_rejected(self):
        harness = make_harness(300, post=(400, {"error": "bad"}, ""))
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_RUN_START_REJECTED"), (result, reason))

    def test_service_route_error(self):
        harness = make_harness(300, post_raises=RuntimeError("connection reset"))
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_ROUTE_ERROR"), (result, reason))

    def test_service_timeout(self):
        harness = make_harness(3, seq=[
            (200, {"status": "running"}, ""),
        ])
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        self.assertEqual("BLOCKED_ENVIRONMENT", result)
        self.assertEqual("SERVICE_TIMEOUT", reason)
        self.assertIn("did not reach a terminal state", detail)

    def test_timeout_reason_differs_from_absence_reason(self):
        # The core 20BQ requirement: SERVICE_TIMEOUT and
        # ENVIRONMENT_UNAVAILABLE carry different structured reasons while
        # both may surface the same top-level BLOCKED_ENVIRONMENT.
        timeout_harness = make_harness(3, seq=[(200, {"status": "running"}, "")])
        _, _, timeout_reason = timeout_harness._run_local_qwen_service_vector(
            "proj-q", "ENVIRONMENT_READY"
        )
        absent_harness = make_harness(300, probe=False)
        _, _, absent_reason = absent_harness._run_local_qwen_service_vector(
            "proj-q", "MODEL_NOT_PRESENT"
        )
        self.assertEqual("SERVICE_TIMEOUT", timeout_reason)
        self.assertEqual("MODEL_NOT_PRESENT", absent_reason)
        self.assertNotEqual(timeout_reason, absent_reason)


class ReportEmbedsDiagnostics(unittest.TestCase):
    def test_report_template_contains_structured_diagnostics_field(self):
        src = (SOFTWARE_ROOT / "packaging" / "final_zero_cost_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"structured_diagnostics"', src)
        self.assertIn('"reason"', src)
        self.assertIn("SERVICE_TIMEOUT", src)

    def test_mandatory_vector_registry_unchanged(self):
        self.assertEqual(31, len(fzc.MANDATORY_VECTORS))
        self.assertEqual(
            ("LOCAL_QWEN_MODEL_RUN",), fzc.ENVIRONMENT_OPTIONAL_VECTORS
        )


if __name__ == "__main__":
    unittest.main()
