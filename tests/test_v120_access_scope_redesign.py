"""v1.2.0 - Local PC Access scope redesign.

The Project folder becomes a Project property set once at creation, and Full
Access becomes genuinely whole-PC. These tests pin the three properties that
must hold together:

1. A new Project always gets a real, writable folder, and a failure leaves no
   half-made project behind.
2. Full Access reaches the whole machine WITHOUT removing the boundary: the
   grants are real per-drive grants, so containment, junction rejection and
   exact-scope approvals all keep working.
3. The folder-bound levels stay inside the Project folder, and a standalone
   chat is refused with an actionable message rather than a silent denial.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from amca.chat_history import ConversationStore
from amca.domain import ProjectState
from amca.governed_chat import GovernedChatAdapter
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
    _PROVISIONED_REASON,
    _local_drive_roots,
)
from amca.permissions import PermissionAction, PermissionStore
from amca.service import ServiceContext
from amca.storage import SQLiteStore
from amca.studio_projects import (
    StudioProjectStore,
    default_project_folder,
    discard_created_folders,
    prepare_project_folder,
    same_folder,
    _validate_project_folder,
)


class ProjectFolderTests(unittest.TestCase):
    """The Project folder is mandatory, created, and cleaned up on failure."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        # ConversationStore creates the shared schema the Project store joins.
        ConversationStore(self.database)
        self.store = StudioProjectStore(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_new_project_gets_a_real_folder_and_records_it(self) -> None:
        target = self.root / "projects" / "Research"
        created = self.store.save({"name": "Research", "root_path": str(target)})
        self.assertTrue(target.is_dir(), "AMCA must create the missing folder")
        # Compare resolved forms: Windows may hand back an 8.3 short name for
        # the temp path while the stored canonical root is the long name.
        self.assertEqual(
            os.path.normcase(str(Path(created["root_path"]).resolve())),
            os.path.normcase(str(target.resolve())),
        )

    def test_default_folder_is_documents_under_the_project_name(self) -> None:
        expected = self.root / "Documents" / "My Project"
        with mock.patch.dict(os.environ, {"AMCA_DEFAULT_PROJECT_BASE": str(self.root / "Documents")}, clear=False):
            created = self.store.save({"name": "My Project"})
            # The reported default must be the folder that was actually made,
            # so the form can show it without a second naming rule.
            reported = default_project_folder("My Project")
        self.assertEqual(
            os.path.normcase(str(Path(created["root_path"]).resolve())),
            os.path.normcase(str(expected.resolve())),
        )
        self.assertTrue(expected.is_dir())
        self.assertEqual(
            os.path.normcase(str(Path(reported).resolve())),
            os.path.normcase(str(expected.resolve())),
        )

    def test_reserved_windows_device_names_get_a_usable_default(self) -> None:
        with mock.patch.dict(os.environ, {"AMCA_DEFAULT_PROJECT_BASE": str(self.root / "Documents")}, clear=False):
            for name in ("CON", "con.txt", "NUL"):
                with self.subTest(name=name):
                    cleaned = Path(default_project_folder(name)).name
                    self.assertNotEqual(cleaned.split(".")[0].upper(), name.split(".")[0].upper())

    def test_relative_device_and_nul_roots_are_rejected(self) -> None:
        for bad in ("relative\\folder", "C:\\?\\GLOBALROOT\\Device\\Harddisk", "bad\x00name", "\\\\server\\share"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _validate_project_folder(bad)

    def test_rollback_removes_only_a_folder_this_call_created(self) -> None:
        # A folder that already existed is never deleted on a later failure.
        preexisting = self.root / "keepme"
        preexisting.mkdir()
        root, created = prepare_project_folder(preexisting)
        self.assertEqual(created, [], "an existing folder must be reported as not created")
        # Simulate the database step failing after preparation.
        discard_created_folders(created)
        self.assertTrue(preexisting.is_dir(), "a pre-existing folder must survive")

    def test_rollback_removes_every_parent_this_call_created(self) -> None:
        # mkdir(parents=True) could not tell us which parents we made, so a
        # later failure orphaned them.  Every created component is tracked.
        target = self.root / "a" / "b" / "Doomed"
        boom = sqlite3.OperationalError("simulated failure")
        with mock.patch.object(StudioProjectStore, "_connect", side_effect=boom):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save({"name": "Doomed", "root_path": str(target)})
        self.assertFalse(target.exists(), "the project folder must be removed")
        self.assertFalse((self.root / "a").exists(), "parents this call created must be removed too")

    def test_save_failure_leaves_no_project_and_no_new_folder(self) -> None:
        target = self.root / "projects" / "Doomed"
        boom = sqlite3.OperationalError("simulated failure")
        with mock.patch.object(StudioProjectStore, "_connect", side_effect=boom):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.save({"name": "Doomed", "root_path": str(target)})
        self.assertFalse(target.exists(), "a folder created by a failed save must be removed")
        self.assertIsNone(self.store.get("studio-doomed"))

    def test_legacy_null_root_is_preserved_and_not_auto_migrated(self) -> None:
        # A pre-v1.2.0 row: no root_path column value at all.
        with contextlib.closing(sqlite3.connect(self.database)) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO studio_projects
                       (project_id,name,description,instructions,template,created_at,updated_at,archived,root_path)
                       VALUES ('studio-legacy','Legacy','','','general','2026-01-01','2026-01-01',0,NULL)"""
                )
        record = self.store.get("studio-legacy")
        self.assertIsNone(record["root_path"])
        # Loading must not have created anything on disk.
        self.assertFalse((self.root / "Legacy").exists())


class FullAccessScopeTests(unittest.TestCase):
    """Full Access is whole-PC, modelled as one real grant per drive."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.project_id = "scope-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="scope test"))
        self.store = LocalPermissionStore(self.database)
        self.executor = LocalToolExecutor(self.store, state_root=self.root / "state")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _set_profile(self, profile: PermissionProfile) -> None:
        self.store.set_profile(
            project_id=self.project_id, profile=profile, workspace_root=self.workspace
        )

    def _drive_grant_for(self, target: Path) -> dict:
        """The provisioned whole-PC grant that covers `target`'s drive."""
        drive = target.resolve().drive.upper()
        for grant in self.store.grants(self.project_id, active_only=True):
            if grant["reason"] != _PROVISIONED_REASON:
                continue
            if Path(grant["canonical_root"]).drive.upper() == drive:
                return grant
        self.fail(f"no provisioned grant covers {drive}")

    def test_provisions_one_grant_per_drive_and_reports_coverage(self) -> None:
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        scope = self.store.provision_full_access(self.project_id)
        self.assertTrue(scope["active"])
        self.assertEqual(scope["drives_granted"], scope["drives_detected"])
        self.assertEqual(scope["drives_missing"], [])
        provisioned = [
            grant for grant in self.store.grants(self.project_id, active_only=True)
            if grant["reason"] == _PROVISIONED_REASON
        ]
        self.assertEqual(len(provisioned), len(_local_drive_roots()))
        # Each grant really is that drive's root, so containment still has a
        # concrete boundary to enforce rather than an "anywhere" sentinel.
        for grant in provisioned:
            root = Path(grant["canonical_root"])
            self.assertEqual(root.resolve(), root)
            self.assertEqual(root.drive, root.anchor.rstrip("\\/"))

    def test_provisioning_is_idempotent(self) -> None:
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        first = self.store.provision_full_access(self.project_id)
        second = self.store.provision_full_access(self.project_id)
        self.assertEqual(first["drives_granted"], second["drives_granted"])
        active = [
            grant for grant in self.store.grants(self.project_id, active_only=True)
            if grant["reason"] == _PROVISIONED_REASON
        ]
        self.assertEqual(len(active), len(_local_drive_roots()))

    def test_only_full_computer_provisions_and_leaving_it_revokes(self) -> None:
        for profile in (PermissionProfile.RESTRICTED, PermissionProfile.STANDARD, PermissionProfile.EXTENDED):
            with self.subTest(profile=profile):
                self._set_profile(profile)
                with self.assertRaises(PermissionError):
                    self.store.provision_full_access(self.project_id)
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        self.store.provision_full_access(self.project_id)
        self._set_profile(PermissionProfile.STANDARD)
        self.store.sync_full_access(self.project_id)
        remaining = [
            grant for grant in self.store.grants(self.project_id, active_only=True)
            if grant["reason"] == _PROVISIONED_REASON
        ]
        self.assertEqual(remaining, [], "a whole-PC grant must not outlive the level")

    def test_full_computer_writes_new_files_outside_the_project_folder(self) -> None:
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        self.store.provision_full_access(self.project_id)
        grant = self._drive_grant_for(self.outside)
        target = self.outside / "written_outside_project.txt"
        result = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant["grant_id"],
            action=FileCapability.CREATE,
            path=str(target),
            content="pc wide",
        )
        self.assertTrue(result["created"])
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), "pc wide")

    def test_overwrite_still_requires_an_exact_approval_under_full_computer(self) -> None:
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        self.store.provision_full_access(self.project_id)
        target = self.outside / "existing.txt"
        target.write_text("original", encoding="utf-8")
        grant = self._drive_grant_for(self.outside)
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant["grant_id"],
                action=FileCapability.WRITE,
                path=str(target),
                content="clobbered",
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_a_drive_grant_cannot_reach_another_drive(self) -> None:
        drives = _local_drive_roots()
        if len(drives) < 2:
            self.skipTest("only one local drive is attached; cross-drive containment is not exercisable")
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        self.store.provision_full_access(self.project_id)
        source = self.outside.resolve()
        other = next(
            drive for drive in drives
            if drive.drive.upper() != source.drive.upper()
        )
        # Pick a real directory on the OTHER drive so the path itself exists;
        # only the grant's root is wrong.
        probe = next(
            (entry for entry in other.iterdir() if entry.is_dir() and not entry.is_symlink()),
            None,
        )
        if probe is None:
            self.skipTest("the other drive has no readable directory to probe")
        grant = self._drive_grant_for(source)
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant["grant_id"],
                action=FileCapability.CREATE,
                path=str(probe / "amca_escape_probe.txt"),
                content="no",
            )
        self.assertFalse((probe / "amca_escape_probe.txt").exists())

    def test_provisioned_grants_still_respect_the_approval_model(self) -> None:
        self._set_profile(PermissionProfile.FULL_COMPUTER)
        self.store.provision_full_access(self.project_id)
        target = self.outside / "to_trash.txt"
        target.write_text("x", encoding="utf-8")
        grant = self._drive_grant_for(self.outside)
        with self.assertRaises(PermissionError):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant["grant_id"],
                action=FileCapability.TRASH,
                path=str(target),
            )
        self.assertTrue(target.exists(), "a refused trash must leave the file alone")


