"""filesystem.search must search file TEXT, not only file names.

The tool's own description promises "Search file names or file text
within granted scope", but the implementation matched NAMES only.  A model
that searched for a string it had just written into a file got an empty
result, concluded the search was unreliable, and blocked its own run on
that disagreement.  The model was right and the tool was wrong.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.domain import ProjectState
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.storage import SQLiteStore


class FilesystemSearchContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.database = self.root / "state.sqlite3"
        self.project_id = "search-content-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="test"))
        self.store = LocalPermissionStore(self.database)
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.STANDARD,
            workspace_root=self.workspace,
        )
        self.store.create_grant(
            project_id=self.project_id,
            root_path=self.workspace,
            capabilities=[FileCapability.SEARCH, FileCapability.READ],
            reason="Bounded test grant",
        )
        self.executor = LocalToolExecutor(
            self.store, state_root=self.root / "tool-state"
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def search(self, query: str, path: str = ".") -> dict:
        return self.executor.execute(
            project_id=self.project_id,
            grant_id=self.store.grants(self.project_id, active_only=True)[0]["grant_id"],
            action=FileCapability.SEARCH,
            path=path,
            query=query,
        )

    def test_a_string_inside_a_file_is_found(self) -> None:
        (self.workspace / "calculator.html").write_text(
            '<button data-digit="7">7</button>', encoding="utf-8"
        )
        result = self.search("data-digit")
        names = [item["name"] for item in result.get("matches", [])]
        self.assertIn("calculator.html", names, "the file's TEXT must be searched")

    def test_a_content_match_is_reported_as_such(self) -> None:
        (self.workspace / "calculator.html").write_text(
            '<button data-digit="7">7</button>', encoding="utf-8"
        )
        result = self.search("data-digit")
        matches = result.get("matches") or []
        self.assertTrue(matches, "a content match must be reported")
        self.assertEqual(matches[0].get("matched_in"), "content")

    def test_a_name_match_is_still_found(self) -> None:
        (self.workspace / "calculator.html").write_text("x", encoding="utf-8")
        result = self.search("calculator")
        matches = result.get("matches") or []
        names = [item["name"] for item in matches]
        self.assertIn("calculator.html", names, "a name match must still work")
        self.assertEqual(matches[0].get("matched_in"), "name")

    def test_a_string_that_is_in_no_file_is_not_found(self) -> None:
        (self.workspace / "calculator.html").write_text("x", encoding="utf-8")
        result = self.search("not-present-anywhere")
        self.assertEqual(result.get("matches"), [])

    def test_a_binary_file_is_skipped_rather_than_failing_the_search(self) -> None:
        (self.workspace / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
        (self.workspace / "notes.txt").write_text("data-digit", encoding="utf-8")
        result = self.search("data-digit")
        names = [item["name"] for item in result.get("matches", [])]
        self.assertIn("notes.txt", names)
        self.assertNotIn("blob.bin", names)


    def test_a_large_result_set_does_not_fail_the_tool(self) -> None:
        # The audit event keeps the result OUT of its detail, and the event
        # detail has a size cap.  The result used to carry the same entries
        # three times over, which pushed the event past the cap and made
        # the whole tool call fail for a large result set.
        for index in range(200):
            (self.workspace / f"file-{index:03d}.txt").write_text(
                "the needle is here", encoding="utf-8"
            )
        result = self.search("needle")
        self.assertEqual(len(result.get("matches") or []), 200)
        self.assertFalse(result.get("truncated"))

    def test_a_name_and_a_content_match_are_both_reported(self) -> None:
        # A file whose NAME matches is still content-searched, and the
        # entry says that it matched in BOTH ways.
        (self.workspace / "needle.txt").write_text(
            "a needle in the text too", encoding="utf-8"
        )
        result = self.search("needle")
        matches = result.get("matches") or []
        self.assertTrue(matches)
        self.assertEqual(matches[0].get("matched_in"), "name+content")


    def test_a_search_only_grant_cannot_probe_file_content(self) -> None:
        # A search-only grant must not become a way to ask whether an
        # unknown string is in a file: that is reading, by another name.
        (self.workspace / "secret.txt").write_text(
            "the treasure is here", encoding="utf-8"
        )
        search_only = self.store.create_grant(
            project_id=self.project_id,
            root_path=self.workspace,
            capabilities=[FileCapability.SEARCH],
            reason="Search only",
        )
        result = self.executor.execute(
            project_id=self.project_id,
            grant_id=search_only.grant_id,
            action=FileCapability.SEARCH,
            path=".",
            query="treasure",
        )
        names = [item["name"] for item in result.get("matches") or []]
        self.assertNotIn(
            "secret.txt",
            names,
            "a search-only grant must not report a content match",
        )

if __name__ == "__main__":
    unittest.main()
