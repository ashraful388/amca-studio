"""Round 24 panel workspaces: FileExplorer + TerminalManager.

Covers the ZCode-style Files tab (live tree + any-type read, path-contained)
and the operator Terminal tab (real PowerShell sessions — distinct from the
agent's governed propose→approve→execute path, which is untouched).
"""

from __future__ import annotations

import base64
import sys
import tempfile
import time
import unittest
from pathlib import Path

from amca.panel_workspace import (
    MAX_SESSIONS_PER_PROJECT,
    FileExplorer,
    PanelWorkspaceError,
    TerminalManager,
)
from amca.workspace import WorkspaceStore


class FileExplorerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()  # noqa: S811 - resolved below
        self.root = Path(self.temporary.name)
        self.store = WorkspaceStore(self.root)
        self.explorer = FileExplorer(self.store)
        self.project_id = "explorer-project"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, rel: str, content: str) -> None:
        self.store.write_file(self.project_id, rel, content)

    def test_list_root_shows_dirs_and_files_hiding_internal_records(self) -> None:
        self.write("notes.md", "# hello")
        self.write("src/app.py", "print('hi')\n")
        listing = self.explorer.list_dir(self.project_id, "")
        names = [(entry["name"], entry["type"]) for entry in listing["entries"]]
        self.assertIn(("src", "dir"), names)
        self.assertIn(("notes.md", "file"), names)
        # .history and *.versions.json sidecars are internal bookkeeping.
        self.assertFalse(any(name.startswith(".") for name, _ in names))
        self.assertEqual(listing["path"], "")

    def test_list_nested_dir_returns_relative_path(self) -> None:
        self.write("src/app.py", "x = 1\n")
        listing = self.explorer.list_dir(self.project_id, "src")
        self.assertEqual(listing["path"], "src")
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["app.py"])
        self.assertEqual(listing["entries"][0]["type"], "file")

    def test_traversal_and_absolute_paths_refused(self) -> None:
        with self.assertRaisesRegex(PanelWorkspaceError, "traversal"):
            self.explorer.list_dir(self.project_id, "../outside")
        with self.assertRaisesRegex(PanelWorkspaceError, "relative"):
            self.explorer.list_dir(self.project_id, "C:/Windows")
        with self.assertRaisesRegex(PanelWorkspaceError, "traversal"):
            self.explorer.read_file(self.project_id, "a/../../secret.txt")

    def test_read_text_file_returns_decoded_content(self) -> None:
        self.write("docs/readme.md", "line one\nline two")
        content = self.explorer.read_file(self.project_id, "docs/readme.md")
        self.assertEqual(content["encoding"], "text")
        self.assertEqual(content["content"], "line one\nline two")
        self.assertFalse(content["truncated"])
        self.assertEqual(content["media_type"], "text/markdown")

    def test_read_binary_file_returns_base64_with_media_type(self) -> None:
        payload = bytes(range(256))
        target = self.store.project_root(self.project_id) / "pixel.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        content = self.explorer.read_file(self.project_id, "pixel.png")
        self.assertEqual(content["encoding"], "base64")
        self.assertEqual(content["media_type"], "image/png")
        self.assertEqual(base64.b64decode(content["content"]), payload)

    def test_read_missing_file_refused(self) -> None:
        with self.assertRaisesRegex(PanelWorkspaceError, "not found"):
            self.explorer.read_file(self.project_id, "nope.txt")

    def test_base_root_override_lists_and_contains(self) -> None:
        # Round 26: the operator can browse a REAL folder (the project's
        # root_path); containment now applies under THAT folder.
        real = self.root / "real-folder"
        (real / "sub").mkdir(parents=True)
        (real / "real.txt").write_text("on disk", encoding="utf-8")
        listing = self.explorer.list_dir(self.project_id, "", base_root=real)
        # Temp dirs can surface 8.3 aliases; compare identity, not spelling.
        self.assertTrue(Path(listing["root"]).samefile(real))
        names = [entry["name"] for entry in listing["entries"]]
        self.assertIn("sub", names)
        self.assertIn("real.txt", names)
        content = self.explorer.read_file(self.project_id, "real.txt", base_root=real)
        self.assertEqual(content["content"], "on disk")

    def test_base_root_override_still_refuses_traversal(self) -> None:
        real = self.root / "real-folder"
        real.mkdir()
        with self.assertRaisesRegex(PanelWorkspaceError, "traversal"):
            self.explorer.list_dir(self.project_id, "../outside", base_root=real)
        with self.assertRaisesRegex(PanelWorkspaceError, "colons"):
            self.explorer.read_file(self.project_id, "x.txt:ads", base_root=real)
        with self.assertRaisesRegex(PanelWorkspaceError, "traversal"):
            self.explorer.read_file(self.project_id, "..\\escape.txt", base_root=real)

    def test_base_root_override_missing_dir_refused(self) -> None:
        # The service validates existence before calling; the explorer also
        # fails closed if handed a nonexistent base.
        missing = self.root / "no-such-folder"
        with self.assertRaisesRegex(PanelWorkspaceError, "not found"):
            self.explorer.list_dir(self.project_id, "", base_root=missing)


