"""X1.2 workspace store tests: versions, containment, diffs, conflicts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.workspace import WorkspaceError, WorkspaceStore


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_read_update_versions_and_diff(self) -> None:
        created = self.store.write_file("proj1", "notes/plan.md", "# Plan\nline 1\n")
        self.assertTrue(created["created"])
        self.assertEqual(created["version"], 1)

        updated = self.store.write_file(
            "proj1", "notes/plan.md", "# Plan\nline 1\nline 2\n", base_version=1
        )
        self.assertFalse(updated["created"])
        self.assertEqual(updated["version"], 2)
        self.assertIn("+line 2", updated["diff"])
        self.assertIn("a/notes/plan.md (v1)", updated["diff"])
        self.assertIn("b/notes/plan.md (v2)", updated["diff"])

        opened = self.store.read_file("proj1", "notes/plan.md")
        self.assertEqual(opened["version"], 2)
        self.assertIn("line 2", opened["content"])

        history = self.store.read_version("proj1", "notes/plan.md", 1)
        self.assertNotIn("line 2", history)

    def test_stale_base_version_fails_closed(self) -> None:
        self.store.write_file("proj1", "a.txt", "one")
        with self.assertRaises(WorkspaceError) as raised:
            self.store.write_file("proj1", "a.txt", "two", base_version=99)
        self.assertIn("changed since version", str(raised.exception))
        opened = self.store.read_file("proj1", "a.txt")
        self.assertEqual(opened["content"], "one")

    def test_path_traversal_and_absolute_paths_are_rejected(self) -> None:
        for bad in ("../escape.txt", "..\\escape.txt", "/abs.txt", "C:/x.txt", ""):
            with self.assertRaises(WorkspaceError):
                self.store.write_file("proj1", bad, "nope")

    def test_list_files_reports_current_version_only_for_text_files(self) -> None:
        self.store.write_file("proj1", "root.md", "hello")
        self.store.write_file("proj1", "sub/deep.md", "deep")
        listed = self.store.list_files("proj1")
        paths = sorted(item["path"] for item in listed)
        self.assertEqual(paths, ["root.md", "sub/deep.md"])
        self.assertTrue(all(item["version"] == 1 for item in listed))

    def test_revert_creates_new_version_with_old_content(self) -> None:
        self.store.write_file("proj1", "doc.md", "v1 body")
        self.store.write_file("proj1", "doc.md", "v2 body", base_version=1)
        reverted = self.store.revert_file("proj1", "doc.md", 1)
        self.assertEqual(reverted["version"], 3)
        opened = self.store.read_file("proj1", "doc.md")
        self.assertEqual(opened["content"], "v1 body")
        self.assertIn("-v2 body", reverted["diff"])
        self.assertIn("+v1 body", reverted["diff"])

    def test_revert_to_current_content_fails_closed(self) -> None:
        self.store.write_file("proj1", "doc.md", "same")
        with self.assertRaises(WorkspaceError):
            self.store.revert_file("proj1", "doc.md", 1)

    def test_recent_changes_lists_versions_newest_first(self) -> None:
        self.store.write_file("proj1", "log.txt", "one")
        self.store.write_file("proj1", "log.txt", "one\ntwo", base_version=1)
        changes = self.store.recent_changes("proj1")
        self.assertEqual(len(changes), 1)
        entry = changes[0]
        self.assertEqual(entry["path"], "log.txt")
        self.assertEqual(entry["version"], 2)
        self.assertIn("+two", entry["diff"])

    def test_delete_removes_file(self) -> None:
        self.store.write_file("proj1", "gone.txt", "bye")
        result = self.store.delete_file("proj1", "gone.txt")
        self.assertEqual(result["deleted"], "gone.txt")
        with self.assertRaises(WorkspaceError):
            self.store.read_file("proj1", "gone.txt")

    def test_invalid_project_ids_rejected(self) -> None:
        for bad in ("../p", "a/b", "", "with:colon"):
            with self.assertRaises(WorkspaceError):
                self.store.list_files(bad)


if __name__ == "__main__":
    unittest.main()
