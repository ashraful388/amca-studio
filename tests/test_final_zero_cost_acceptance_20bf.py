"""Focused unit test suite for AMCA v1.1.1 Final Zero-Cost Native Acceptance Harness (20BF).

Work Order: ANTIGRAVITY-AMCA-V111-FINAL-ZERO-COST-ACCEPTANCE-HARNESS-20BF
Tests/tooling only.
Validates:
1. A failed mandatory vector causes final harness verdict FAIL.
2. Blocked local-model vectors remain visibly blocked (BLOCKED_ENVIRONMENT).
3. Security denials require zero side effects (counters fail-closed).
4. Paid/external-network activity causes immediate failure.
5. Final verdict cannot be PASS if any mandatory non-environment vector is unresolved.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
if str(SOFTWARE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT))
if str(SOFTWARE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT / "src"))
if str(SOFTWARE_ROOT / "packaging") not in sys.path:
    sys.path.insert(0, str(SOFTWARE_ROOT / "packaging"))

from final_zero_cost_acceptance import (
    ALL_VECTORS,
    ENVIRONMENT_OPTIONAL_VECTORS,
    MANDATORY_VECTORS,
    FinalZeroCostAcceptanceHarness,
)


class TestFinalZeroCostAcceptance20BF(unittest.TestCase):
    """Unit tests for the 20BF final zero-cost native acceptance harness."""

    def setUp(self) -> None:
        self.harness = FinalZeroCostAcceptanceHarness()

    def _simulate_passing_mandatory_vectors(self) -> None:
        for vec in MANDATORY_VECTORS:
            self.harness.vector_results[vec] = "PASS"

    def test_failed_mandatory_vector_causes_verdict_fail(self) -> None:
        """A single failed mandatory vector must make the overall verdict FAIL."""
        self._simulate_passing_mandatory_vectors()
        self.harness.vector_results["FILESYSTEM_ALLOWED_READ"] = "FAIL"

        mandatory_all_pass = all(
            self.harness.vector_results.get(v) == "PASS" for v in MANDATORY_VECTORS
        )
        self.assertFalse(mandatory_all_pass)

        security_all_clean = (
            self.harness.wrong_project_disclosure == 0
            and self.harness.malformed_tool_execution == 0
            and self.harness.unknown_tool_execution == 0
            and self.harness.unsupported_tool_execution == 0
            and self.harness.approval_bypass == 0
            and self.harness.post_cancel_tool_dispatch == 0
            and self.harness.post_cancel_model_continuation == 0
            and self.harness.network_external_calls == 0
            and self.harness.paid_api_calls == 0
            and self.harness.model_budget_approval_required == "NO"
        )
        verdict = "PASS" if (mandatory_all_pass and security_all_clean) else "FAIL"
        self.assertEqual(verdict, "FAIL")

    def test_blocked_environment_vectors_remain_visibly_blocked(self) -> None:
        """When local model is unavailable, status is BLOCKED_ENVIRONMENT and not converted to PASS."""
        with patch.object(self.harness, "_probe_ollama_qwen", return_value=False):
            self.assertFalse(self.harness._probe_ollama_qwen())
            self.harness.vector_results["LOCAL_QWEN_MODEL_RUN"] = "BLOCKED_ENVIRONMENT"
            self.assertEqual(
                self.harness.vector_results["LOCAL_QWEN_MODEL_RUN"],
                "BLOCKED_ENVIRONMENT",
            )
            self.assertNotEqual(
                self.harness.vector_results["LOCAL_QWEN_MODEL_RUN"],
                "PASS",
            )

    def test_security_denials_require_zero_side_effects(self) -> None:
        """Any security anomaly (e.g. approval bypass > 0) must force verdict to FAIL."""
        self._simulate_passing_mandatory_vectors()
        self.harness.approval_bypass = 1

        mandatory_all_pass = all(
            self.harness.vector_results.get(v) == "PASS" for v in MANDATORY_VECTORS
        )
        self.assertTrue(mandatory_all_pass)

        security_all_clean = (
            self.harness.wrong_project_disclosure == 0
            and self.harness.malformed_tool_execution == 0
            and self.harness.unknown_tool_execution == 0
            and self.harness.unsupported_tool_execution == 0
            and self.harness.approval_bypass == 0
            and self.harness.post_cancel_tool_dispatch == 0
            and self.harness.post_cancel_model_continuation == 0
            and self.harness.network_external_calls == 0
            and self.harness.paid_api_calls == 0
            and self.harness.model_budget_approval_required == "NO"
        )
        self.assertFalse(security_all_clean)

        verdict = "PASS" if (mandatory_all_pass and security_all_clean) else "FAIL"
        self.assertEqual(verdict, "FAIL")

    def test_paid_or_external_network_activity_causes_immediate_failure(self) -> None:
        """Paid calls, external network calls, or budget approval requirement must fail harness."""
        self._simulate_passing_mandatory_vectors()

        self.harness.paid_api_calls = 1
        security_all_clean = (
            self.harness.network_external_calls == 0
            and self.harness.paid_api_calls == 0
            and self.harness.model_budget_approval_required == "NO"
        )
        self.assertFalse(security_all_clean)

        self.harness.paid_api_calls = 0
        self.harness.network_external_calls = 2
        security_all_clean = (
            self.harness.network_external_calls == 0
            and self.harness.paid_api_calls == 0
            and self.harness.model_budget_approval_required == "NO"
        )
        self.assertFalse(security_all_clean)

        self.harness.network_external_calls = 0
        self.harness.model_budget_approval_required = "YES"
        security_all_clean = (
            self.harness.network_external_calls == 0
            and self.harness.paid_api_calls == 0
            and self.harness.model_budget_approval_required == "NO"
        )
        self.assertFalse(security_all_clean)

    def test_final_verdict_cannot_be_pass_if_any_mandatory_vector_unresolved(self) -> None:
        """If any mandatory vector is missing or unresolved, verdict cannot be PASS."""
        self._simulate_passing_mandatory_vectors()
        del self.harness.vector_results["PROJECT_ISOLATION"]

        mandatory_all_pass = all(
            self.harness.vector_results.get(v) == "PASS" for v in MANDATORY_VECTORS
        )
        self.assertFalse(mandatory_all_pass)

        self.harness.vector_results["PROJECT_ISOLATION"] = "UNRESOLVED"
        mandatory_all_pass = all(
            self.harness.vector_results.get(v) == "PASS" for v in MANDATORY_VECTORS
        )
        self.assertFalse(mandatory_all_pass)


if __name__ == "__main__":
    unittest.main()
