"""20BU tests: current-line manifest completion.

Prove that the seven release-critical suites created after 20BA are
registered in the runner manifest and that the manifest closure changes
nothing else:

1. manifest completeness — exactly the seven 20BT-identified suites are
   registered as current-line;
2. failure propagation — a newly registered suite forced to fail via the
   fake executor flips the overall verdict to FAIL (no real repository test
   is corrupted);
3. missing-suite diagnostic — on a pre-convergence source state where a
   newly registered suite's file does not exist, the runner classifies it
   SKIPPED_ABSENT (never PASS); diagnostic only;
4. classification — all seven classify as plain current-line suites;
5. no other manifest or logic change (20BF/20BO/20BQ/20BQ-R1/20BH/20BH-R1
   sources untouched).

Tests/tooling only.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "current_release_regression_runner",
    SOFTWARE_ROOT / "packaging" / "current_release_regression_runner.py",
)
runner = importlib.util.module_from_spec(_spec)
sys.modules["current_release_regression_runner"] = runner
_spec.loader.exec_module(runner)

SEVEN_REGISTERED_SUITES = {
    "test_17e6_interactive_sse_service_20as.py",
    "test_final_zero_cost_acceptance_20bf.py",
    "test_final_zero_cost_acceptance_20bo.py",
    "test_final_zero_cost_acceptance_20bq.py",
    "test_final_zero_cost_acceptance_20bqr1.py",
    "test_current_release_compliance_gate_20bh.py",
    "test_current_release_compliance_gate_20bh_r1.py",
}

#: Suites legitimately registered in the release battery after 20BU, one per
#: completed remediation order on the current line (26D0 reconciliation):
#: 21A -> v1.1.2 release-tooling recovery, 22A -> v1.1.3 clean-payload
#: recovery, 24A -> v1.1.4 defect remediation.  25B/25C/26C suites are
#: work-order-specific and are intentionally not part of the battery.
POST_20BU_REGISTERED_SUITES = {
    "test_studio_v112_release_tooling_recovery_21a.py",
    "test_studio_v113_clean_payload_recovery_22a.py",
    "test_v114_24a_defect_remediation.py",
}


def fake_executor(results_by_suite):
    calls = []

    def execute(software_root, suite, pytest_extra):
        calls.append(suite)
        rc, summary = results_by_suite.get(suite, (0, "synthetic pass"))
        return {
            "suite": suite,
            "classification": "current",
            "status": "PASS" if rc == 0 else "FAIL",
            "returncode": rc,
            "summary": summary,
        }

    execute.calls = calls
    return execute


class ManifestCompletenessTests(unittest.TestCase):
    def test_all_seven_suites_registered_as_current(self):
        registered = {
            s for s in runner.CURRENT_LINE_SUITES
            if s in SEVEN_REGISTERED_SUITES
        }
        self.assertEqual(SEVEN_REGISTERED_SUITES, registered)
        for suite in SEVEN_REGISTERED_SUITES:
            self.assertEqual("current", runner.classify_suite(suite))

    def test_no_other_manifest_change(self):
        # The rest of the manifest is exactly the pre-20BU battery plus the
        # seven 20BU additions plus the post-20BU order suites (26D0).
        pre_20bu = {
            "test_17e4_governed_loop_20w.py",
            "test_17e4_runtime_acceptance_harness.py",
            "test_17e5_service_exposure_20ae.py",
            "test_agent_fabric.py",
            "test_agent_runtime.py",
            "test_agent_runtime_batching.py",
            "test_artifact_render.py",
            "test_artifacts.py",
            "test_cross_provider_router.py",
            "test_g8_acceptance_harness.py",
            "test_g8_dependency_gate_fail_closed_20y.py",
            "test_m12_model_planner.py",
            "test_m4_router_foundation.py",
            "test_packaged_dependency_hardening_20l.py",
            "test_provider_adapter_conformance.py",
            "test_studio_v100_ps2_canonical_integration_sentinel.py",
            "test_studio_v101_13c_agent_artifact_governance.py",
            "test_studio_v101_13d_verifying_cancellation.py",
            "test_studio_v101_13h_agent_observability_core.py",
            "test_studio_v101_13i_durable_provenance_bindings.py",
            "test_studio_v101_13j_agent_run_tree_api.py",
            "test_studio_v101_13k_run_summary_query_efficiency.py",
            "test_toolcall_production_wiring_20d1.py",
        }
        self.assertEqual(
            pre_20bu | SEVEN_REGISTERED_SUITES | POST_20BU_REGISTERED_SUITES,
            set(runner.CURRENT_LINE_SUITES),
        )


class FailurePropagationVectorTests(unittest.TestCase):
    def test_failing_newly_registered_suite_flips_verdict(self):
        # Deterministic propagation vector: a controlled temporary root
        # provides the target suite's file (existence only — no real
        # repository test is corrupted), and the fake executor reports the
        # injected failure. Require: overall verdict FAIL.
        target = "test_17e6_interactive_sse_service_20as.py"
        harness = fake_executor({
            "test_m4_router_foundation.py": (0, "19 passed"),
            target: (1, "1 failed — injected"),
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / target).write_text("", encoding="utf-8")
            orig = runner.CURRENT_LINE_SUITES
            runner.CURRENT_LINE_SUITES = (
                "test_m4_router_foundation.py",
                target,
            )
            try:
                report = runner.run_current_release_battery(
                    root, executor=harness
                )
            finally:
                runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("FAIL", report["verdict"])
        failed = {r["suite"]: r["status"] for r in report["current_line"]}
        self.assertEqual("FAIL", failed[target])
        # The failure was a genuine executor result, not swallowed.
        self.assertEqual(
            "FAIL",
            next(
                r["status"] for r in report["current_line"] if r["suite"] == target
            ),
        )

    def test_all_newly_registered_suites_passing_yields_pass(self):
        # State-aware: at the fully converged tip all seven exist and are
        # executed (current_line == 7); on a pre-convergence checkout the
        # absent ones surface as SKIPPED_ABSENT. Either way: verdict PASS
        # with zero FAIL results and zero fail-closed entries.
        harness = fake_executor({})
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = tuple(SEVEN_REGISTERED_SUITES)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=harness
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("PASS", report["verdict"])
        self.assertEqual([], report["fail_closed"])
        executed = {r["suite"] for r in report["current_line"]}
        skipped = {r["suite"] for r in report["skipped_absent"]}
        self.assertEqual(SEVEN_REGISTERED_SUITES, executed | skipped)
        self.assertFalse(executed & skipped)


class MissingSuiteDiagnosticTests(unittest.TestCase):
    def test_newly_registered_suite_absent_on_old_state_is_skipped_absent(self):
        # On the old 20BA/20BR source state the newly registered files do
        # not exist. The runner must classify them SKIPPED_ABSENT (never
        # PASS). Diagnostic evidence only; decisive proof is the converged
        # tip where all seven are present and executed with zero skips.
        with tempfile.TemporaryDirectory() as tmp:
            empty_root = Path(tmp)
            (empty_root / "tests").mkdir()
            harness = fake_executor({})
            orig = runner.CURRENT_LINE_SUITES
            runner.CURRENT_LINE_SUITES = (
                "test_m4_router_foundation.py",
                "test_17e6_interactive_sse_service_20as.py",
            )
            try:
                report = runner.run_current_release_battery(
                    empty_root, executor=harness
                )
            finally:
                runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("PASS", report["verdict"])  # m4 passed; no blocker
        self.assertEqual(1, len(report["skipped_absent"]))
        self.assertEqual(
            "test_17e6_interactive_sse_service_20as.py",
            report["skipped_absent"][0]["suite"],
        )
        self.assertEqual(
            "SKIPPED_ABSENT", report["skipped_absent"][0]["status"]
        )


if __name__ == "__main__":
    unittest.main()