class TerminalManagerTests(unittest.TestCase):
    """Real PowerShell sessions. Skipped off Windows (dev machine is win32)."""

    def setUp(self) -> None:
        if sys.platform != "win32":
            self.skipTest("operator terminal requires Windows PowerShell")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.manager = TerminalManager()
        self.project_id = "terminal-project"

    def tearDown(self) -> None:
        self.manager.shutdown()
        time.sleep(0.2)
        self.temporary.cleanup()

    def _poll_until(self, session_id: str, needle: str, seconds: float = 20.0) -> str:
        deadline = time.monotonic() + seconds
        after = 0
        seen = ""
        while time.monotonic() < deadline:
            page = self.manager.output(session_id, after)
            seen += page["text"]
            after = page["next_offset"]
            if needle in seen:
                return seen
            time.sleep(0.2)
        self.fail(f"terminal output never contained {needle!r}; got: {seen[-2000:]!r}")

    def test_create_send_output_and_delete(self) -> None:
        session = self.manager.create(self.project_id, self.root)
        self.assertTrue(session["alive"])
        self.assertEqual(session["cwd"], str(self.root))
        self.manager.send_command(session["session_id"], "Write-Output amca-term-e2e")
        seen = self._poll_until(session["session_id"], "amca-term-e2e")
        self.assertIn("amca-term-e2e", seen)
        deleted = self.manager.delete(session["session_id"])
        self.assertEqual(deleted["deleted"], session["session_id"])
        with self.assertRaisesRegex(PanelWorkspaceError, "unknown terminal session"):
            self.manager.send_command(session["session_id"], "dir")

    def test_cwd_tracks_set_location(self) -> None:
        subdir = self.root / "nested"
        subdir.mkdir()
        session = self.manager.create(self.project_id, self.root)
        session_id = session["session_id"]
        self.manager.send_command(session_id, f"Set-Location -LiteralPath '{subdir}'")
        deadline = time.monotonic() + 15
        cwd = ""
        while time.monotonic() < deadline:
            cwd = self.manager.output(session_id, 0)["cwd"]
            if str(cwd).casefold().endswith(subdir.name.casefold()):
                break
            time.sleep(0.2)
        self.assertTrue(
            str(cwd).casefold().endswith(subdir.name.casefold()),
            f"cwd did not track Set-Location: {cwd!r}",
        )
        self.manager.delete(session_id)

    def test_break_restarts_session_and_next_command_runs(self) -> None:
        session = self.manager.create(self.project_id, self.root)
        session_id = session["session_id"]
        self.manager.send_command(session_id, "Start-Sleep 30")
        time.sleep(1.0)
        result = self.manager.send_break(session_id)
        self.assertEqual(result["restarted"], True)
        # The session restarts in place: PowerShell is respawned at the same
        # cwd, scrollback is kept, and the next command runs.
        deadline = time.monotonic() + 15
        alive = False
        while time.monotonic() < deadline:
            if self.manager.output(session_id, 0)["alive"]:
                alive = True
                break
            time.sleep(0.2)
        self.assertTrue(alive, "session did not come back after break")
        self.manager.send_command(session_id, "Write-Output amca-after-break")
        self._poll_until(session_id, "amca-after-break")
        self.manager.delete(session_id)

    def test_unknown_session_refused(self) -> None:
        with self.assertRaisesRegex(PanelWorkspaceError, "unknown terminal session"):
            self.manager.output("nope", 0)
        with self.assertRaisesRegex(PanelWorkspaceError, "unknown terminal session"):
            self.manager.delete("nope")

    def test_empty_command_refused(self) -> None:
        session = self.manager.create(self.project_id, self.root)
        with self.assertRaisesRegex(PanelWorkspaceError, "required"):
            self.manager.send_command(session["session_id"], "   ")
        self.manager.delete(session["session_id"])

    def test_terminal_starts_in_override_folder(self) -> None:
        # Round 26: the operator can start the terminal in a REAL folder
        # (the project's root_path); the session cwd + spawn root follow it.
        real = self.root / "term-folder"
        real.mkdir()
        session = self.manager.create(self.project_id, real)
        self.assertEqual(session["cwd"], str(real.resolve()))
        self.manager.send_command(session["session_id"], "Write-Output cwd-check-ok")
        deadline = time.monotonic() + 15
        seen = ""
        while time.monotonic() < deadline:
            page = self.manager.output(session["session_id"], 0)
            seen = page["text"]
            if "cwd-check-ok" in seen:
                break
            time.sleep(0.2)
        self.assertIn("cwd-check-ok", seen)
        self.manager.delete(session["session_id"])

    def test_per_project_session_cap(self) -> None:
        sessions = []
        try:
            for _ in range(MAX_SESSIONS_PER_PROJECT):
                sessions.append(self.manager.create(self.project_id, self.root))
            with self.assertRaisesRegex(PanelWorkspaceError, "limit reached"):
                self.manager.create(self.project_id, self.root)
        finally:
            for session in sessions:
                try:
                    self.manager.delete(session["session_id"])
                except PanelWorkspaceError:
                    pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
