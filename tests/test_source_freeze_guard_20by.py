"""20BY focused tests for the source-freeze guard.

Each vector runs against a controlled temporary git repository so no real
repository state is touched and no real packaging build is executed:

1. clean tree + matching frozen SHA -> PASS (exit 0);
2. HEAD != frozen SHA -> FAIL with sha_mismatch;
3. missing frozen SHA -> FAIL; the guard NEVER falls back to HEAD;
4. tracked Cargo.lock mutation (the 20BX risk) -> FAIL with the watchlist
   path surfaced;
5. untracked entry -> FAIL classified as untracked;
6. CLI semantics: exit 0 iff PASS; usage errors exit nonzero.

Release-tooling only.
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

FROZEN = "1" * 40


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return res.stdout.strip()


def make_repo(with_lockfile: bool = False) -> Path:
    """Controlled temporary git repo with one committed file."""
    root = Path(tempfile.mkdtemp(prefix="20by_guard_"))
    _git(root, "init")
    _git(root, "config", "user.email", "guard@test")
    _git(root, "config", "user.name", "guard")
    (root / "tracked.txt").write_text("hello\n", encoding="utf-8")
    if with_lockfile:
        lock = root / "software" / "desktop-ui" / "src-tauri"
        lock.mkdir(parents=True, exist_ok=True)
        (lock / "Cargo.lock").write_text(
            '# generated\n[[package]]\nname="amca-desktop"\n',
            encoding="utf-8",
        )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "baseline", "-q")
    return root


class CleanTreeTests(unittest.TestCase):
    def test_clean_tree_and_matching_sha_pass(self):
        root = make_repo()
        head = _git(root, "rev-parse", "HEAD")
        report = guard.run_freeze_guard(root, frozen_sha=head)
        self.assertEqual("PASS", report["verdict"])
        self.assertTrue(report["clean"])
        self.assertEqual(head, report["head"])
        self.assertEqual(head, report["frozen_sha"])


class ShaBindingTests(unittest.TestCase):
    def test_head_mismatch_is_sha_mismatch_fail(self):
        root = make_repo()
        report = guard.run_freeze_guard(root, frozen_sha=FROZEN)
        self.assertEqual("FAIL", report["verdict"])
        kinds = {m["kind"] for m in report["mutations"]}
        self.assertIn("head_drift", kinds)

    def test_missing_frozen_sha_never_falls_back_to_head(self):
        root = make_repo()
        # Even though HEAD is a valid SHA, the guard must refuse without an
        # explicit frozen SHA.
        report = guard.run_freeze_guard(root, frozen_sha=None)
        self.assertEqual("FAIL", report["verdict"])
        self.assertTrue(any("never falls back" in e for e in report["errors"]))
        self.assertIsNone(report["frozen_sha"])

    def test_sha_binding_is_machine_readable(self):
        root = make_repo()
        head = _git(root, "rev-parse", "HEAD")
        report = guard.run_freeze_guard(root, frozen_sha=head)
        self.assertIn("frozen_sha", report)
        self.assertIn("head", report)
        self.assertIn("verdict", report)
        self.assertIn("mutations", report)


class CargoLockMutationVectorTests(unittest.TestCase):
    def test_tracked_cargo_lock_mutation_is_caught(self):
        # 20BX risk simulation: the official build rewrites the tracked
        # Cargo.lock after the freeze. The guard must fail and surface the
        # exact watchlist path.
        root = make_repo(with_lockfile=True)
        head = _git(root, "rev-parse", "HEAD")
        lock = (
            root / "software" / "desktop-ui" / "src-tauri" / "Cargo.lock"
        )
        lock.write_text(
            '# regenerated during official build\n[[package]]\nname="amca-desktop"\nversion="1.1.1"\n',
            encoding="utf-8",
        )
        report = guard.run_freeze_guard(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        paths = {m["path"] for m in report["mutations"]}
        self.assertIn("software/desktop-ui/src-tauri/Cargo.lock", paths)
        self.assertTrue(
            any(m.get("watchlist") for m in report["mutations"])
        )


class UntrackedAndCliSemanticsTests(unittest.TestCase):
    def test_untracked_entry_fails_classified_as_untracked(self):
        root = make_repo()
        head = _git(root, "rev-parse", "HEAD")
        (root / "stray.txt").write_text("stray\n", encoding="utf-8")
        report = guard.run_freeze_guard(root, frozen_sha=head)
        self.assertEqual("FAIL", report["verdict"])
        kinds = {m["kind"] for m in report["mutations"]}
        self.assertIn("untracked", kinds)

    def test_cli_exit_0_iff_pass(self):
        root = make_repo()
        head = _git(root, "rev-parse", "HEAD")
        ok = subprocess.run(
            [sys.executable, str(SOFTWARE_ROOT / "packaging" / "source_freeze_guard.py"),
             "--repo-root", str(root), "--frozen-sha", head],
            capture_output=True, text=True,
        )
        self.assertEqual(0, ok.returncode)
        (root / "tracked.txt").write_text("mutated\n", encoding="utf-8")
        bad = subprocess.run(
            [sys.executable, str(SOFTWARE_ROOT / "packaging" / "source_freeze_guard.py"),
             "--repo-root", str(root), "--frozen-sha", head],
            capture_output=True, text=True,
        )
        self.assertEqual(1, bad.returncode)
        self.assertEqual("FAIL", json.loads(bad.stdout)["verdict"])


if __name__ == "__main__":
    unittest.main()
