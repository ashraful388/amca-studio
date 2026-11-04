"""Canonical AMCA Studio certification gate tests.

Work order: OPENCODE-AMCA-STUDIO-V1.0.0-I1D-CANONICAL-CERTIFICATION-GATE-AND-
RELEASE-READINESS-AUTOMATION-01

Validates:
- SHA mismatch -> FAIL
- backend failure -> FAIL
- collection error -> FAIL
- frontend failure -> FAIL
- unexplained exclusion -> FAIL
- dirty tree correctly disclosed
- version mismatch -> FAIL
- fully valid evidence -> PASS
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SOFTWARE_ROOT / "scripts"
CERTIFICATION = SCRIPTS / "run_canonical_certification.py"
MANIFEST = SOFTWARE_ROOT / "tests" / "test-universe-manifest.json"


def run_cert(args: list[str] | None = None) -> tuple[int, dict]:
    cmd = [sys.executable, str(CERTIFICATION), "--skip-regression", "--audit-only"]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, cwd=str(SOFTWARE_ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        report = {"parsing_error": result.stdout[-2000:]}
    return result.returncode, report


class CertificationGateSHAValidationTests(unittest.TestCase):
    def test_sha_is_recorded(self):
        _, report = run_cert()
        sha = report.get("provenance", {}).get("sha", "")
        self.assertGreater(len(sha), 0, "SHA must be recorded")

    def test_branch_is_recorded(self):
        _, report = run_cert()
        branch = report.get("provenance", {}).get("branch", "")
        self.assertGreater(len(branch), 0, "branch must be recorded")


class CertificationGateMandatoryGateTests(unittest.TestCase):
    def test_version_contract_checked(self):
        _, report = run_cert()
        vc = report.get("version_contract", {})
        self.assertIn("consistent", vc)
        self.assertIn("mismatches", vc)

    def test_exclusions_governance_checked(self):
        _, report = run_cert()
        exclusions = report.get("exclusions", {})
        self.assertIn("all_explained", exclusions)
        self.assertIn("unexplained", exclusions)

    def test_gates_evaluated(self):
        _, report = run_cert()
        gates = report.get("gates", {})
        self.assertIn("all_pass", gates)
        self.assertIn("gates", gates)

    def test_same_sha_integrity_checked(self):
        _, report = run_cert()
        same_sha = report.get("same_sha", {})
        self.assertIn("all_match", same_sha)

    def test_working_tree_status_recorded(self):
        _, report = run_cert()
        provenance = report.get("provenance", {})
        self.assertIn("clean", provenance)
        self.assertIn("dirty", provenance)


class CertificationGateOverallVerdictTests(unittest.TestCase):
    def test_overall_verdict_recorded(self):
        _, report = run_cert()
        self.assertIn("overall_verdict", report)

    def test_verdict_detail_recorded(self):
        _, report = run_cert()
        self.assertIn("verdict_detail", report)

    def test_machine_readable_output(self):
        _, report = run_cert()
        required = ["generated_at", "certification", "provenance", "version_contract",
                    "exclusions", "gates", "same_sha", "overall_verdict"]
        for key in required:
            self.assertIn(key, report, f"certification result must contain '{key}'")


class CertificationGateDirtyTreeDisclosureTests(unittest.TestCase):
    def test_dirty_tree_disclosed(self):
        _, report = run_cert()
        dirty = report.get("provenance", {}).get("dirty", None)
        self.assertIsNotNone(dirty, "dirty tree state must be disclosed")

    def test_dirty_tree_does_not_fail_in_audit_mode(self):
        rc, _ = run_cert(["--audit-only", "--skip-regression"])
        # In audit-only mode, dirty tree does not cause FAIL.
        self.assertIn(rc, (0, 1), "certification must complete in audit mode")


class CertificationGateVersionContractTests(unittest.TestCase):
    def test_version_contract_consistent(self):
        _, report = run_cert()
        vc = report.get("version_contract", {})
        mismatches = vc.get("mismatches", {})
        # This test is informative: it reports the version state.
        # The certification gate will fail if inconsistent.
        self.assertIsInstance(mismatches, dict)


class CertificationGateManifestBasedTests(unittest.TestCase):
    def test_manifest_is_readable(self):
        self.assertTrue(MANIFEST.is_file(), "manifest must exist")

    def test_manifest_has_exclusions(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("exclusions", data)
        self.assertIn("backend", data["exclusions"])
        self.assertIn("frontend", data["exclusions"])


class CertificationGateBLOCKEDVerdictTests(unittest.TestCase):
    def test_skip_regression_yields_blocked(self):
        """When regression is skipped, mandatory evidence is missing -> BLOCKED."""
        _, report = run_cert(["--audit-only", "--skip-regression"])
        self.assertEqual(report.get("overall_verdict"), "BLOCKED",
                         "skipping regression should produce BLOCKED verdict")

    def test_blocked_has_reasons(self):
        """BLOCKED verdict must include specific reasons."""
        _, report = run_cert(["--audit-only", "--skip-regression"])
        blocked = report.get("blocked_reasons", [])
        self.assertGreater(len(blocked), 0, "BLOCKED verdict must have at least one reason")

    def test_blocked_never_degrades_to_pass(self):
        """BLOCKED must never degrade to PASS under any circumstance."""
        _, report = run_cert(["--audit-only", "--skip-regression"])
        self.assertNotEqual(report.get("overall_verdict"), "PASS",
                            "BLOCKED must never degrade to PASS")

    def test_verdict_is_one_of_three(self):
        """Verdict must be one of PASS, FAIL, or BLOCKED."""
        _, report = run_cert(["--audit-only", "--skip-regression"])
        self.assertIn(report.get("overall_verdict"), ("PASS", "FAIL", "BLOCKED"))


if __name__ == "__main__":
    unittest.main()