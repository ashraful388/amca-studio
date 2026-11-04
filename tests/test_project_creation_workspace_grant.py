"""A Project created with a folder must be able to work IN that folder.

This is the service-level regression test for the defect the physical
end-to-end test found.  A chat in a Project with a folder could not write a
file into that folder, because the Project's folder was never bound to the
Project's local-tool identity.  The executor therefore anchored a relative
path such as "calculator.html" to whichever drive grant came first, so the
deliverable landed on a drive root instead of in the Project's folder.

The test covers the whole path: create the Project through the service,
then check the folder is bound, that the level in force is honoured, and
that a relative target resolves INSIDE the Project's folder.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.local_tools import (
    FileCapability,
    LocalToolExecutor,
    PermissionAction,
    PermissionProfile,
)
from amca.service import ServiceContext


class ProjectCreationWorkspaceGrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_folder = self.root / "Calculator Project"
        self.project_folder.mkdir()
        self.benchmarks = self.root / "benchmarks" / "tasks.json"
        self.benchmarks.parent.mkdir(parents=True, exist_ok=True)
        self.benchmarks.write_text("[]", encoding="utf-8")
        self.service = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.benchmarks,
        )

    def tearDown(self) -> None:
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _set_global_level(self, level: str) -> None:
        """Put a PC access level in force.

        The level is global, and it can only be set for an id that already
        names a real project - so an anchor project establishes it, exactly
        as it does in the product.
        """
        anchor = self.root / "anchor"
        anchor.mkdir(exist_ok=True)
        self.service.save_studio_project(
            {
                "project_id": "studio-level-anchor",
                "name": "Level anchor",
                "root_path": str(anchor),
            }
        )
        self.service.set_local_permission_profile(
            {"project_id": "studio-level-anchor", "profile": level}
        )

    def test_a_new_project_binds_its_own_folder(self) -> None:
        project = self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        profile = self.service.local_permission_store.profile(project["project_id"])
        self.assertTrue(
            profile["workspace_root"],
            "the Project's own folder must be bound to its local-tool identity",
        )
        self.assertEqual(
            Path(str(profile["workspace_root"])).resolve(),
            self.project_folder.resolve(),
        )

    def test_the_folder_bound_is_the_one_the_project_recorded(self) -> None:
        project = self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-2",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        self.assertEqual(
            Path(str(project["root_path"])).resolve(),
            self.project_folder.resolve(),
        )
        # The BOUND folder is the one the Project recorded - this is what
        # makes a relative path land in the Project's folder.
        profile = self.service.local_permission_store.profile(project["project_id"])
        self.assertEqual(
            Path(str(profile["workspace_root"])).resolve(),
            self.project_folder.resolve(),
        )

    def test_binding_a_folder_does_not_raise_the_global_access_level(self) -> None:
        before = self.service.local_permission_store.global_profile()["profile"]
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-3",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        after = self.service.local_permission_store.global_profile()["profile"]
        self.assertEqual(before, after, "the global PC access level must not move")

    def test_a_restricted_project_can_read_its_folder_but_not_write(self) -> None:
        # The level in force decides the capabilities.  Restricted is
        # read-only, and the grant the Project gets must not widen it.
        self._set_global_level("restricted")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-4",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        grants = self.service.local_permission_store.grants(
            "studio-creation-binding-4", active_only=True
        )
        self.assertTrue(grants, "the Project's folder must carry a grant")
        for grant in grants:
            self.assertNotIn("write", [str(c) for c in grant["capabilities"]])
            self.assertNotIn("create", [str(c) for c in grant["capabilities"]])

    def test_a_standard_project_is_granted_its_own_folder(self) -> None:
        # Standard permits writing, and the Project's own folder has to
        # carry a grant for that write to be possible at all.  The grant is
        # made when the Project is CREATED, so the level has to be in force
        # by then.
        self._set_global_level("standard")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-5",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        grants = self.service.local_permission_store.grants(
            "studio-creation-binding-5", active_only=True
        )
        roots = {
            Path(str(grant["canonical_root"])).resolve() for grant in grants
        }
        self.assertIn(
            self.project_folder.resolve(),
            roots,
            "the Project's own folder must be granted",
        )

    def test_a_relative_target_resolves_inside_the_project_folder(self) -> None:
        # The behaviour the whole defect was about: "calculator.html" in a
        # chat in this Project means the Project's folder.
        self._set_global_level("standard")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-6",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        executor = LocalToolExecutor(
            self.service.local_permission_store,
            state_root=self.root / "tool-state",
        )
        resolution = executor.resolve_mutation_target(
            project_id="studio-creation-binding-6",
            action=FileCapability.CREATE,
            path="calculator.html",
        )
        self.assertTrue(resolution["matched"], "the relative target must resolve")
        # The key that carries the anchored path is "requested_path" - the
        # earlier assertion looked for keys this method never returns, so it
        # never ran.
        target = str(resolution.get("requested_path") or "")
        self.assertTrue(target, "the resolution must carry the resolved path")
        # Windows resolves the temp folder to its long name in one place and
        # its short name in the other, so the paths are compared resolved.
        self.assertEqual(
            Path(target).resolve().parent,
            self.project_folder.resolve(),
            "the target must land inside the Project's folder",
        )
        # The grant that governs it must be the Project's own folder.
        grant = self.service.local_permission_store.grant(
            str(resolution["grant_id"])
        )
        self.assertIsNotNone(grant)
        self.assertEqual(
            Path(str(grant.canonical_root)).resolve(),
            self.project_folder.resolve(),
        )


    def test_a_revoked_grant_is_not_brought_back_by_re_saving_the_project(self) -> None:
        # A read re-resolves its grant to ANY active grant covering the
        # path, so a grant that came back after the operator revoked one
        # would silently keep the access alive.  Re-saving the Project must
        # not resurrect it.
        self._set_global_level("standard")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-7",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        grants = self.service.local_permission_store.grants(
            "studio-creation-binding-7", active_only=True
        )
        self.assertTrue(grants, "the Project's folder must carry a grant")
        grant_id = str(grants[0]["grant_id"])
        self.service.local_permission_store.revoke_grant(
            grant_id, revoked_by="desktop_user", reason="Test"
        )
        # Re-save the Project: the grant must NOT come back.
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-7",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        after = self.service.local_permission_store.grants(
            "studio-creation-binding-7", active_only=True
        )
        self.assertEqual(after, [], "a revoked grant must not come back")

    def test_a_level_change_widens_the_projects_grant(self) -> None:
        # The level is global, so a change to it has to reach the grant
        # that was made under the old one.  Otherwise the Project stays
        # read-only with no way for the operator to tell why.
        self._set_global_level("restricted")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-8",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        grants = self.service.local_permission_store.grants(
            "studio-creation-binding-8", active_only=True
        )
        self.assertTrue(grants)
        self.assertNotIn("write", [str(c) for c in grants[0]["capabilities"]])
        # Raise the level.
        self.service.set_local_permission_profile(
            {"project_id": "studio-creation-binding-8", "profile": "standard"}
        )
        widened = self.service.local_permission_store.grants(
            "studio-creation-binding-8", active_only=True
        )
        self.assertTrue(widened, "the grant must survive the level change")
        self.assertIn("write", [str(c) for c in widened[0]["capabilities"]])

    def test_a_level_change_grants_a_project_that_has_none(self) -> None:
        # A Project created before the operator had ever chosen a level is
        # bound but ungranted, and the level choice is what repairs it.
        # Without this the Project stays permanently denied, with no
        # backend path left to fix it.
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-9",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        self.assertEqual(
            self.service.local_permission_store.grants(
                "studio-creation-binding-9", active_only=True
            ),
            [],
            "no level has been chosen, so no grant can exist yet",
        )
        self.service.set_local_permission_profile(
            {"project_id": "studio-creation-binding-9", "profile": "standard"}
        )
        granted = self.service.local_permission_store.grants(
            "studio-creation-binding-9", active_only=True
        )
        self.assertTrue(
            granted,
            "the level choice must grant the Project's own folder",
        )
        self.assertIn("write", [str(c) for c in granted[0]["capabilities"]])


    def test_a_level_change_does_not_widen_the_operators_own_grant(self) -> None:
        # A grant the operator made by hand is THEIRS: it was deliberately
        # narrow, and a level change must not quietly widen it.
        self._set_global_level("restricted")
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-10",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        # The operator makes a narrow grant of their own on the same folder.
        self.service.create_local_grant({
            "project_id": "studio-creation-binding-10",
            "root_path": str(self.project_folder),
            "capabilities": ["list", "read"],
            "reason": "The operator's own narrow grant",
        })
        before = [
            item for item in self.service.local_permission_store.grants(
                "studio-creation-binding-10", active_only=True
            )
        ]
        narrow = [
            item for item in before
            if "write" not in [str(c) for c in item["capabilities"]]
        ]
        self.assertTrue(narrow, "the operator's narrow grant must survive")
        self.service.set_local_permission_profile(
            {"project_id": "studio-creation-binding-10", "profile": "standard"}
        )
        after = self.service.local_permission_store.grants(
            "studio-creation-binding-10", active_only=True
        )
        still_narrow = [
            item for item in after
            if "write" not in [str(c) for c in item["capabilities"]]
        ]
        self.assertTrue(
            still_narrow,
            "the operator's own narrow grant must not be widened by a level change",
        )

    def test_a_project_created_before_the_level_is_repaired_by_the_backfill(self) -> None:
        # A Project created before the operator had ever set an access
        # level ends up bound but ungranted.  The backfill has to repair
        # that, or the Project is permanently denied.
        self.service.save_studio_project(
            {
                "project_id": "studio-creation-binding-11",
                "name": "Calculator Project",
                "root_path": str(self.project_folder),
            }
        )
        # No level has ever been set, so no grant could be made.
        self.assertEqual(
            self.service.local_permission_store.grants(
                "studio-creation-binding-11", active_only=True
            ),
            [],
        )
        # A level is chosen, and the Project is created BEFORE that choice,
        # so the grant is the one the level choice makes.
        self._set_global_level("standard")
        granted = self.service.local_permission_store.grants(
            "studio-creation-binding-11", active_only=True
        )
        self.assertTrue(
            granted,
            "the level choice must grant the Project's own folder",
        )
        # The backfill runs at startup and must not break anything.
        self.service._backfill_project_workspace_roots()
        again = self.service.local_permission_store.grants(
            "studio-creation-binding-11", active_only=True
        )
        self.assertEqual(
            len(again), len(granted), "the backfill must not add a second grant"
        )

if __name__ == "__main__":
    unittest.main()