class StandaloneChatAccessTests(unittest.TestCase):
    """A chat with no Project is still a place Local PC Access works."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.conversations = ConversationStore(self.database)
        self.conversation = self.conversations.create(title="Standalone chat")
        self.chat_id = f"conv-{self.conversation['conversation_id']}"
        self.store = LocalPermissionStore(self.database)
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_store_accepts_a_standalone_chat_identity(self) -> None:
        self.store.set_profile(
            project_id=self.chat_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=None,
        )
        scope = self.store.provision_full_access(self.chat_id)
        self.assertTrue(scope["active"])

    def test_approvals_can_be_requested_for_a_standalone_chat(self) -> None:
        # Regression: approvals validated only against projects/studio_projects,
        # so a conv-* chat could never raise one - which would have left a
        # standalone chat unable to complete any approval-gated operation.
        with PermissionStore(self.database) as permissions:
            request = permissions.request(
                project_id=self.chat_id,
                action=PermissionAction.LOCAL_FILE_MODIFY,
                scope={"path": str(self.outside / "note.txt")},
                reason="Approve this exact local operation",
                risk="high",
            )
        self.assertTrue(request.id)

    def test_an_invented_chat_id_is_still_refused(self) -> None:
        # The fix widened who may ask, never what exists: without a real
        # conversation row the level must not be raisable.
        with self.assertRaises(ValueError):
            self.store.set_profile(
                project_id="conv-does-not-exist",
                profile=PermissionProfile.FULL_COMPUTER,
                workspace_root=None,
            )

    def test_full_access_reaches_outside_the_chat_with_no_project_folder(self) -> None:
        self.store.set_profile(
            project_id=self.chat_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=None,
        )
        self.store.provision_full_access(self.chat_id)
        grant = next(
            g for g in self.store.grants(self.chat_id, active_only=True)
            if g["reason"] == _PROVISIONED_REASON
            and Path(g["canonical_root"]).drive.upper() == self.outside.resolve().drive.upper()
        )
        target = self.outside / "standalone.txt"
        executor = LocalToolExecutor(self.store, state_root=self.root / "state")
        executor.execute(
            project_id=self.chat_id,
            grant_id=grant["grant_id"],
            action=FileCapability.CREATE,
            path=str(target),
            content="no project needed",
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "no project needed")


class ReviewRemediationTests(unittest.TestCase):
    """Regressions for every finding raised by the v1.2.0 review round.

    Each test here corresponds to a defect the three independent reviews found
    in the first implementation. They are written to FAIL against that
    implementation, so they pin the fix rather than the intent.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.conversations = ConversationStore(self.database)
        first = self.conversations.create(title="First", conversation_id="chat-a")["conversation_id"]
        second = self.conversations.create(title="Second", conversation_id="chat-b")["conversation_id"]
        self.chat_a = f"conv-{first}"
        self.chat_b = f"conv-{second}"
        self.store = LocalPermissionStore(self.database)
        self.folder = self.root / "project"
        self.folder.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        # v1.2.0: the folder-bound levels belong to a Project.  A chat-prefixed
        # identity may not carry a folder, so every proof that needs one uses
        # this Project id, exactly as the desktop addresses a chat in a
        # Project.
        self.project_id = "studio-v120-review"
        StudioProjectStore(self.database).save(
            {
                "project_id": self.project_id,
                "name": "v1.2.0 review",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": str(self.folder),
            }
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    # -- new drives must stay uncovered until an explicit refresh -----------

    def test_run_admission_never_provisions_a_newly_attached_drive(self) -> None:
        """The first implementation provisioned at run admission.

        That made "Refresh drives" decorative: attaching a drive and starting
        any chat silently widened whole-PC access to it.
        """
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        before = self.store.provision_full_access(self.chat_a)
        self.assertTrue(before["drives_granted"], "setup must cover at least one drive")

        added = [Path("Z:\\")]
        with mock.patch(
            "amca.local_tools._local_drive_roots",
            return_value=[*_local_drive_roots(), *added],
        ):
            # Run admission, not the refresh button.
            scope = self.store.sync_full_access(self.chat_a)
        self.assertNotIn(
            "Z:\\", scope["drives_granted"],
            "a drive attached later must not be granted by merely starting a run",
        )

    def test_run_admission_provisions_the_frozen_scope_and_nothing_wider(self) -> None:
        """Run admission catches up THIS identity; it never widens the scope.

        The first implementation never provisioned at run admission, so a
        Project created after Full Access was switched on kept no whole-PC
        grant and every tool on the machine was denied - found by driving the
        installed app, not by a test.  The second implementation re-read the
        live drive list, which made "Refresh drives" decorative again.  The
        property that satisfies both: run admission provisions the drives
        frozen when the level was turned on, and a drive attached since then
        stays uncovered until the explicit refresh.
        """
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )

        # An identity that comes into existence after the level was switched
        # on: same promise, same coverage, no user action needed.
        with mock.patch("amca.local_tools._local_drive_roots", return_value=[Path("Z:\\")]):
            scope = self.store.sync_full_access(self.chat_b)
        self.assertTrue(
            self.store.grants(self.chat_b, active_only=True),
            "an identity created after Full Access was enabled got no whole-PC grant",
        )
        self.assertNotIn(
            "Z:\\", scope["drives_granted"],
            "run admission must not widen the scope to a newly attached drive",
        )

    def test_an_identity_created_after_the_switch_reaches_the_whole_pc(self) -> None:
        """The physical-GUI defect, pinned: Full Access means every chat.

        A Project created while the level was already Full Access had no
        provisioned grants at all, so a write to a path outside the project
        folder was denied with "outside every active path grant" while the UI
        plainly said Full Access.
        """
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        # The Project is created afterwards - it was not in the known set
        # when the level changed, which is exactly the failing case.
        self.later = "studio-created-later"
        StudioProjectStore(self.database).save(
            {
                "project_id": self.later,
                "name": "Created later",
                "root_path": str(self.folder),
            }
        )
        self.assertFalse(
            self.store.grants(self.later, active_only=True),
            "precondition: the new Project starts with no grant",
        )

        self.store.sync_full_access(self.later)
        drive_root = _local_drive_roots()[0]
        granted = self.store.grants(self.later, active_only=True)
        self.assertTrue(granted, "the later Project still has no whole-PC grant")
        self.assertIn(
            os.path.normcase(str(drive_root)),
            {os.path.normcase(str(grant["canonical_root"])) for grant in granted},
        )

    def test_explicit_refresh_is_what_provisions(self) -> None:
        """Only the explicit action re-freezes the drive scope.

        Enumeration is stubbed so the assertion is about WHICH path re-freezes,
        not about which drives this particular machine happens to have.
        """
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        with mock.patch("amca.local_tools._local_drive_roots", return_value=_local_drive_roots()):
            self.store.apply_profile_transition(
                project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
            )
            self.assertTrue(self.store.grants(self.chat_a, active_only=True))

        with mock.patch(
            "amca.local_tools._local_drive_roots",
            return_value=[*_local_drive_roots(), Path("Z:\\")],
        ):
            # A run never re-freezes the scope.
            self.store.sync_full_access(self.chat_a)
            self.assertNotIn(
                "Z:\\", self.store._full_access_scope_drives_text(),
            )
            # The explicit refresh does.  Z:\ is not attached here, so it can
            # be in the frozen scope without being a grantable drive - which
            # is exactly why the scope, not the grant, is the thing to assert.
            self.store.refresh_full_access_scope()
        self.assertIn("Z:\\", self.store._full_access_scope_drives_text())

    def test_one_ungrantable_drive_does_not_cost_the_others(self) -> None:
        """A drive that vanishes mid-provision costs that drive only."""
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        real = _local_drive_roots()
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.revoke_full_access(self.chat_a)
        with mock.patch(
            "amca.local_tools._local_drive_roots",
            return_value=[Path("Z:\\"), *real],
        ):
            self.store.provision_full_access(self.chat_a)
        granted = {
            os.path.normcase(str(Path(g["canonical_root"])))
            for g in self.store.grants(self.chat_a, active_only=True)
        }
        for drive in real:
            self.assertIn(os.path.normcase(str(drive)), granted)

    # -- the level is global, so reconciliation must be global --------------

    def test_leaving_full_access_revokes_every_identity_not_just_the_caller(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.provision_full_access(self.chat_a)
        self.store.provision_full_access(self.chat_b)
        self.assertTrue(self.store.grants(self.chat_b, active_only=True))

        # The level is global; a downgrade from ANY chat must clear them all.
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.RESTRICTED
        )
        for identity in (self.chat_a, self.chat_b):
            self.assertFalse(
                self.store.grants(identity, active_only=True),
                f"{identity} kept machine-wide grants after the level was lowered",
            )

    def test_enabling_full_access_provisions_every_identity(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        for identity in (self.chat_a, self.chat_b):
            self.assertTrue(
                self.store.grants(identity, active_only=True),
                f"{identity} has no whole-PC grant, so the global promise is a lie",
            )

    def test_provisioned_grant_is_inert_when_the_level_is_not_full(self) -> None:
        """Defence in depth: a missed revocation must not stay live."""
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.provision_full_access(self.chat_a)
        grant = next(
            g for g in self.store.grants(self.chat_a, active_only=True)
            if Path(g["canonical_root"]).drive.upper() == self.outside.resolve().drive.upper()
        )
        # Drop the level WITHOUT revoking, simulating a lag or partial failure.
        self.store._connection.execute(
            "UPDATE local_permission_policy SET profile='restricted'"
        )
        self.store._connection.commit()
        executor = LocalToolExecutor(self.store, state_root=self.root / "state")
        with self.assertRaises(PermissionError):
            executor.execute(
                project_id=self.chat_a,
                grant_id=grant["grant_id"],
                action=FileCapability.LIST,
                path=".",
            )

    # -- provisioned ownership must not be a caller-chosen string -----------

    def test_a_hand_made_grant_cannot_impersonate_a_provisioned_one(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        impostor = self.store.create_grant(
            project_id=self.chat_a,
            root_path=self.folder,
            capabilities=[FileCapability.READ],
            reason=_PROVISIONED_REASON,
            created_by="desktop_user",
        )
        self.assertFalse(
            impostor.provisioned,
            "a caller-supplied reason must never mark a grant as AMCA-owned",
        )
        # Provisioning must still add the real whole-PC grant alongside it.
        self.store.provision_full_access(self.chat_a)
        owned = [g for g in self.store.grants(self.chat_a, active_only=True) if g["provisioned"]]
        self.assertTrue(owned, "the real provisioned grant was suppressed by the impostor")

    def test_a_hand_made_grant_is_never_revoked_by_a_downgrade(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        manual = self.store.create_grant(
            project_id=self.chat_a,
            root_path=self.folder,
            capabilities=[FileCapability.READ],
            reason=_PROVISIONED_REASON,
            created_by="desktop_user",
        )
        self.store.apply_profile_transition(
            project_id=self.chat_a, profile=PermissionProfile.RESTRICTED
        )
        self.assertTrue(
            manual.active,
            "a user-created grant must not be swept up by a machine-wide revocation, "
            "even when it borrowed the provisioned reason text",
        )

    # -- only Full Access is PC-wide ---------------------------------------

    def test_extended_is_bounded_by_the_project_folder(self) -> None:
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=self.folder,
        )
        with self.assertRaisesRegex(PermissionError, "inside the project workspace"):
            self.store.create_grant(
                project_id=self.project_id,
                root_path=self.outside,
                capabilities=[FileCapability.MOVE],
                reason="Outside the project folder",
            )

    def test_extended_still_gains_its_capabilities_inside_the_folder(self) -> None:
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=self.folder,
        )
        grant = self.store.create_grant(
            project_id=self.project_id,
            root_path=self.folder,
            capabilities=[FileCapability.MOVE, FileCapability.TRASH],
            reason="Inside the project folder",
        )
        self.assertIn(FileCapability.MOVE.value, grant.capabilities)
        self.assertIn(FileCapability.TRASH.value, grant.capabilities)

    def test_a_network_root_is_never_grantable(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        with self.assertRaises(PermissionError):
            self.store.create_grant(
                project_id=self.chat_a,
                root_path="//server/share/team",
                capabilities=[FileCapability.READ],
                reason="A network share is not the local PC",
            )

    # -- drive classification -----------------------------------------------

    def test_only_fixed_drives_are_enumerated(self) -> None:
        """A USB stick or mapped share presents as X:\\ just like C:\\."""
        real = _local_drive_roots()
        self.assertTrue(real, "the test machine must have at least one fixed drive")
        # Every returned root must be a real, existing local directory.
        for drive in real:
            self.assertTrue(drive.is_dir())
            self.assertFalse(str(drive).startswith("\\\\"))

    def test_non_fixed_drive_types_are_filtered(self) -> None:
        """GetDriveTypeW must gate the enumeration, not merely the letter."""
        import ctypes

        class FakeKernel:
            @staticmethod
            def GetLogicalDrives():
                return 0b11100  # C, D, E all present

            @staticmethod
            def GetDriveTypeW(letter):
                return {"C:\\": 3, "D:\\": 2, "E:\\": 4}[letter]  # fixed, removable, remote

        with mock.patch("ctypes.windll", mock.Mock(kernel32=FakeKernel)):
            with mock.patch.object(Path, "is_dir", return_value=True):
                roots = _local_drive_roots()
        self.assertEqual([str(item) for item in roots], ["C:\\"])

    # -- identity lifecycle -------------------------------------------------

    def test_a_deleted_identity_cannot_keep_using_its_grants(self) -> None:
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        self.store.provision_full_access(self.chat_a)
        grant = next(
            g for g in self.store.grants(self.chat_a, active_only=True)
            if Path(g["canonical_root"]).drive.upper() == self.outside.resolve().drive.upper()
        )
        self.conversations.delete("chat-a")
        self.store.revoke_identity(self.chat_a)
        executor = LocalToolExecutor(self.store, state_root=self.root / "state")
        with self.assertRaises(PermissionError):
            executor.execute(
                project_id=self.chat_a,
                grant_id=grant["grant_id"],
                action=FileCapability.LIST,
                path=".",
            )

    def test_a_project_may_not_take_a_chat_identity(self) -> None:
        projects = StudioProjectStore(self.database)
        with self.assertRaisesRegex(ValueError, "conv-"):
            projects.save({"project_id": "conv-chat-a", "name": "Impostor",
                           "root_path": str(self.root / "impostor")})

    # -- project folder hardening -------------------------------------------

    def test_unc_and_device_paths_are_rejected_in_both_spellings(self) -> None:
        for candidate in (
            "//server/share/team",
            r"\\server\share\team",
            "//?/C:/Windows",
            r"\\?\C:\Windows",
            r"\\.\PhysicalDrive0",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "local path"):
                    _validate_project_folder(candidate)

    def test_the_write_probe_never_touches_a_user_file_of_the_same_name(self) -> None:
        projects = StudioProjectStore(self.database)
        target = self.root / "with-user-file"
        target.mkdir()
        keeper = target / ".amca-write-probe"
        keeper.write_text("the user's own data", encoding="utf-8")
        created = projects.save({"name": "WithUserFile", "root_path": str(target)})
        self.assertIsNotNone(created)
        self.assertEqual(
            keeper.read_text(encoding="utf-8"),
            "the user's own data",
            "AMCA's writability check overwrote and deleted a user file",
        )

    def test_a_legacy_project_without_a_folder_can_still_be_edited(self) -> None:
        """A project from before folders existed must not become uneditable."""
        projects = StudioProjectStore(self.database)
        with projects._connect() as connection:
            connection.execute(
                "INSERT INTO studio_projects (project_id,name,description,instructions,"
                "template,created_at,updated_at,archived,root_path) "
                "VALUES ('studio-legacy','Legacy','','','general',?,?,0,NULL)",
                ("2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"),
            )
        updated = projects.save({"project_id": "studio-legacy", "name": "Legacy renamed"})
        self.assertEqual(updated["name"], "Legacy renamed")
        self.assertIsNone(updated["root_path"], "no folder may be invented for it")
        self.assertFalse((self.root / "Legacy").exists())


class ReReviewRemediationTests(unittest.TestCase):
    """Regressions for the four findings of the security re-review.

    The first remediation pass closed the review's headline findings but left
    four holes behind it.  Each test below fails against that pass.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.conversations = ConversationStore(self.database)
        created = self.conversations.create(
            title="Re-review", conversation_id="chat-a"
        )["conversation_id"]
        self.chat_a = f"conv-{created}"
        self.store = LocalPermissionStore(self.database)
        self.folder = self.root / "project"
        self.folder.mkdir()
        self.store.set_profile(
            project_id=self.chat_a, profile=PermissionProfile.FULL_COMPUTER
        )
        # v1.2.0: a folder-bound level belongs to a Project; a chat-prefixed
        # identity may not carry a folder.  The proofs that need one address
        # this Project, the way the desktop addresses a chat in a Project.
        self.project_id = "studio-v120-rereview"
        StudioProjectStore(self.database).save(
            {
                "project_id": self.project_id,
                "name": "v1.2.0 re-review",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": str(self.folder),
            }
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    # -- blocker 1: a caller cannot mint an AMCA-owned grant -----------------

    def test_the_public_grant_signature_offers_no_ownership_flag(self) -> None:
        parameters = inspect.signature(self.store.create_grant).parameters
        self.assertNotIn(
            "provisioned",
            parameters,
            "provisioned must not be settable through the public grant route",
        )
        self.assertNotIn("allow_network_root", parameters)

    # -- blocker 2: an existing project's folder is immutable ----------------

    def test_an_existing_project_folder_cannot_be_replaced(self) -> None:
        projects = StudioProjectStore(self.database)
        first = self.root / "first-folder"
        second = self.root / "second-folder"
        created = projects.save(
            {"project_id": "studio-frozen", "name": "Frozen",
             "root_path": str(first)}
        )
        self.assertTrue(
            same_folder(str(created["root_path"]), str(first)),
            f"the recorded folder {created['root_path']} is not {first}",
        )
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            projects.save(
                {"project_id": "studio-frozen", "name": "Frozen",
                 "root_path": str(second)}
            )
        stored = projects.get("studio-frozen")
        self.assertTrue(same_folder(str(stored["root_path"]), str(first)))
        self.assertFalse(second.exists(), "the refused folder must not be created")

    def test_resaving_the_same_folder_in_another_spelling_is_allowed(self) -> None:
        projects = StudioProjectStore(self.database)
        first = self.root / "same-folder"
        projects.save(
            {"project_id": "studio-same", "name": "Same", "root_path": str(first)}
        )
        trailing = str(first) + os.sep
        updated = projects.save(
            {"project_id": "studio-same", "name": "Same renamed",
             "root_path": trailing}
        )
        self.assertEqual(updated["name"], "Same renamed")
        self.assertTrue(same_folder(trailing, str(first)))

    # -- blocker 3: restore is reachable through the public service route ----

    def test_restore_is_proposable_through_the_service_route(self) -> None:
        benchmarks = self.root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        context = ServiceContext(
            root=self.root, database=self.root / "service.sqlite3",
            benchmarks=benchmarks,
        )
        try:
            context.conversations.create(
                title="Restore route", conversation_id="chat-restore"
            )
            chat = "conv-chat-restore"
            context.set_local_permission_profile(
                {"project_id": chat, "profile": PermissionProfile.FULL_COMPUTER.value}
            )
            grant = context.create_local_grant(
                {
                    "project_id": chat,
                    "root_path": str(self.folder),
                    "capabilities": [
                        FileCapability.CREATE.value, FileCapability.WRITE.value,
                        FileCapability.MOVE.value, FileCapability.TRASH.value,
                        FileCapability.RESTORE.value,
                    ],
                    "reason": "re-review restore route",
                }
            )
            victim = self.folder / "restorable.txt"
            context.local_tool_executor.execute(
                project_id=chat,
                grant_id=grant["grant_id"],
                action=FileCapability.CREATE,
                path="restorable.txt",
                content="original",
            )
            self.assertTrue(victim.exists())

            trash_proposal = context.propose_local_filesystem_approval(
                {
                    "project_id": chat,
                    "grant_id": grant["grant_id"],
                    "capability": "trash",
                    "approval_action": PermissionAction.LOCAL_FILE_TRASH.value,
                    "source": "restorable.txt",
                    "reason": "remove it",
                }
            )
            trash_approval = str(trash_proposal["approval"]["id"])
            context.decide_approval(trash_approval, approve=True)
            trashed = context.local_tool_executor.execute(
                project_id=chat,
                grant_id=grant["grant_id"],
                action=FileCapability.TRASH,
                path="restorable.txt",
                approval_id=trash_approval,
            )
            trash_id = str(trashed["trash_id"])
            self.assertFalse(victim.exists())

            # The route the desktop and the governed chat both use.  Before the
            # fix it forwarded no trash_id, so restore raised
            # "trash_id is required" and could never be reached by any user.
            restore_proposal = context.propose_local_filesystem_approval(
                {
                    "project_id": chat,
                    "grant_id": grant["grant_id"],
                    "capability": "restore",
                    "approval_action": PermissionAction.LOCAL_FILE_MOVE.value,
                    "source": trash_id,
                    "trash_id": trash_id,
                    "reason": "put it back",
                }
            )
            restore_approval = str(restore_proposal["approval"]["id"])
            context.decide_approval(restore_approval, approve=True)
            context.local_tool_executor.execute(
                project_id=chat,
                grant_id=grant["grant_id"],
                action=FileCapability.RESTORE,
                trash_id=trash_id,
                approval_id=restore_approval,
            )
            self.assertTrue(victim.exists(), "the file was not restored")
        finally:
            context.close()

    # -- blocker 4: the chat namespace is disjoint from the project one ------

    def test_a_canonical_project_cannot_resurrect_a_deleted_chat(self) -> None:
        """A legacy project row literally named conv-<id> used to win the
        exact-match lookup, so the chat identity stayed alive - and kept its
        grants - after the conversation behind it was deleted."""
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "project_id TEXT PRIMARY KEY, name TEXT)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO projects (project_id,name) VALUES (?,?)",
                (self.chat_a, "Impostor project"),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertTrue(
            self.store._project_exists(self.chat_a),
            "the live conversation must resolve through its own row",
        )

        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "DELETE FROM desktop_conversations WHERE conversation_id=?",
                ("chat-a",),
            )
            connection.commit()
        finally:
            connection.close()

        self.assertFalse(
            self.store._project_exists(self.chat_a),
            "a project row must not keep a deleted chat identity alive",
        )
        self.assertNotIn(
            self.chat_a,
            self.store._known_project_ids(),
            "a project row in the chat namespace must never be provisioned",
        )

    def test_a_project_row_with_a_chat_prefix_is_not_addressable(self) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                "project_id TEXT PRIMARY KEY, name TEXT)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO projects (project_id,name) VALUES (?,?)",
                ("conv-not-a-chat", "Ghost"),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(self.store._project_exists("conv-not-a-chat"))
        self.assertNotIn("conv-not-a-chat", self.store._known_project_ids())

    # -- re-review round 2: the two findings that survived the first fix ---

    def test_a_junction_swapped_project_folder_cannot_move_the_boundary(self) -> None:
        """The recorded folder is the boundary, so resolving it with
        ``Path.resolve`` alone let a replaced folder hand the boundary to the
        link target."""
        if os.name != "nt":
            self.skipTest("junction behaviour is Windows-specific")
        import subprocess

        real = self.root / "real-target"
        real.mkdir()
        (real / "secret.txt").write_text("must stay unreachable", encoding="utf-8")
        store = LocalPermissionStore(self.database)
        store.set_profile(
            project_id=self.project_id, profile=PermissionProfile.EXTENDED,
            workspace_root=str(self.folder),
        )
        # Replace the recorded folder with a junction pointing elsewhere.
        moved = self.root / "moved-away"
        self.folder.rename(moved)
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.folder), str(real)],
            check=True, capture_output=True,
        )
        try:
            with self.assertRaises(PermissionError):
                store.set_profile(
                    project_id=self.project_id, profile=PermissionProfile.EXTENDED,
                )
            with self.assertRaises(PermissionError):
                store.create_grant(
                    project_id=self.project_id,
                    root_path=str(real),
                    capabilities=[FileCapability.READ],
                    reason="junction swap attempt",
                )
        finally:
            store.close()
            os.rmdir(self.folder)

    def test_a_chat_identity_never_takes_a_folder_from_a_project_row(self) -> None:
        """A project row carrying the chat spelling must not supply the
        boundary for the conversation identity the local store resolves."""
        projects = StudioProjectStore(self.database)
        impostor = self.root / "impostor-folder"
        with projects._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO studio_projects "
                "(project_id,name,description,instructions,template,"
                "created_at,updated_at,archived,root_path) "
                "VALUES (?,?,'','','general',?,?,0,?)",
                (
                    self.chat_a, "Impostor", "2024-01-01T00:00:00Z",
                    "2024-01-01T00:00:00Z", str(impostor),
                ),
            )
        context = ServiceContext.__new__(ServiceContext)
        context.studio_project_store = projects
        self.assertIsNone(
            context._authoritative_workspace_root(self.chat_a, None),
            "the chat namespace must not resolve to a project row's folder",
        )
        self.assertEqual(
            context._authoritative_workspace_root(self.chat_a, str(self.folder)),
            str(self.folder),
            "a folder stated for the chat itself is still honoured",
        )

    def test_a_project_may_not_take_either_chat_prefix(self) -> None:
        projects = StudioProjectStore(self.database)
        for candidate in ("conv-chat-a", "desktop-chat-a"):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "chat prefix"):
                    projects.save({"project_id": candidate, "name": "Impostor",
                                   "root_path": str(self.root / "x")})

    def test_a_legacy_chat_workspace_row_is_purged_on_open(self) -> None:
        """An earlier build could record a colliding project folder against a
        conv- row, and the UI posted that value straight back.  The row is
        seeded through SQL because the API now refuses to create one."""
        grant = self.store.create_grant(
            project_id=self.chat_a, root_path=str(self.folder),
            capabilities=[FileCapability.READ], reason="legacy",
        )
        self.store.close()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO local_permission_profiles "
                "(project_id,profile,workspace_root,updated_at,updated_by) "
                "VALUES (?,?,?,?,?)",
                (
                    self.chat_a, PermissionProfile.EXTENDED.value,
                    str(self.root / "elsewhere"), "2024-01-01T00:00:00Z",
                    "legacy-build",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        reopened = LocalPermissionStore(self.database)
        try:
            row = reopened.profile(self.chat_a)
            self.assertIsNone(
                row["workspace_root"],
                "a chat-prefixed row must not keep a project folder",
            )
            revoked = reopened.grant(grant.grant_id)
            self.assertIsNotNone(revoked, "the grant row is kept for the audit trail")
            self.assertIsNotNone(
                revoked.revoked_at,
                "a grant bounded by the purged folder must be revoked",
            )
        finally:
            reopened.close()
        # Idempotent: opening again changes nothing and does not raise.
        again = LocalPermissionStore(self.database)
        again.close()

    def test_a_chat_identity_is_refused_a_folder_outright(self) -> None:
        """The invariant is total: the store will not let a chat take a
        folder, so the purge above only ever has legacy rows to clean."""
        with self.assertRaisesRegex(ValueError, "standalone chat has no Project folder"):
            self.store.set_profile(
                project_id=self.chat_a,
                profile=PermissionProfile.EXTENDED,
                workspace_root=str(self.folder),
            )

    def test_the_workspace_resolver_refuses_network_and_device_paths(self) -> None:
        for candidate in (r"\\server\share\team", r"\\?\C:\Windows", r"\\.\C:\Windows"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(PermissionError):
                    self.store.set_profile(
                        project_id=self.project_id,
                        profile=PermissionProfile.EXTENDED,
                        workspace_root=candidate,
                    )


class MultiRootGrantSelectionTests(unittest.TestCase):
    """One identity, several whole-PC drive grants: the path must decide.

    Full Access grants a drive at a time, so a single identity normally holds
    several grants.  Every selection path that asked for "a grant that can do
    X" rather than "the grant that covers this path" picked whichever came
    first, which made a legitimate C:\\ read or write fail against the grant for
    another drive.  These tests fail against that behaviour.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.conversations = ConversationStore(self.database)
        created = self.conversations.create(
            title="Multi-root", conversation_id="chat-multi"
        )["conversation_id"]
        self.chat_id = f"conv-{created}"
        self.store = LocalPermissionStore(self.database)
        self.store.set_profile(
            project_id=self.chat_id, profile=PermissionProfile.FULL_COMPUTER
        )
        # Two real directories stand in for two drive roots: containment,
        # lexical roots and reparse checks treat them exactly as they treat
        # C:\\ and H:\\, and the test stays true on a machine with one drive.
        self.drive_one = self.root / "drive-one"
        self.drive_two = self.root / "drive-two"
        self.drive_one.mkdir()
        self.drive_two.mkdir()
        self.store.provision_full_access(
            self.chat_id, drives=[self.drive_one, self.drive_two]
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.root / "state")
        self.grants = {
            # The grant records the canonical root, which on Windows is the
            # resolved (long) spelling, so the lookup key is resolved too.
            os.path.normcase(str(Path(g["canonical_root"]).resolve())): str(g["grant_id"])
            for g in self.store.grants(self.chat_id, active_only=True)
            if g["reason"] == _PROVISIONED_REASON
        }
        # The store's own iteration order decides which root a path-blind
        # selector would pick.  The target is deliberately filed under the one
        # that comes LAST, so the test cannot pass by accident: before the fix
        # the first grant won, and the target was in the other one.
        order = [
            os.path.normcase(str(Path(g["canonical_root"]).resolve()))
            for g in self.store.grants(self.chat_id, active_only=True)
            if g["reason"] == _PROVISIONED_REASON
        ]
        self.other_drive = Path(order[0])
        self.second_drive = Path(order[-1])

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def grant_for(self, drive: Path) -> str:
        return self.grants[os.path.normcase(str(drive.resolve()))]

    def second_drive_note(self) -> Path:
        target = self.second_drive / "notes" / "second.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text("on the second drive", encoding="utf-8")
        return target

    def test_both_roots_really_were_provisioned(self) -> None:
        # Guard the guard: with only one grant the tests below would pass for
        # the wrong reason.
        self.assertEqual(len(self.grants), 2)
        self.assertNotEqual(self.other_drive, self.second_drive)

    def test_a_path_on_the_second_root_resolves_to_that_roots_grant(self) -> None:
        target = self.second_drive_note()
        resolution = self.executor.resolve_mutation_target(
            project_id=self.chat_id, action="stat", path=str(target)
        )
        self.assertTrue(resolution["matched"], resolution.get("reason"))
        self.assertEqual(
            str(resolution["grant_id"]), self.grant_for(self.second_drive),
            "the grant that contains the target must govern it",
        )

    def test_a_relative_path_binds_to_the_root_that_actually_holds_it(self) -> None:
        # The same target spelled without its root.  Before the fix every
        # granted root matched a relative spelling and the first one won.
        self.second_drive_note()
        resolution = self.executor.resolve_mutation_target(
            project_id=self.chat_id, action="stat", path="notes/second.txt",
        )
        self.assertTrue(resolution["matched"], resolution.get("reason"))
        self.assertEqual(str(resolution["grant_id"]), self.grant_for(self.second_drive))

    def test_a_relative_path_no_root_holds_still_resolves(self) -> None:
        # A stat of a file that does not exist yet must still resolve to a
        # grant, so the tool can answer "false" instead of refusing to look.
        # Refusing every unresolvable relative spelling would have broken that
        # contract, which is why containment is a preference here and not a
        # gate.
        resolution = self.executor.resolve_mutation_target(
            project_id=self.chat_id, action="stat", path="notes/nowhere.txt"
        )
        self.assertTrue(resolution["matched"], resolution.get("reason"))
        self.assertFalse(resolution["target_exists"])
        self.assertIn(str(resolution["grant_id"]), self.grants.values())

    def test_a_path_outside_every_root_is_still_refused(self) -> None:
        # Containment is the point of per-drive grants; disambiguating between
        # roots must not have opened the space between them.
        outside = self.root / "between-the-drives" / "sneaky.txt"
        outside.parent.mkdir()
        outside.write_text("not granted", encoding="utf-8")
        resolution = self.executor.resolve_mutation_target(
            project_id=self.chat_id, action="stat", path=str(outside)
        )
        self.assertFalse(resolution["matched"])
        self.assertIsNone(resolution.get("grant_id"))

    def test_the_read_dispatch_resolves_the_grant_from_the_path(self) -> None:
        # The defect as it reached the user: the read/stat branch of the
        # governed adapter asked for a grant by capability alone, so a read of
        # a path on the second drive was attempted against the first drive's
        # grant and refused for escaping a root it never left.
        source = Path(inspect.getsourcefile(GovernedChatAdapter) or "")
        pathless = [
            line for line in source.read_text(encoding="utf-8").splitlines()
            if "_agent_grant_for(self.project_id, action)" in line
        ]
        self.assertEqual(
            pathless, [], "read/stat must resolve the grant from the requested path",
        )
        target = self.second_drive_note()
        grant_id = self.executor.resolve_mutation_target(
            project_id=self.chat_id, action="read", path=str(target)
        )["grant_id"]
        result = self.executor.execute(
            project_id=self.chat_id, grant_id=str(grant_id),
            action=FileCapability.READ, path=str(target),
        )
        self.assertIn("on the second drive", str(result.get("content")))


if __name__ == "__main__":
    unittest.main()
