"""v1.1.9: a governed write must be able to create a file, and stat must tell
the truth about absence.

Found by the physical GUI acceptance run (installed build 5ebd3f06): with the
owner's live grant -- which carries BOTH ``create`` and ``write`` -- a model
call to ``filesystem.write`` for a not-yet-existing ``plan.txt`` resolved to
capability ``write`` instead of ``create``.  That skipped the direct-create
branch in ``governed_chat`` and fell into the exact-scope approval path, which
requires the target to exist, so the call died with
``FileNotFoundError [WinError 2]``.  The models read that as a write failure,
raised a blocker, the sync gate failed the run, and the user saw "That operation
was not executed or could not be verified."  with no file on disk.

Two properties are pinned here:

1. For a not-yet-existing target the CREATE capability governs; for an existing
   target WRITE still governs, which keeps the approval + version-backup
   requirement for overwriting existing content untouched.
2. ``stat`` is an existence query: a missing target is an answer (``exists:
   false``), not a failed tool event.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.chat_history import ConversationStore
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)


class TestNewFileWriteCapabilityResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v119-newfile-"))
        self.granted = self.tmp / "granted"
        self.granted.mkdir()
        self.store = LocalPermissionStore(self.tmp / "perm.sqlite3")
        self.pid = "desktop-v119newfile"
        ConversationStore(self.tmp / "perm.sqlite3").create(
            title="v1.1.9 new-file write", conversation_id="v119newfile"
        )
        self.store.set_profile(
            project_id=self.pid,
            profile=PermissionProfile.FULL_COMPUTER,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.tmp / "state")

    def _grant(self, capabilities, reason="v1.1.9 grant"):
        return self.store.create_grant(
            project_id=self.pid,
            root_path=str(self.granted),
            capabilities=capabilities,
            reason=reason,
            ttl_seconds=3600,
        )

    def _events(self, action: str):
        with sqlite3.connect(self.tmp / "perm.sqlite3") as connection:
            return connection.execute(
                "SELECT action, outcome, target, detail_json FROM local_tool_events "
                "WHERE action = ? ORDER BY created_at",
                (action,),
            ).fetchall()

    # -- the defect ------------------------------------------------------

    def test_both_capabilities_new_target_resolves_to_create(self) -> None:
        """The live shape: one grant holding create AND write."""
        self._grant([FileCapability.CREATE, FileCapability.WRITE])
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="plan.txt"
        )
        self.assertTrue(resolution["matched"], resolution)
        self.assertEqual(resolution["capability"], "create")
        self.assertFalse(resolution["target_exists"])

    def test_both_capabilities_new_target_executes_and_creates_the_file(self) -> None:
        self._grant([FileCapability.CREATE, FileCapability.WRITE])
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="plan.txt"
        )
        result = self.executor.execute(
            project_id=self.pid,
            grant_id=resolution["grant_id"],
            action=resolution["capability"],
            path=resolution["relative_path"],
            content="# Plan\nstep 1\n",
        )
        self.assertTrue(result["created"])
        self.assertEqual(
            (self.granted / "plan.txt").read_text(encoding="utf-8"), "# Plan\nstep 1\n"
        )
        # The audit trail must name the capability that actually governed it.
        # The row is named for the REQUESTED action, with the governing
        # capability recorded in its detail.
        resolved = self._events("write_grant_resolved")
        self.assertTrue(resolved)
        detail = json.loads(resolved[-1][3])
        self.assertEqual(detail["matched_capability"], "create")
        self.assertFalse(detail["target_exists"])
        # And the execution itself is audited as a success.
        self.assertEqual(
            [row[1] for row in self._events("create")], ["succeeded"]
        )

    def test_absolute_new_target_resolves_to_create(self) -> None:
        self._grant([FileCapability.CREATE, FileCapability.WRITE])
        absolute = self.granted / "nested-name.txt"
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path=str(absolute)
        )
        self.assertEqual(resolution["capability"], "create")
        self.assertEqual(resolution["relative_path"], "nested-name.txt")

    # -- the security property that must not move -----------------------

    def test_existing_target_still_resolves_to_write(self) -> None:
        self._grant([FileCapability.CREATE, FileCapability.WRITE])
        (self.granted / "existing.txt").write_text("v1", encoding="utf-8")
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="existing.txt"
        )
        self.assertEqual(resolution["capability"], "write")
        self.assertTrue(resolution["target_exists"])

    def test_existing_target_overwrite_still_requires_exact_scope_approval(self) -> None:
        """Overwriting existing content must keep demanding its approval."""
        grant = self._grant([FileCapability.CREATE, FileCapability.WRITE])
        target = self.granted / "existing.txt"
        target.write_text("v1", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=grant.grant_id,
                action="write",
                path="existing.txt",
                content="v2",
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "v1")

    def test_write_only_grant_cannot_create_a_new_file(self) -> None:
        """A grant without CREATE does not authorize creating files."""
        grant = self._grant([FileCapability.WRITE])
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="nope.txt"
        )
        # Unchanged legacy decision: WRITE governs, and the target does not
        # exist, so the create branch is not available to the caller.
        self.assertEqual(resolution["capability"], "write")
        self.assertFalse(resolution["target_exists"])
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=grant.grant_id,
                action="create",
                path="nope.txt",
                content="x",
            )
        self.assertFalse((self.granted / "nope.txt").exists())

    def test_create_only_grant_new_target_still_resolves_to_create(self) -> None:
        self._grant([FileCapability.CREATE])
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="new.txt"
        )
        self.assertEqual(resolution["capability"], "create")

    def test_create_grant_still_cannot_overwrite_existing_file(self) -> None:
        self._grant([FileCapability.CREATE])
        (self.granted / "existing.txt").write_text("v1", encoding="utf-8")
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="existing.txt"
        )
        self.assertFalse(resolution["matched"])

    def test_out_of_scope_target_is_still_refused(self) -> None:
        self._grant([FileCapability.CREATE, FileCapability.WRITE])
        outside = self.tmp / "outside.txt"
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path=str(outside)
        )
        self.assertFalse(resolution["matched"])
        self.assertFalse(outside.exists())


class TestStatReportsAbsenceTruthfully(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v119-stat-"))
        self.granted = self.tmp / "granted"
        self.granted.mkdir()
        self.store = LocalPermissionStore(self.tmp / "perm.sqlite3")
        self.pid = "desktop-v119stat"
        ConversationStore(self.tmp / "perm.sqlite3").create(
            title="v1.1.9 stat truth", conversation_id="v119stat"
        )
        self.store.set_profile(
            project_id=self.pid,
            profile=PermissionProfile.FULL_COMPUTER,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.tmp / "state")
        self.grant = self.store.create_grant(
            project_id=self.pid,
            root_path=str(self.granted),
            capabilities=[FileCapability.LIST, FileCapability.READ, FileCapability.STAT],
            reason="stat truth grant",
            ttl_seconds=3600,
        )

    def _stat(self, path: str):
        return self.executor.execute(
            project_id=self.pid,
            grant_id=self.grant.grant_id,
            action="stat",
            path=path,
        )

    def test_missing_target_reports_absence_instead_of_raising(self) -> None:
        result = self._stat("plan.txt")
        self.assertFalse(result["exists"])
        self.assertEqual(result["path"], "plan.txt")
        self.assertNotIn("metadata", result)

    def test_missing_target_is_audited_as_a_successful_query(self) -> None:
        self._stat("plan.txt")
        with sqlite3.connect(self.tmp / "perm.sqlite3") as connection:
            rows = connection.execute(
                "SELECT outcome, detail_json FROM local_tool_events "
                "WHERE action = 'stat' ORDER BY created_at"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["succeeded"])
        self.assertFalse(json.loads(rows[-1][1])["exists"])

    def test_existing_target_reports_existence_and_metadata(self) -> None:
        (self.granted / "notes.txt").write_text("hello", encoding="utf-8")
        result = self._stat("notes.txt")
        self.assertTrue(result["exists"])
        self.assertEqual(result["metadata"]["size_bytes"], 5)

    def test_stat_still_refuses_a_path_outside_the_grant(self) -> None:
        with self.assertRaises(PermissionError):
            self._stat(str(self.tmp / "outside.txt"))


class TestReviewRoundHardening(unittest.TestCase):
    """Fixes demanded by the independent code review, bug hunt and security audit."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v119-review-"))
        self.granted = self.tmp / "granted"
        self.granted.mkdir()
        self.store = LocalPermissionStore(self.tmp / "perm.sqlite3")
        self.pid = "desktop-v119review"
        ConversationStore(self.tmp / "perm.sqlite3").create(
            title="v1.1.9 review hardening", conversation_id="v119review"
        )
        self.store.set_profile(
            project_id=self.pid,
            profile=PermissionProfile.FULL_COMPUTER,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.tmp / "state")
        self.grant = self.store.create_grant(
            project_id=self.pid,
            root_path=str(self.granted),
            capabilities=[
                FileCapability.LIST,
                FileCapability.READ,
                FileCapability.STAT,
                FileCapability.CREATE,
                FileCapability.WRITE,
                FileCapability.APPEND,
                FileCapability.COPY,
            ],
            reason="review hardening grant",
            ttl_seconds=3600,
        )

    def _execute(self, action: str, path: str, content: str = "x", **kwargs):
        return self.executor.execute(
            project_id=self.pid,
            grant_id=self.grant.grant_id,
            action=action,
            path=path,
            content=content,
            **kwargs,
        )

    def _stat(self, path: str):
        return self._execute("stat", path)

    # -- review finding 1: stat must be truthful for a missing parent ----

    def test_stat_with_missing_parent_reports_absence(self) -> None:
        result = self._stat("missing-dir/plan.txt")
        self.assertFalse(result["exists"])
        self.assertEqual(result["path"], "missing-dir/plan.txt")

    def test_stat_with_deeply_missing_parents_reports_absence(self) -> None:
        # NB: a first segment that Windows reads as a drive letter ("a/b/...")
        # is a drive-relative path and is correctly refused as out of scope.
        result = self._stat("nested/deeper/plan.txt")
        self.assertFalse(result["exists"])

    def test_relaxed_stat_resolution_still_refuses_a_relative_escape(self) -> None:
        """Skipping the strict parent check must not weaken containment."""
        with self.assertRaises(PermissionError):
            self._stat("../outside.txt")
        with self.assertRaises(PermissionError):
            self._stat("missing/../../outside.txt")

    def test_relaxed_stat_resolution_still_refuses_an_absolute_escape(self) -> None:
        with self.assertRaises(PermissionError):
            self._stat(str(self.tmp / "outside.txt"))

    # -- review finding 2: WRITE may not become an approval-free create --

    def test_write_to_a_missing_file_is_refused_and_creates_nothing(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self._execute("write", "never-existed.txt", "content")
        self.assertFalse((self.granted / "never-existed.txt").exists())

    def test_stale_write_resolution_cannot_downgrade_into_creation(self) -> None:
        """The reported TOCTOU: resolve against an existing file, then lose it.

        Execution must refuse rather than silently create the file with no
        exact-scope approval consumed and no version backup.
        """
        target = self.granted / "racy.txt"
        target.write_text("v1", encoding="utf-8")
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="racy.txt"
        )
        self.assertEqual(resolution["capability"], "write")
        target.unlink()
        with self.assertRaises(FileNotFoundError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=self.grant.grant_id,
                action="write",
                path="racy.txt",
                content="smuggled",
            )
        self.assertFalse(target.exists())

    def test_write_only_grant_cannot_create_through_the_raw_executor(self) -> None:
        write_only = self.store.create_grant(
            project_id=self.pid,
            root_path=str(self.granted),
            capabilities=[FileCapability.WRITE],
            reason="write only",
            ttl_seconds=3600,
        )
        with self.assertRaises(FileNotFoundError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=write_only.grant_id,
                action="write",
                path="write-only.txt",
                content="nope",
            )
        self.assertFalse((self.granted / "write-only.txt").exists())

    # -- review finding 3: explicit create never resolves to write ------

    def test_explicit_create_against_an_existing_target_does_not_resolve(self) -> None:
        (self.granted / "taken.txt").write_text("original", encoding="utf-8")
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="create", path="taken.txt"
        )
        self.assertFalse(resolution["matched"])
        with self.assertRaises(FileExistsError):
            self._execute("create", "taken.txt", "overwrite attempt")
        self.assertEqual(
            (self.granted / "taken.txt").read_text(encoding="utf-8"), "original"
        )

    # -- review finding 4: concurrent create must not lose an update ----

    def test_concurrent_creates_allow_exactly_one_winner(self) -> None:
        """The create path must be exclusive, not check-then-open.

        Each thread gets its OWN store/executor over the same database, exactly
        as separate requests do: LocalPermissionStore keeps one connection with
        check_same_thread=False, and sharing that single connection across
        threads is a pre-existing limitation of the store, not of this path.
        """
        import threading

        database = self.tmp / "perm.sqlite3"
        outcomes: list[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def attempt(marker: str) -> None:
            store = LocalPermissionStore(database)
            executor = LocalToolExecutor(store, state_root=self.tmp / "state")
            barrier.wait(timeout=30)
            try:
                executor.execute(
                    project_id=self.pid,
                    grant_id=self.grant.grant_id,
                    action="create",
                    path="contended.txt",
                    content=marker,
                )
                result = "created"
            except FileExistsError:
                result = "exists"
            except Exception as error:  # pragma: no cover - diagnostic only
                result = f"{type(error).__name__}: {error}"
            with lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=attempt, args=(f"writer-{index}",))
            for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(sorted(outcomes), ["created", "exists"], outcomes)
        self.assertIn(
            (self.granted / "contended.txt").read_text(encoding="utf-8"),
            {"writer-0", "writer-1"},
        )

    def test_create_over_an_existing_file_still_never_overwrites(self) -> None:
        target = self.granted / "existing.txt"
        target.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self._execute("create", "existing.txt", "clobber")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    # -- round-2 bug hunt: append is documented as acting on an existing file

    def test_append_to_a_missing_file_is_refused(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=self.grant.grant_id,
                action="append",
                path="not-there.txt",
                content="text",
            )
        self.assertFalse((self.granted / "not-there.txt").exists())

    def test_append_to_an_existing_file_still_needs_its_approval(self) -> None:
        """Appending to existing content is a modification, so it stays gated."""
        target = self.granted / "log.txt"
        target.write_text("one\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.pid,
                grant_id=self.grant.grant_id,
                action="append",
                path="log.txt",
                content="two\n",
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "one\n")

    def test_resolver_refuses_append_on_a_missing_target_with_a_clear_reason(self) -> None:
        """The governed path must not surface a raw WinError 2 for this."""
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="append", path="missing.txt"
        )
        self.assertFalse(resolution["matched"])
        self.assertIn("does not exist", str(resolution["reason"]))
        self.assertNotIn("WinError", str(resolution["reason"]))

    def test_resolver_refuses_copy_of_a_missing_source_with_a_clear_reason(self) -> None:
        resolution = self.executor.resolve_mutation_target(
            project_id=self.pid, action="copy", path="missing.txt"
        )
        self.assertFalse(resolution["matched"])
        self.assertIn("does not exist", str(resolution["reason"]))

    def test_stat_of_the_empty_path_reports_the_root_consistently(self) -> None:
        result = self._stat("")
        self.assertTrue(result["exists"])
        self.assertEqual(result["path"], ".")
        self.assertEqual(result["metadata"]["path"], ".")


if __name__ == "__main__":
    unittest.main()
