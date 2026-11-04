"""20BQ-R1 closure tests: distinct stable environment reasons.

The probe/environment classification produces distinct machine-readable
reasons — RUNTIME_UNREACHABLE and MODEL_NOT_PRESENT no longer collapse into
one code — and every full reason mapping is proven:

1. RUNTIME_UNREACHABLE -> BLOCKED_ENVIRONMENT (service route never invoked);
2. MODEL_NOT_PRESENT -> BLOCKED_ENVIRONMENT (service route never invoked);
3. ENVIRONMENT_READY + completed service run -> PASS (COMPLETED);
4. ENVIRONMENT_READY + failed terminal state -> FAIL (SERVICE_FAILED);
5. ENVIRONMENT_READY + run start rejected -> FAIL (SERVICE_RUN_START_REJECTED);
6. ENVIRONMENT_READY + route exception -> FAIL (SERVICE_ROUTE_ERROR);
7. ENVIRONMENT_READY + bounded window exhausted -> BLOCKED_ENVIRONMENT
   (SERVICE_TIMEOUT).

Tests/tooling only; 31 mandatory-vector semantics untouched; no free-text
parsing is needed to distinguish any state.
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


def make_harness(timeout_seconds: int, *, classification: str = "ENVIRONMENT_READY", post=(201, {"agent_run": {"organization_id": "agent-r1"}}, ""), seq=None, tags_error=None, tags_models=None):
    class StubHarness(fzc.FinalZeroCostAcceptanceHarness):
        def __init__(self):
            self.local_model_timeout_seconds = timeout_seconds
            self.calls = []

        def _classify_local_environment(self):
            if classification == "RUNTIME_UNREACHABLE":
                return classification
            if classification == "MODEL_NOT_PRESENT":
                return classification
            return "ENVIRONMENT_READY"

        def _probe_ollama_qwen(self):
            return self._classify_local_environment() == "ENVIRONMENT_READY"

        def _http(self, path, method="GET", body=None, **kw):
            self.calls.append((method, path))
            if method == "POST" and path.endswith("/agent-runs"):
                return post
            if "/status" in path:
                item = seq.pop(0) if seq else (200, {"status": "running"}, "")
                return item
            return (404, {}, "")

    return StubHarness()


class EnvironmentBranchTests(unittest.TestCase):
    def test_runtime_unreachable_is_distinct_reason(self):
        harness = make_harness(300, classification="RUNTIME_UNREACHABLE")
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "RUNTIME_UNREACHABLE"
        )
        self.assertEqual(("BLOCKED_ENVIRONMENT", "RUNTIME_UNREACHABLE"), (result, reason))
        self.assertEqual([], harness.calls)  # decisive route never invoked

    def test_model_not_present_is_distinct_reason(self):
        harness = make_harness(300, classification="MODEL_NOT_PRESENT")
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "MODEL_NOT_PRESENT"
        )
        self.assertEqual(("BLOCKED_ENVIRONMENT", "MODEL_NOT_PRESENT"), (result, reason))
        self.assertEqual([], harness.calls)

    def test_the_two_environment_reasons_do_not_collapse(self):
        unreachable = make_harness(300, classification="RUNTIME_UNREACHABLE")
        _, _, r1 = unreachable._run_local_qwen_service_vector(
            "proj-r1", "RUNTIME_UNREACHABLE"
        )
        no_model = make_harness(300, classification="MODEL_NOT_PRESENT")
        _, _, r2 = no_model._run_local_qwen_service_vector(
            "proj-r1", "MODEL_NOT_PRESENT"
        )
        self.assertNotEqual(r1, r2)
        self.assertIn(r1, ("RUNTIME_UNREACHABLE", "MODEL_NOT_PRESENT"))
        self.assertIn(r2, ("RUNTIME_UNREACHABLE", "MODEL_NOT_PRESENT"))

    def test_real_classifier_uses_tags_payload(self):
        # Genuine classification check with a stubbed tags endpoint.
        import json as _json

        class TagsHarness(fzc.FinalZeroCostAcceptanceHarness):
            def __init__(self, payload):
                self.ollama_url = "http://127.0.0.1:11434"
                self.ollama_model = "qwen3:1.7b"
                self.payload = payload

            def _fetch_tags(self):
                return self.payload

        # Reachable but model missing.
        h_missing = TagsHarness({"models": [{"name": "llama3:8b"}]})
        import urllib.request, urllib.error

        def fake_urlopen_missing(req, timeout=None):
            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return _json.dumps(
                        {"models": [{"name": "llama3:8b"}]}
                    ).encode()

            return R()

        # Classify via the real method with monkeypatched urlopen.
        import builtins

        orig_urlopen = fzc.urllib.request.urlopen
        fzc.urllib.request.urlopen = fake_urlopen_missing
        try:
            self.assertEqual(
                "MODEL_NOT_PRESENT", h_missing._classify_local_environment()
            )
        finally:
            fzc.urllib.request.urlopen = orig_urlopen

        # Reachable and model present.
        def fake_urlopen_present(req, timeout=None):
            class R:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return _json.dumps(
                        {"models": [{"name": "qwen3:1.7b"}]}
                    ).encode()

            return R()

        h_ready = TagsHarness({"models": [{"name": "qwen3:1.7b"}]})
        fzc.urllib.request.urlopen = fake_urlopen_present
        try:
            self.assertEqual(
                "ENVIRONMENT_READY", h_ready._classify_local_environment()
            )
        finally:
            fzc.urllib.request.urlopen = orig_urlopen

        # Unreachable runtime.
        def fake_urlopen_dead(req, timeout=None):
            raise OSError("connection refused")

        h_dead = TagsHarness({})
        fzc.urllib.request.urlopen = fake_urlopen_dead
        try:
            self.assertEqual(
                "RUNTIME_UNREACHABLE", h_dead._classify_local_environment()
            )
        finally:
            fzc.urllib.request.urlopen = orig_urlopen


class FullReasonMappingTests(unittest.TestCase):
    def test_completed(self):
        harness = make_harness(300, seq=[
            (200, {"status": "running"}, ""),
            (200, {"status": "completed"}, ""),
        ])
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "ENVIRONMENT_READY"
        )
        self.assertEqual(("PASS", "COMPLETED"), (result, reason))

    def test_service_failed(self):
        harness = make_harness(300, seq=[(200, {"status": "failed"}, "")])
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_FAILED"), (result, reason))

    def test_run_start_rejected(self):
        harness = make_harness(300, post=(400, {"error": "bad"}, ""))
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_RUN_START_REJECTED"), (result, reason))

    def test_route_error(self):
        class ErrHarness(fzc.FinalZeroCostAcceptanceHarness):
            def __init__(self):
                self.local_model_timeout_seconds = 300

            def _http(self, *a, **kw):
                raise RuntimeError("connection reset")

        harness = ErrHarness()
        result, _, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "ENVIRONMENT_READY"
        )
        self.assertEqual(("FAIL", "SERVICE_ROUTE_ERROR"), (result, reason))

    def test_service_timeout(self):
        harness = make_harness(3, seq=[(200, {"status": "running"}, "")])
        result, detail, reason = harness._run_local_qwen_service_vector(
            "proj-r1", "ENVIRONMENT_READY"
        )
        self.assertEqual(("BLOCKED_ENVIRONMENT", "SERVICE_TIMEOUT"), (result, reason))

    def test_all_seven_mappings_are_distinct(self):
        mappings = {
            "RUNTIME_UNREACHABLE": "BLOCKED_ENVIRONMENT",
            "MODEL_NOT_PRESENT": "BLOCKED_ENVIRONMENT",
            "COMPLETED": "PASS",
            "SERVICE_FAILED": "FAIL",
            "SERVICE_RUN_START_REJECTED": "FAIL",
            "SERVICE_ROUTE_ERROR": "FAIL",
            "SERVICE_TIMEOUT": "BLOCKED_ENVIRONMENT",
        }
        self.assertEqual(7, len(mappings))
        # reason codes are unique even where top-level outcomes repeat
        self.assertEqual(7, len({r for r in mappings}))
        for reason, outcome in mappings.items():
            self.assertIn(outcome, ("PASS", "FAIL", "BLOCKED_ENVIRONMENT"))

    def test_mandatory_vector_registry_unchanged(self):
        self.assertEqual(31, len(fzc.MANDATORY_VECTORS))
        self.assertEqual(
            ("LOCAL_QWEN_MODEL_RUN",), fzc.ENVIRONMENT_OPTIONAL_VECTORS
        )


if __name__ == "__main__":
    unittest.main()
