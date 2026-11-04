"""A Project's folder must be the boundary its local tools work inside.

This is the regression test for the defect the physical end-to-end test
found: a chat in a Project with a folder could not write a file into that
folder, because the folder was never bound to the Project's local-tool
identity.  The relative path was then anchored to whichever drive grant
came first, so a request to write "calculator.html" into the Project
folder landed on a drive root instead - and on this machine the drive it
landed on could not accept a new file at all, so the write failed and the
deliverable was never produced.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    PermissionProfile,
    _resolve_workspace_root,
)
from amca.domain import ProjectState
from amca.storage import SQLiteStore


class ProjectWorkspaceBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_folder = self.root / "My Project"
        self.project_folder.mkdir()
        self.other_folder = self.root / "Other"
        self.other_folder.mkdir()
        self.database = self.root / "state.sqlite3"
        self.project_id = "studio-binding-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="test"))
        self.store = LocalPermissionStore(self.database)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_binding_the_folder_does_not_change_the_global_access_level(self) -> None:
        # The PC access LEVEL is global.  Binding a Project's folder must
        # never raise or lower it for every other chat on the machine.
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.RESTRICTED,
            workspace_root=None,
        )
        before = self.store.global_profile()["profile"]
        self.store.bind_workspace_root(
            project_id=self.project_id,
            workspace_root=self.project_folder,
        )
        after = self.store.global_profile()["profile"]
        self.assertEqual(before, after, "the global level must not move")
        profile = self.store.profile(self.project_id)
        self.assertEqual(
            Path(str(profile["workspace_root"])).resolve(),
            _resolve_workspace_root(self.project_folder).resolve(),
        )

    def test_the_bound_folder_is_the_workspace_root(self) -> None:
        self.store.bind_workspace_root(
            project_id=self.project_id,
            workspace_root=self.project_folder,
        )
        profile = self.store.profile(self.project_id)
        self.assertTrue(profile["workspace_root"], "the folder must be bound")
        self.assertEqual(
            Path(str(profile["workspace_root"])).resolve(),
            self.project_folder.resolve(),
        )

    def test_a_folder_that_is_already_bound_is_not_replaced(self) -> None:
        # The Project's folder is chosen ONCE, at creation.  A later bind
        # must not move the boundary to a different folder.
        self.store.bind_workspace_root(
            project_id=self.project_id,
            workspace_root=self.project_folder,
        )
        self.store.bind_workspace_root(
            project_id=self.project_id,
            workspace_root=self.other_folder,
        )
        profile = self.store.profile(self.project_id)
        self.assertEqual(
            Path(str(profile["workspace_root"])).resolve(),
            self.project_folder.resolve(),
        )

    def test_binding_does_not_grant_any_capability(self) -> None:
        # A bound folder is a boundary, NOT a permission.  Binding it must
        # not let the local tools do anything they could not do before.
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.RESTRICTED,
            workspace_root=None,
        )
        self.store.bind_workspace_root(
            project_id=self.project_id,
            workspace_root=self.project_folder,
        )
        grants = self.store.grants(self.project_id, active_only=True)
        self.assertEqual(grants, [], "binding a folder must not create a grant")

    def test_a_folder_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises((OSError, ValueError, PermissionError)):
            self.store.bind_workspace_root(
                project_id=self.project_id,
                workspace_root=self.root / "not-created",
            )


if __name__ == "__main__":
    unittest.main()
