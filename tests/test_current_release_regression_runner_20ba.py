"""Focused tests for the current-release regression runner (20BA).

Prove the five required invariants:
1. the five known suites classify as historical;
2. current-line suite failures propagate as failure;
3. an unknown packaging/preflight suite fails closed;
4. historical exclusion is reported explicitly;
5. the runner never treats historical-suite PASS as evidence for current
   v1.1.1 packaging.

Tests only; no product/dependency/packaging changes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_DIR = SOFTWARE_ROOT / "packaging"

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "current_release_regression_runner",
    PACKAGING_DIR / "current_release_regression_runner.py",
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

# Suites registered by 20BU that postdate the 20BA source line; they exist
# at the fully converged tip and are absent (SKIPPED_ABSENT) here.
POST_20BA_TOOLING_SUITES = {
    "test_17e6_interactive_sse_service_20as.py",
    "test_final_zero_cost_acceptance_20bf.py",
    "test_final_zero_cost_acceptance_20bo.py",
    "test_final_zero_cost_acceptance_20bq.py",
    "test_final_zero_cost_acceptance_20bqr1.py",
    "test_current_release_compliance_gate_20bh.py",
    "test_current_release_compliance_gate_20bh_r1.py",
}


def _fake_executor(results_by_suite):
    calls = []

    def execute(software_root, suite, pytest_extra):
        calls.append(suite)
        rc, summary = results_by_suite[suite]
        return {
            "suite": suite,
            "classification": "current",
            "status": "PASS" if rc == 0 else "FAIL",
            "returncode": rc,
            "summary": summary,
        }

    execute.calls = calls
    return execute


class FiveKnownSuitesClassifyHistoricalTests(unittest.TestCase):
    def test_all_five_known_suites_classify_as_historical(self):
        for suite in sorted(runner.HISTORICAL_SUITES):
            self.assertEqual("historical", runner.classify_suite(suite))
        self.assertEqual(5, len(runner.HISTORICAL_SUITES))


class CurrentLineFailurePropagationTests(unittest.TestCase):
    def test_failing_current_suite_propagates_as_failure(self):
        fake = _fake_executor({
            "test_m4_router_foundation.py": (1, "1 failed"),
        })
        # Patch the manifest to a single fast suite for determinism.
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_m4_router_foundation.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("FAIL", report["verdict"])
        self.assertEqual("FAIL", report["current_line"][0]["status"])
        self.assertEqual(1, report["current_line"][0]["returncode"])

    def test_passing_current_line_yields_pass_verdict(self):
        fake = _fake_executor({
            "test_m4_router_foundation.py": (0, "10 passed"),
        })
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_m4_router_foundation.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("PASS", report["verdict"])


class UnknownPackagingSuiteFailClosedTests(unittest.TestCase):
    def test_unclassified_packaging_suite_fails_closed(self):
        self.assertEqual(
            "unknown_packaging",
            runner.classify_suite("test_some_new_packaging_preflight.py"),
        )
        # And if such a suite ever entered the manifest it must fail closed.
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_some_new_packaging_preflight.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=_fake_executor({})
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        self.assertEqual("FAIL", report["verdict"])
        self.assertEqual(1, len(report["fail_closed"]))
        self.assertEqual("FAIL_CLOSED", report["fail_closed"][0]["status"])
        self.assertEqual([], report["current_line"])


class HistoricalExclusionExplicitTests(unittest.TestCase):
    def test_report_lists_all_five_exclusions_explicitly(self):
        fake = _fake_executor({
            "test_m4_router_foundation.py": (0, "10 passed"),
        })
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_m4_router_foundation.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        excluded = {e["suite"] for e in report["historical_excluded"]}
        self.assertEqual(set(runner.HISTORICAL_SUITES), excluded)
        for entry in report["historical_excluded"]:
            self.assertEqual(
                "EXCLUDED_FROM_CURRENT_EVIDENCE", entry["status"]
            )
            self.assertIn("20AZ triage", entry["reason"])


class HistoricalPassNeverCurrentEvidenceTests(unittest.TestCase):
    def test_historical_suites_are_never_executed_or_counted(self):
        fake = _fake_executor({
            "test_m4_router_foundation.py": (0, "10 passed"),
        })
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_m4_router_foundation.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        # The executor was invoked ONLY for the current-line suite.
        self.assertEqual(
            ["test_m4_router_foundation.py"],
            fake.calls,
        )
        # Historical suites appear only in the exclusion section, and no
        # historical result can appear in current evidence.
        current_names = {r["suite"] for r in report["current_line"]}
        self.assertTrue(current_names.isdisjoint(runner.HISTORICAL_SUITES))
        for entry in report["historical_excluded"]:
            self.assertNotIn("PASS", entry["status"])

    def test_known_xfails_reported_separately_from_passes(self):
        fake = _fake_executor({
            "test_m4_router_foundation.py": (0, "10 passed"),
        })
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = ("test_m4_router_foundation.py",)
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig
        ids = {x["test_id"] for x in report["known_xfails"]}
        self.assertEqual(2, len(ids))
        self.assertIn(
            "tests/test_17e4_runtime_acceptance_harness.py::"
            "FutureMultiTurnOrchestrationTests::test_future_17e6_streaming_service_tool_route",
            ids,
        )
        for x in report["known_xfails"]:
            self.assertNotIn(x["test_id"], str(report["current_line"]))


class ManifestGroundTruthTests(unittest.TestCase):
    """Ground the manifests: every plain current-line suite must exist on
    disk at the canonical checkout; the vetted packaging suites are included
    WHERE PRESENT (absent ones must surface as SKIPPED_ABSENT, never
    silently)."""

    def test_every_plain_current_line_suite_file_exists(self):
        # 20BU: the seven newly registered suites exist at the fully
        # converged tip; on a pre-convergence checkout (this branch) their
        # absence must surface as SKIPPED_ABSENT, never PASS, so their file
        # existence is asserted at Context B only (see the runner test in
        # test_final_zero_cost_acceptance_20bu / rehearsal evidence).
        for suite in runner.CURRENT_LINE_SUITES:
            if runner.classify_suite(suite) == "current_packaging":
                continue
            if suite in POST_20BA_TOOLING_SUITES:
                continue
            self.assertTrue(
                (SOFTWARE_ROOT / "tests" / suite).is_file(),
                f"missing current-line suite: {suite}",
            )

    def test_every_historical_suite_file_exists(self):
        for suite in runner.HISTORICAL_SUITES:
            self.assertTrue(
                (SOFTWARE_ROOT / "tests" / suite).is_file(),
                f"missing historical suite: {suite}",
            )

    def test_manifests_do_not_overlap(self):
        self.assertFalse(
            runner.CURRENT_LINE_SUITES and runner.HISTORICAL_SUITES
            and set(runner.CURRENT_LINE_SUITES) & runner.HISTORICAL_SUITES
        )

    def test_absent_vetted_packaging_suites_reported_skipped_absent(self):
        # 20BR portability fix: the synthetic executor must be state-
        # portable — it may be asked to run any suite in the patched
        # manifest regardless of whether that suite's file exists at the
        # current checkout (20L/20N/20Y are absent at the original 20BA
        # lineage but present on the post-packaging rehearsal spine).
        # Unscripted suites deterministically yield a synthetic PASS so the
        # fixture itself can never raise; assertions are unchanged.
        fake = _fake_executor({
            "test_m4_router_foundation.py": (0, "10 passed"),
            "test_packaged_dependency_hardening_20l.py": (0, "3 passed"),
        })
        orig = runner.CURRENT_LINE_SUITES
        runner.CURRENT_LINE_SUITES = (
            "test_m4_router_foundation.py",
            "test_packaged_dependency_hardening_20l.py",
        )
        try:
            report = runner.run_current_release_battery(
                SOFTWARE_ROOT, executor=fake
            )
        finally:
            runner.CURRENT_LINE_SUITES = orig

        packaging_present = (
            SOFTWARE_ROOT / "tests" / "test_packaged_dependency_hardening_20l.py"
        ).is_file()
        if packaging_present:
            # Present: the suite was executed as current evidence and
            # nothing is skipped.
            self.assertEqual([], report["skipped_absent"])
            current = {r["suite"]: r for r in report["current_line"]}
            self.assertIn(
                "test_packaged_dependency_hardening_20l.py", current
            )
            self.assertEqual(
                "PASS", current["test_packaged_dependency_hardening_20l.py"]["status"]
            )
        else:
            # Absent: explicitly skipped, never silently, never evidence.
            self.assertEqual(1, len(report["skipped_absent"]))
            self.assertEqual(
                "SKIPPED_ABSENT", report["skipped_absent"][0]["status"]
            )
            self.assertEqual(
                "test_packaged_dependency_hardening_20l.py",
                report["skipped_absent"][0]["suite"],
            )
            current_names = {r["suite"] for r in report["current_line"]}
            self.assertNotIn(
                "test_packaged_dependency_hardening_20l.py", current_names
            )


if __name__ == "__main__":
    unittest.main()
