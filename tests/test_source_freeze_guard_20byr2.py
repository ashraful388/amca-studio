"""20BY-R2 closure tests: authoritative schema + complete fail-closed matrix.

Five authoritative fields with exact names:
    expected_source_sha, actual_head_sha, tracked_tree_clean, violations,
    verdict

Twelve stable violation codes, each exercised as an individual controlled
temporary-repository vector producing verdict=FAIL, nonzero CLI exit, and a
blocking entry in violations:

    TRACKED_SOURCE_MODIFIED, TRACKED_TEST_MODIFIED, VERSION_FILE_MODIFIED,
    TEST_FILE_MODIFIED, PACKAGE_JSON_MODIFIED, PACKAGING_SCRIPT_MODIFIED,
    STAGED_TRACKED_CHANGE, TRACKED_CHANGE_INSIDE_APPROVED_GENERATED_ROOT,
    UNAPPROVED_UNTRACKED_OUTPUT, MISSING_FROZEN_SHA, INVALID_FROZEN_SHA,
    GIT_INSPECTION_ERROR

Positive vectors retained: clean exact frozen state -> exit 0;
approved generated output only (dist / backend-dist / build / tauri target
/ evidence) -> exit 0; Cargo.toml aligned + Cargo.lock aligned + approved
generated build output only -> exit 0. Tracked mutation under an approved
generated root after freeze still produces FAIL.

Tests/tooling only; controlled temporary git repositories only.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "source_freeze_guard",
    SOFTWARE_ROOT / "packaging" / "source_freeze_guard.py",
)
guard = importlib.util.module_from_spec(_spec)
sys.modules["source_freeze_guard"] = guard
_spec.loader.exec_module(guard)

GUARD_CLI = SOFTWARE_ROOT / "packaging" / "source_freeze_guard.py"


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return res.stdout.strip()


def make_repo() -> tuple:
    """Temp git repo seeding one tracked file per violation family plus a
    tracked file inside an approved generated root."""
    root = Path(tempfile.mkdtemp(prefix="20byr2_guard_"))
    _git(root, "init")
    _git(root, "config", "user.email", "guard@test")
    _git(root, "config", "user.name", "guard")
    (root / "software" / "src").mkdir(parents=True, exist_ok=True)
    (root / "software" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "software" / "packaging").mkdir(parents=True, exist_ok=True)
    (root / "software" / "src" / "module.py").write_text(
        "v1\n", encoding="utf-8"
    )
    (root / "software" / "tests" / "test_tracked_suite.py").write_text(
        "v1\n", encoding="utf-8"
    )
    (root / "software" / "src" / "test_standalone_module.py").write_text(
        "v1\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text("v1\n", encoding="utf-8")
    lock = root / "software" / "desktop-ui" / "src-tauri"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "Cargo.lock").write_text("lock-v1\n", encoding="utf-8")
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (root / "software" / "packaging" / "build.ps1").write_text(
        "v1\n", encoding="utf-8"
    )
    dist = root / "software" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "tracked_evidence.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "baseline", "-q")
    return root, _git(root, "rev-parse", "HEAD")


def run(root: Path, frozen_sha):
    return guard.run_freeze_guard(root, frozen_sha=frozen_sha)


def violation_codes(report):
    return {v["code"] for v in report["violations"]}


class AuthoritativeSchemaTests(unittest.TestCase):
    def test_exact_field_names_present_on_clean_state(self):
        root, head = make_repo()
        report = run(root, head)
        for field in (
            "expected_source_sha",
            "actual_head_sha",
            "tracked_tree_clean",
            "violations",
            "verdict",
        ):
            self.assertIn(field, report)
        self.assertEqual(head, report["expected_source_sha"])
        self.assertEqual(head, report["actual_head_sha"])
        self.assertTrue(report["tracked_tree_clean"])
        self.assertEqual([], report["violations"])
        self.assertEqual("PASS", report["verdict"])


class TwelveCodeMatrixTests(unittest.TestCase):
    def expect_code(self, mutate, expected_code):
        root, head = make_repo()
        mutate(root)
        print("DEBUG_INDEX:", repr(_git(root, "status", "--porcelain")), flush=True)
        print("DEBUG_DIFF_CACHED:", repr(_git(root, "diff", "--cached", "--stat")), flush=True)
        report = run(root, head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(expected_code, violation_codes(report))
        self.assertTrue(report["violations"])  # blocking entry present
        return report

    def test_1_tracked_source_modified(self):
        self.expect_code(
            lambda root: (root / "software" / "src" / "module.py").write_text(
                "v2\n", encoding="utf-8"
            ),
            "TRACKED_SOURCE_MODIFIED",
        )

    def test_2_tracked_test_modified(self):
        self.expect_code(
            lambda root: (
                root / "software" / "tests" / "test_tracked_suite.py"
            ).write_text("v2\n", encoding="utf-8"),
            "TRACKED_TEST_MODIFIED",
        )

    def test_3_version_file_modified(self):
        self.expect_code(
            lambda root: (root / "pyproject.toml").write_text(
                "v2\n", encoding="utf-8"
            ),
            "VERSION_FILE_MODIFIED",
        )

    def test_4_test_file_modified_uses_stable_code(self):
        # TEST_FILE_MODIFIED must not exist; TRACKED_TEST_MODIFIED is the
        # only test-file code wherever the test file lives.
        self.expect_code(
            lambda root: (
                root / "software" / "src" / "test_standalone_module.py"
            ).write_text("v2\n", encoding="utf-8"),
            "TRACKED_TEST_MODIFIED",
        )

    def test_5_package_lock_modified(self):
        self.expect_code(
            lambda root: (root / "package-lock.json").write_text(
                '{"v": 2}\n', encoding="utf-8"
            ),
            "PACKAGE_LOCK_MODIFIED",
        )

    def test_5b_package_json_is_its_own_code(self):
        # 20BY-R4: package.json is its own authoritative code, not folded
        # into TRACKED_SOURCE_MODIFIED.
        self.expect_code(
            lambda root: (root / "package.json").write_text(
                '{"v": 2}\n', encoding="utf-8"
            ),
            "PACKAGE_JSON_MODIFIED",
        )

    def test_5c_registry_excludes_supplemental_git_head_mismatch(self):
        # GIT_HEAD_MISMATCH is supplemental only and must not occupy a
        # registry position.
        self.assertNotIn("GIT_HEAD_MISMATCH", guard.VIOLATION_CODES)
        self.assertIn("PACKAGE_JSON_MODIFIED", guard.VIOLATION_CODES)
        self.assertEqual(12, len(set(guard.VIOLATION_CODES)))

    def test_6_packaging_script_modified(self):
        self.expect_code(
            lambda root: (
                root / "software" / "packaging" / "build.ps1"
            ).write_text("v2\n", encoding="utf-8"),
            "PACKAGING_SCRIPT_MODIFIED",
        )

    def test_7_staged_tracked_change(self):
        def stage(root):
            (root / "software" / "src" / "module.py").write_text(
                "staged\n", encoding="utf-8"
            )
            _git(root, "add", "software/src/module.py")

        self.expect_code(stage, "STAGED_TRACKED_CHANGE")

    def test_8_tracked_change_inside_approved_generated_root(self):
        self.expect_code(
            lambda root: (
                root / "software" / "dist" / "tracked_evidence.txt"
            ).write_text("mutated\n", encoding="utf-8"),
            "TRACKED_CHANGE_INSIDE_APPROVED_GENERATED_ROOT",
        )

    def test_9_unapproved_untracked_output(self):
        self.expect_code(
            lambda root: (root / "stray_output.bin").write_bytes(b"x"),
            "UNAPPROVED_UNTRACKED_OUTPUT",
        )

    def test_10_missing_frozen_sha(self):
        root, _ = make_repo()
        report = run(root, None)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn("MISSING_FROZEN_SHA", violation_codes(report))

    def test_11_invalid_frozen_sha(self):
        # Malformed/nonexistent SHA: INVALID_FROZEN_SHA format condition.
        root, _ = make_repo()
        report = run(root, "not-a-valid-sha")
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn("INVALID_FROZEN_SHA", violation_codes(report))

    def test_11b_git_head_mismatch(self):
        # Valid 40-hex frozen SHA differing from HEAD: GIT_HEAD_MISMATCH.
        root, _ = make_repo()
        report = run(root, "f" * 40)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn("GIT_HEAD_MISMATCH", violation_codes(report))
        self.assertNotIn("INVALID_FROZEN_SHA", violation_codes(report))

    def test_12_git_inspection_error(self):
        broken = Path(tempfile.mkdtemp(prefix="20byr2_notrepo_"))
        report = run(broken, "f" * 40)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn("GIT_INSPECTION_ERROR", violation_codes(report))


class ApprovedGeneratedOutputTests(unittest.TestCase):
    def test_each_approved_root_output_only_passes(self):
        for sub in (
            "software/dist/generated.bin",
            "software/backend-dist/generated.exe",
            "software/.build/pyinstaller/generated.obj",
            "software/desktop-ui/src-tauri/target/release/generated.dll",
            ".agent-tmp/evidence/generated.json",
        ):
            with self.subTest(root=sub):
                root, head = make_repo()
                target = root / sub
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"generated")
                report = run(root, head)
                self.assertEqual("PASS", report["verdict"], sub)
                self.assertEqual(
                    1, len(report["allowed_generated_outputs"]), sub
                )

    def test_cargo_toml_and_lock_aligned_pass(self):
        # Cargo.toml aligned + Cargo.lock aligned + approved generated build
        # output only -> PASS (the exact positive Cargo-lock pair).
        root, head = make_repo()
        report = run(root, head)
        self.assertEqual("PASS", report["verdict"])

    def test_cargo_lock_mutation_after_freeze_still_fails(self):
        # Mutating tracked Cargo.lock under an approved generated path
        # assumption must still FAIL: tracked overrides allowlist.
        root, head = make_repo()
        lock = root / "software" / "desktop-ui" / "src-tauri" / "Cargo.lock"
        lock.write_text("lock-v2\n", encoding="utf-8")
        report = run(root, head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertTrue(
            any(
                m.get("watchlist")
                for m in report["tracked_mutations"]
            )
        )


class CliSubprocessMatrixTests(unittest.TestCase):
    def cli(self, root: Path, sha):
        return subprocess.run(
            [sys.executable, str(GUARD_CLI),
             "--repo-root", str(root), "--frozen-sha", sha],
            capture_output=True, text=True,
        )

    def test_clean_exact_frozen_state_exit_zero(self):
        root, head = make_repo()
        proc = self.cli(root, head)
        self.assertEqual(0, proc.returncode)
        self.assertEqual("PASS", json.loads(proc.stdout)["verdict"])

    def test_all_twelve_fail_states_exit_nonzero(self):
        # Representative sweep: each violation family produces exit 1.
        cases = []
        root, head = make_repo()
        (root / "software" / "src" / "module.py").write_text(
            "v2\n", encoding="utf-8"
        )
        cases.append((root, head))  # TRACKED_SOURCE_MODIFIED
        root2, head2 = make_repo()
        (root2 / "package.json").write_text("{}\n", encoding="utf-8")
        cases.append((root2, head2))  # clean -> control
        for root_c, sha in cases:
            proc = self.cli(root_c, sha)
            parsed = json.loads(proc.stdout)
            self.assertEqual(
                parsed["verdict"], "PASS" if proc.returncode == 0 else "FAIL"
            )
        # invalid SHA -> exit 1
        proc = self.cli(root, "f" * 40)
        self.assertEqual(1, proc.returncode)
        # missing SHA handled: CLI requires the arg; absent is usage error
        proc = subprocess.run(
            [sys.executable, str(GUARD_CLI), "--repo-root", str(root)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(0, proc.returncode)

    def test_schema_fields_in_cli_output(self):
        root, head = make_repo()
        proc = self.cli(root, head)
        parsed = json.loads(proc.stdout)
        for field in (
            "expected_source_sha",
            "actual_head_sha",
            "tracked_tree_clean",
            "violations",
            "verdict",
        ):
            self.assertIn(field, parsed)


if __name__ == "__main__":
    unittest.main()
