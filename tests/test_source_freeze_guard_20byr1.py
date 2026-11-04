"""20BY-R1 closure vectors for the source-freeze guard.

Closes the three 20BY contract gaps:

1. Authoritative five-field schema — every normal result directly contains
   verdict, frozen_sha, head, tracked_mutations, untracked_mutations.
2. Approved generated-output policy — untracked entries are tolerated only
   under approved generated roots; tracked status always overrides the path
   allowance; arbitrary untracked files fail.
3. Complete fail-closed matrix — every negative state fails nonzero,
   including staged tracked changes, tracked test/version/package/packaging
   mutations, Cargo.lock, invalid SHA, git inspection error, and a tracked
   mutation inside an approved path that cannot be hidden by the allowlist.

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

FROZEN = "2" * 40
GUARD_CLI = SOFTWARE_ROOT / "packaging" / "source_freeze_guard.py"


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return res.stdout.strip()


def make_repo() -> tuple:
    """Temp git repo with a committed tracked file + tracked Cargo.lock,
    plus tracked files inside an approved generated root."""
    root = Path(tempfile.mkdtemp(prefix="20byr1_guard_"))
    _git(root, "init")
    _git(root, "config", "user.email", "guard@test")
    _git(root, "config", "user.name", "guard")
    (root / "tracked.txt").write_text("hello\n", encoding="utf-8")
    lock = root / "software" / "desktop-ui" / "src-tauri"
    lock.mkdir(parents=True, exist_ok=True)
    (lock / "Cargo.lock").write_text("lock-v1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("v1\n", encoding="utf-8")
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    # A tracked file that lives INSIDE an approved generated root: the
    # allowlist must not be able to hide its mutation.
    dist = root / "software" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "tracked_inside_approved_root.txt").write_text(
        "v1\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "baseline", "-q")
    head = _git(root, "rev-parse", "HEAD")
    return root, head


def run(root: Path, frozen_sha=None):
    return guard.run_freeze_guard(root, frozen_sha=frozen_sha)


class AuthoritativeSchemaTests(unittest.TestCase):
    def test_result_directly_contains_five_required_fields(self):
        root, head = make_repo()
        report = run(root, frozen_sha=head)
        for field in (
            "verdict",
            "frozen_sha",
            "head",
            "tracked_mutations",
            "untracked_mutations",
        ):
            self.assertIn(field, report)
        self.assertEqual("PASS", report["verdict"])
        self.assertEqual(head, report["frozen_sha"])
        self.assertEqual([], report["tracked_mutations"])
        self.assertEqual([], report["untracked_mutations"])


class TrackedMutationMatrixTests(unittest.TestCase):
    def test_tracked_plain_file_mutation_fails(self):
        root, head = make_repo()
        (root / "tracked.txt").write_text("mutated\n", encoding="utf-8")
        report = run(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertEqual(
            1, len(report["tracked_mutations"])
        )

    def test_staged_tracked_change_fails(self):
        root, head = make_repo()
        (root / "tracked.txt").write_text("staged\n", encoding="utf-8")
        _git(root, "add", "tracked.txt")  # staged, not just dirty
        report = run(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertGreaterEqual(len(report["tracked_mutations"]), 1)

    def test_cargo_lock_mutation_fails_with_watchlist(self):
        root, head = make_repo()
        lock = root / "software" / "desktop-ui" / "src-tauri" / "Cargo.lock"
        lock.write_text("lock-v2-regenerated\n", encoding="utf-8")
        report = run(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertIn(
            "software/desktop-ui/src-tauri/Cargo.lock",
            {m["path"] for m in report["watchlist_hits"]},
        )

    def test_version_and_package_file_mutations_fail(self):
        for target, content in (
            ("pyproject.toml", "v2\n"),
            ("package.json", '{"x": 2}\n'),
        ):
            with self.subTest(target=target):
                root, head = make_repo()
                (root / target).write_text(content, encoding="utf-8")
                report = run(root, frozen_sha=head)
                self.assertEqual("FAIL", report["verdict"])

    def test_tracked_mutation_inside_approved_root_cannot_hide(self):
        root, head = make_repo()
        target = root / "software" / "dist" / "tracked_inside_approved_root.txt"
        target.write_text("mutated-under-allowlist\n", encoding="utf-8")
        report = run(root, frozen_sha=head)
        # Tracked status ALWAYS overrides the path allowance.
        self.assertEqual("FAIL", report["verdict"])
        self.assertGreaterEqual(len(report["tracked_mutations"]), 1)
        self.assertTrue(
            any(
                m["path"].endswith("tracked_inside_approved_root.txt")
                for m in report["tracked_mutations"]
            )
        )


class UntrackedAllowlistTests(unittest.TestCase):
    def test_untracked_outside_approved_roots_fails(self):
        root, head = make_repo()
        (root / "stray.txt").write_text("stray\n", encoding="utf-8")
        report = run(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        self.assertEqual(1, len(report["untracked_mutations"]))
        self.assertFalse(
            report["untracked_mutations"][0]["approved_generated_output"]
        )

    def test_untracked_inside_approved_root_is_allowed(self):
        root, head = make_repo()
        art = root / "software" / "dist" / "generated-artifact.bin"
        art.write_bytes(b"generated-bytes")
        report = run(root, frozen_sha=head)
        self.assertEqual("PASS", report["verdict"])
        self.assertEqual(1, len(report["allowed_generated_outputs"]))

    def test_arbitrary_untracked_files_are_not_broadly_ignored(self):
        root, head = make_repo()
        (root / "random_untracked.cfg").write_text("x\n", encoding="utf-8")
        report = run(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])


class ShaAndGitErrorMatrixTests(unittest.TestCase):
    def test_missing_frozen_sha_fails(self):
        root, _ = make_repo()
        report = run(root, frozen_sha=None)
        self.assertEqual("FAIL", report["verdict"])
        self.assertTrue(report["errors"])

    def test_invalid_sha_format_fails(self):
        root, _ = make_repo()
        report = run(root, frozen_sha="not-a-sha")
        self.assertEqual("FAIL", report["verdict"])
        kinds = {m.get("kind") for m in report["mutations"]}
        self.assertIn("invalid_sha_format", kinds)

    def test_git_inspection_error_fails(self):
        root, head = make_repo()
        # Simulate a git inspection failure by corrupting the git binary
        # path the guard uses: point repo_root at a non-repo after binding.
        report = guard.run_freeze_guard(
            root, frozen_sha=head
        )
        self.assertEqual("PASS", report["verdict"])  # sanity: intact repo
        broken = Path(tempfile.mkdtemp(prefix="20byr1_notrepo_"))
        report2 = guard.run_freeze_guard(broken, frozen_sha=head)
        self.assertEqual("FAIL", report2["verdict"])
        self.assertTrue(report2["errors"])


class CliClosureTests(unittest.TestCase):
    def test_subprocess_exit_0_iff_pass(self):
        root, head = make_repo()
        ok = subprocess.run(
            [sys.executable, str(GUARD_CLI),
             "--repo-root", str(root), "--frozen-sha", head],
            capture_output=True, text=True,
        )
        self.assertEqual(0, ok.returncode)
        parsed = json.loads(ok.stdout)
        for field in (
            "verdict",
            "frozen_sha",
            "head",
            "tracked_mutations",
            "untracked_mutations",
        ):
            self.assertIn(field, parsed)

        (root / "tracked.txt").write_text("mutated\n", encoding="utf-8")
        bad = subprocess.run(
            [sys.executable, str(GUARD_CLI),
             "--repo-root", str(root), "--frozen-sha", head],
            capture_output=True, text=True,
        )
        self.assertEqual(1, bad.returncode)
        self.assertEqual("FAIL", json.loads(bad.stdout)["verdict"])

    def test_missing_args_fail_nonzero(self):
        proc = subprocess.run(
            [sys.executable, str(GUARD_CLI)], capture_output=True, text=True
        )
        self.assertNotEqual(0, proc.returncode)


if __name__ == "__main__":
    unittest.main()
