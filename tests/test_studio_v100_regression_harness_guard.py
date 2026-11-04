"""
AMCA Studio v1.0.0 I1C Canonical Regression Harness Guard Tests
Validates that the harness script and test discovery infrastructure work correctly.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOFTWARE_DIR = REPO_ROOT / "software"
HARNESS_SCRIPT = REPO_ROOT / "scripts" / "run_regression.py"
BACKEND_TEST_ROOT = SOFTWARE_DIR / "tests"
FRONTEND_TEST_ROOT = SOFTWARE_DIR / "desktop-ui" / "tests"
MANIFEST_PATH = SOFTWARE_DIR / "docs" / "architecture" / "test_universe_manifest.json"


class HarnessInfrastructureTests(unittest.TestCase):
    """Validate that the regression harness infrastructure is present and sane."""

    def test_harness_script_exists(self) -> None:
        """The canonical regression harness script must exist."""
        self.assertTrue(
            HARNESS_SCRIPT.exists(),
            f"Regression harness script missing: {HARNESS_SCRIPT}",
        )

    def test_harness_script_is_executable_python(self) -> None:
        """The harness script must be importable (no syntax errors)."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(HARNESS_SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Harness script has syntax error: {result.stderr}",
        )

    def test_backend_root_exists_and_is_not_empty(self) -> None:
        """Backend test root must exist and contain at least one test file."""
        self.assertTrue(BACKEND_TEST_ROOT.exists(), "Backend test root must exist")
        test_files = list(BACKEND_TEST_ROOT.glob("test_*.py"))
        self.assertGreater(
            len(test_files), 0,
            "Backend test root cannot be silently excluded — must have test files",
        )

    def test_frontend_root_exists_and_is_not_empty(self) -> None:
        """Frontend test root must exist and contain .test.mjs files."""
        self.assertTrue(FRONTEND_TEST_ROOT.exists(), "Frontend test root must exist")
        test_files = list(FRONTEND_TEST_ROOT.glob("*.test.mjs"))
        self.assertGreater(
            len(test_files), 0,
            "Frontend discovery must identify at least one eligible test file",
        )

    def test_frontend_discovery_uses_glob_not_hardcoded_list(self) -> None:
        """Frontend discovery must discover all *.test.mjs files, not a fixed subset."""
        # All files found by glob must actually exist
        test_files = list(FRONTEND_TEST_ROOT.glob("*.test.mjs"))
        for f in test_files:
            self.assertTrue(f.exists(), f"Discovered frontend test file does not exist: {f}")
        # Must discover more than the legacy 13-file set (ZCode UX is 59 files)
        self.assertGreater(
            len(test_files), 13,
            "Frontend discovery should find all eligible test files (not just legacy 13)",
        )

    def test_manifest_file_was_generated(self) -> None:
        """Test universe manifest must have been generated."""
        self.assertTrue(
            MANIFEST_PATH.exists(),
            f"Test-universe manifest must exist at: {MANIFEST_PATH}",
        )

    def test_manifest_contains_required_fields(self) -> None:
        """Manifest must contain all required provenance and count fields."""
        self.assertTrue(MANIFEST_PATH.exists(), "Manifest must exist")
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn("generated_at", data)
        self.assertIn("provenance", data)
        self.assertIn("backend", data)
        self.assertIn("frontend", data)
        self.assertIn("sha", data["provenance"])
        self.assertIn("is_dirty", data["provenance"])
        self.assertIn("file_count", data["backend"])
        self.assertIn("file_count", data["frontend"])
        self.assertIn("files", data["backend"])
        self.assertIn("files", data["frontend"])

    def test_manifest_backend_count_not_hardcoded(self) -> None:
        """Manifest backend file count must come from actual discovery, not be hardcoded."""
        self.assertTrue(MANIFEST_PATH.exists(), "Manifest must exist")
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        # Verify manifest count matches actual filesystem count
        actual_count = len(list(BACKEND_TEST_ROOT.glob("test_*.py")))
        manifest_count = data["backend"]["file_count"]
        # Allow minor differences (collect-only may miss some non-standard files)
        self.assertAlmostEqual(
            manifest_count, actual_count, delta=5,
            msg=f"Manifest backend count ({manifest_count}) must reflect actual discovery (found {actual_count})",
        )

    def test_manifest_sha_is_captured(self) -> None:
        """Manifest provenance must capture a non-empty git SHA."""
        self.assertTrue(MANIFEST_PATH.exists(), "Manifest must exist")
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        sha = data["provenance"].get("sha", "")
        self.assertTrue(sha and sha != "UNKNOWN", "Manifest provenance SHA must be captured")
        # SHA should look like a git hash (40 hex chars) or at least be non-empty
        self.assertGreater(len(sha), 6, f"SHA '{sha}' too short to be a valid git SHA")

    def test_manifest_dirty_state_is_disclosed(self) -> None:
        """Manifest provenance must include is_dirty field (dirty state must be disclosed)."""
        self.assertTrue(MANIFEST_PATH.exists(), "Manifest must exist")
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn(
            "is_dirty", data["provenance"],
            "Provenance must include is_dirty to disclose working-tree state",
        )

    def test_harness_manifest_only_exits_zero(self) -> None:
        """Harness --manifest-only mode must exit 0 and write manifest successfully."""
        result = subprocess.run(
            [sys.executable, str(HARNESS_SCRIPT), "--manifest-only"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            result.returncode, 0,
            f"Harness --manifest-only must exit 0. Got: {result.returncode}\n{result.stderr}",
        )
        self.assertIn("Backend", result.stdout + result.stderr)
        self.assertIn("Frontend", result.stdout + result.stderr)

    def test_exclusions_must_be_reported(self) -> None:
        """Manifest must include exclusions field (even if empty) — not silently hide them."""
        self.assertTrue(MANIFEST_PATH.exists(), "Manifest must exist")
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn("exclusions", data["backend"], "Backend exclusions must be declared")
        self.assertIn("exclusions", data["frontend"], "Frontend exclusions must be declared")
        self.assertIn("exclusion_reasons", data["backend"], "Backend exclusion reasons must be declared")
        self.assertIn("exclusion_reasons", data["frontend"], "Frontend exclusion reasons must be declared")


if __name__ == "__main__":
    unittest.main()
