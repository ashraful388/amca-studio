"""AMCA Studio v1.1.8 Final Build Plan - Phase 5 evidence (Local PC access).

Phase 5 requires:
  * one authoritative global PC permission state, identical for every model,
    chat and project;
  * four distinct, fixed permission presets where each higher level adds a
    clear capability tier and Full Computer enables every permission;
  * folder grants to stay path-specific;
  * command execution to stay behind an exact, approved approval.

Every test below asserts the plan's requirement, not the implementation.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from amca.domain import ProjectState
from amca.governed_chat import get_governed_tools_for_profile
from amca.local_tools import (
    PROFILE_CAPABILITIES,
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.permissions import PermissionAction, PermissionStore
from amca.storage import SQLiteStore
from amca.studio_projects import StudioProjectStore


def conversation_ref(database: Path, title: str = "Chat") -> str:
    """Create a real conversation and return the id form the desktop sends.

    Since v1.1.8 the PC access level is global, so a level change must name a
    real chat or project - an invented id is refused.  Tests therefore seed a
    real row instead of passing a synthetic ``conv-`` string.
    """
    from amca.chat_history import ConversationStore

    created = ConversationStore(database).create(title=title)
    return f"conv-{created['conversation_id']}"


def studio_project_ref(database: Path, name: str, root: Path) -> str:
    """Register a Project that owns ``root`` and return the id the desktop sends.

    Since v1.2.0 the folder belongs to the Project, never to a chat: a chat in a
    Project is addressed by that Project's id, and a standalone chat has no
    folder at all.  Tests that exercise a folder therefore name a Project.
    """
    StudioProjectStore(database).save(
        {"project_id": f"studio-{name}", "name": name, "root_path": str(root)}
    )
    return f"studio-{name}"


class GlobalPcAccessLevelTests(unittest.TestCase):
    """The PC access level is global; only the workspace folder is per-project."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.store = LocalPermissionStore(self.database)
        # The level is global, so a caller must name a real chat or project:
        # these ids resolve to real rows, exactly as the desktop's do.
        self.chats = [conversation_ref(self.database, f"Chat {index}") for index in range(4)]

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_level_is_identical_for_every_chat_and_project(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        project = studio_project_ref(self.database, "phase5-level", workspace)
        self.store.set_profile(
            project_id=project,
            profile=PermissionProfile.EXTENDED,
            workspace_root=workspace,
        )
        expected = sorted(
            item.value for item in PROFILE_CAPABILITIES[PermissionProfile.EXTENDED]
        )
        for project_id in [*self.chats, project]:
            policy = self.store.profile(project_id)
            self.assertEqual(policy["profile"], "extended", project_id)
            self.assertTrue(policy["configured"], project_id)
            self.assertEqual(sorted(policy["capabilities"]), expected, project_id)

    def test_workspace_root_stays_scoped_to_its_own_project(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        project = studio_project_ref(self.database, "phase5-folder", workspace)
        self.store.set_profile(
            project_id=project,
            profile=PermissionProfile.STANDARD,
            workspace_root=workspace,
        )
        self.assertEqual(
            self.store.profile(project)["workspace_root"],
            str(workspace.resolve()),
        )
        # The level is global; the folder is not.  A chat sees the level and
        # never inherits another identity's folder.
        for chat in self.chats:
            self.assertIsNone(self.store.profile(chat)["workspace_root"], chat)
            self.assertEqual(self.store.profile(chat)["profile"], "standard", chat)

    def test_raising_the_level_does_not_require_a_folder(self):
        policy = self.store.set_profile(
            project_id=self.chats[0], profile=PermissionProfile.RESTRICTED
        )
        self.assertEqual(policy["profile"], "restricted")
        self.assertTrue(policy["configured"])
        self.assertIsNone(policy["workspace_root"])

    def test_fresh_install_is_restricted_and_unconfigured(self):
        policy = self.store.profile(self.chats[0])
        self.assertEqual(policy["profile"], "restricted")
        self.assertFalse(policy["configured"])

    def test_an_invented_chat_id_cannot_raise_the_global_level(self):
        """The prefix alone must never be enough to move a global setting."""
        for invented in (
            "conv-",
            "conv-00000000000000000000000000000000",
            "desktop-invented",
            "studio-invented",
        ):
            with self.assertRaisesRegex(ValueError, "project does not exist"):
                self.store.set_profile(
                    project_id=invented, profile=PermissionProfile.FULL_COMPUTER
                )
        self.assertEqual(self.store.profile(self.chats[0])["profile"], "restricted")

    def test_a_level_change_is_audited(self):
        self.store.set_profile(
            project_id=self.chats[0], profile=PermissionProfile.EXTENDED
        )
        rows = self.store._connection.execute(
            "SELECT * FROM local_tool_events WHERE action=?",
            ("permission_level_changed",),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        detail = json.loads(rows[0]["detail_json"])
        self.assertEqual(detail["from"], "restricted")
        self.assertEqual(detail["to"], "extended")
        self.assertEqual(detail["requested_project_id"], self.chats[0])

class PermissionPresetTests(unittest.TestCase):
    """The four presets are distinct, fixed and nested."""

    def test_each_level_is_a_strict_superset_of_the_one_below(self):
        order = [
            PermissionProfile.RESTRICTED,
            PermissionProfile.STANDARD,
            PermissionProfile.EXTENDED,
            PermissionProfile.FULL_COMPUTER,
        ]
        previous: frozenset[FileCapability] = frozenset()
        for level in order:
            current = PROFILE_CAPABILITIES[level]
            self.assertTrue(previous < current, f"{level.value} must add capability")
            previous = current

    def test_restricted_is_the_minimum_safe_set(self):
        self.assertEqual(
            PROFILE_CAPABILITIES[PermissionProfile.RESTRICTED],
            frozenset(
                {
                    FileCapability.LIST,
                    FileCapability.STAT,
                    FileCapability.SEARCH,
                    FileCapability.READ,
                }
            ),
        )

    def test_standard_is_normal_file_operations_without_destructive_actions(self):
        caps = PROFILE_CAPABILITIES[PermissionProfile.STANDARD]
        self.assertLessEqual(
            {
                FileCapability.MKDIR,
                FileCapability.CREATE,
                FileCapability.WRITE,
                FileCapability.APPEND,
                FileCapability.COPY,
            },
            caps,
        )
        for destructive in (
            FileCapability.MOVE,
            FileCapability.TRASH,
            FileCapability.RESTORE,
        ):
            self.assertNotIn(destructive, caps)

    def test_extended_adds_the_destructive_file_workflow(self):
        caps = PROFILE_CAPABILITIES[PermissionProfile.EXTENDED]
        self.assertLessEqual(
            {
                FileCapability.MOVE,
                FileCapability.TRASH,
                FileCapability.RESTORE,
                FileCapability.PROPOSE_COMMAND,
            },
            caps,
        )
        self.assertNotIn(FileCapability.PROPOSE_APPLICATION, caps)

    def test_full_computer_enables_every_capability(self):
        self.assertEqual(
            PROFILE_CAPABILITIES[PermissionProfile.FULL_COMPUTER],
            frozenset(FileCapability),
        )

    def test_every_offered_tool_is_executable_under_its_own_profile(self):
        """The regression that started this: a preset must never advertise a
        tool whose execution the same preset denies."""
        required = {
            "filesystem.read": FileCapability.READ,
            "filesystem.list": FileCapability.LIST,
            "filesystem.stat": FileCapability.STAT,
            "filesystem.search": FileCapability.SEARCH,
            "filesystem.write": FileCapability.WRITE,
            "filesystem.append": FileCapability.APPEND,
            "filesystem.copy": FileCapability.COPY,
            "filesystem.move": FileCapability.MOVE,
            "filesystem.trash": FileCapability.TRASH,
            "command.inspect": FileCapability.PROPOSE_COMMAND,
            "command.run": FileCapability.PROPOSE_APPLICATION,
        }
        for level in PermissionProfile:
            caps = PROFILE_CAPABILITIES[level]
            for tool in get_governed_tools_for_profile(level):
                name = str(tool["name"])
                self.assertIn(
                    required[name],
                    caps,
                    f"{level.value} offers {name} without the matching capability",
                )

    def test_standard_never_offers_the_trash_tool(self):
        names = {
            t["name"] for t in get_governed_tools_for_profile(PermissionProfile.STANDARD)
        }
        self.assertNotIn("filesystem.trash", names)

    def test_trash_tool_appears_exactly_from_the_extended_level(self):
        for level in (PermissionProfile.RESTRICTED, PermissionProfile.STANDARD):
            names = {t["name"] for t in get_governed_tools_for_profile(level)}
            self.assertNotIn("filesystem.trash", names, level.value)
        for level in (PermissionProfile.EXTENDED, PermissionProfile.FULL_COMPUTER):
            names = {t["name"] for t in get_governed_tools_for_profile(level)}
            self.assertIn("filesystem.trash", names, level.value)


class PathAwareGrantSelectionTests(unittest.TestCase):
    """Grant selection must follow the target path, not the creation order."""

    def setUp(self) -> None:
        from amca.service import ServiceContext

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.context = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=self.root / "benchmarks.json",
        )
        self.project_id = studio_project_ref(
            self.context.database, "grants-chat", self.workspace
        )
        with SQLiteStore(self.context.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="read"))
        self.context.local_permission_store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.STANDARD,
            workspace_root=self.workspace,
        )

    def tearDown(self) -> None:
        self.context.close()
        self.temporary.cleanup()

    def _grant(self, root: Path):
        return self.context.local_permission_store.create_grant(
            project_id=self.project_id,
            root_path=root,
            capabilities=[FileCapability.READ],
            reason="Path-aware selection test grant",
        )

    def test_the_grant_covering_the_target_wins_over_the_newest_grant(self):
        alpha = self.workspace / "alpha"
        beta = self.workspace / "beta"
        alpha.mkdir()
        beta.mkdir()
        alpha_grant = self._grant(alpha)
        beta_grant = self._grant(beta)  # created later: previously won every call

        selected = self.context._agent_grant_for(
            self.project_id, "read", str(alpha / "notes.md")
        )
        self.assertEqual(selected, alpha_grant.grant_id)

        selected = self.context._agent_grant_for(
            self.project_id, "read", str(beta / "notes.md")
        )
        self.assertEqual(selected, beta_grant.grant_id)

    def test_a_target_outside_every_grant_is_denied(self):
        alpha = self.workspace / "alpha"
        alpha.mkdir()
        self._grant(alpha)
        outside = self.root / "elsewhere"
        outside.mkdir()
        self.assertIsNone(
            self.context._agent_grant_for(self.project_id, "read", str(outside / "x.md"))
        )


class CommandApprovalGateTests(unittest.TestCase):
    """git worktree add must require an approved RUN_COMMAND approval."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.project_id = "approval-gate-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="git"))
        self.store = LocalPermissionStore(self.database)
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=self.root,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.root / "tool-state")
        self.repo = self._init_repo("approval-repo")
        self.grant = self.store.create_grant(
            project_id=self.project_id,
            root_path=self.repo,
            capabilities=[FileCapability.PROPOSE_COMMAND],
            reason="Bounded git grant",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _init_repo(self, name: str) -> Path:
        repo = self.root / name
        repo.mkdir(parents=True, exist_ok=True)
        (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git", "-C", str(repo), "-c", "user.email=amca@test",
                "-c", "user.name=AMCA Test", "commit", "-am", "initial",
            ],
            check=True,
            capture_output=True,
        )
        return repo

    def _request_argv(
        self,
        argv: list[str],
        *,
        action: PermissionAction = PermissionAction.RUN_COMMAND,
        decision: str = "approve",
        digest_of: list[str] | None = None,
    ) -> str:
        recorded = argv if digest_of is None else digest_of
        scope = {
            "grant_id": self.grant.grant_id,
            "cwd": ".",
            "argv": argv,
            "argv_sha256": hashlib.sha256(
                json.dumps(recorded, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        with PermissionStore(self.database) as permissions:
            request = permissions.request(
                project_id=self.project_id,
                action=action,
                scope=scope,
                reason="Approved git write",
                risk="high",
            )
            if decision == "approve":
                permissions.decide(request.id, approve=True, decided_by="test_user")
            elif decision == "deny":
                permissions.decide(request.id, approve=False, decided_by="test_user")
            return request.id

    def _worktree_argv(self) -> list[str]:
        return ["git", "worktree", "add", str(self.repo / "wt"), "feature/x"]

    def test_pending_approval_is_refused(self):
        approval_id = self._request_argv(self._worktree_argv(), decision="pending")
        with self.assertRaisesRegex(PermissionError, "approved state"):
            self.executor.git_worktree_add(
                project_id=self.project_id,
                grant_id=self.grant.grant_id,
                approval_id=approval_id,
            )

    def test_denied_approval_is_refused(self):
        approval_id = self._request_argv(self._worktree_argv(), decision="deny")
        with self.assertRaisesRegex(PermissionError, "approved state"):
            self.executor.git_worktree_add(
                project_id=self.project_id,
                grant_id=self.grant.grant_id,
                approval_id=approval_id,
            )

    def test_argv_that_does_not_match_its_recorded_digest_is_refused(self):
        approval_id = self._request_argv(
            self._worktree_argv(), digest_of=["git", "status"]
        )
        with self.assertRaisesRegex(PermissionError, "recorded digest"):
            self.executor.git_worktree_add(
                project_id=self.project_id,
                grant_id=self.grant.grant_id,
                approval_id=approval_id,
            )

    def test_a_non_run_command_approval_is_refused(self):
        approval_id = self._request_argv(
            self._worktree_argv(), action=PermissionAction.LOCAL_FILE_MODIFY
        )
        with self.assertRaisesRegex(PermissionError, "RUN_COMMAND"):
            self.executor.git_worktree_add(
                project_id=self.project_id,
                grant_id=self.grant.grant_id,
                approval_id=approval_id,
            )


class LegacyAdoptionSafetyTests(unittest.TestCase):
    """Adoption must never raise a chat above the level it already had."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed(self, rows: list[tuple[str, str]]) -> dict[str, str]:
        """Create real chats and legacy rows for exactly those chats."""
        refs: dict[str, str] = {}
        seed = LocalPermissionStore(self.database)
        try:
            for title, level in rows:
                ref = conversation_ref(self.database, title)
                refs[title] = ref
                seed._connection.execute(
                    """INSERT INTO local_permission_profiles
                       (project_id, profile, workspace_root, updated_at, updated_by)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ref, level, str(self.root), "2026-01-01T00:00:00Z", "legacy"),
                )
            seed._connection.execute("DELETE FROM local_permission_policy")
        finally:
            seed.close()
        return refs

    def test_the_most_restrictive_recorded_level_wins(self) -> None:
        """A newer Full Computer row must not raise a Standard chat."""
        refs = self._seed([("Alpha", "standard"), ("Beta", "full_computer")])
        with LocalPermissionStore(self.database) as store:
            self.assertEqual(store.profile(refs["Alpha"])["profile"], "standard")
            self.assertEqual(store.profile(refs["Beta"])["profile"], "standard")

    def test_a_chat_with_no_legacy_row_forces_restricted_adoption(self) -> None:
        """A chat that never had a row was Restricted; adoption must not raise it."""
        refs = self._seed([("Alpha", "full_computer")])
        unrecorded = conversation_ref(self.database, "Gamma")
        with LocalPermissionStore(self.database) as store:
            self.assertEqual(store.profile(refs["Alpha"])["profile"], "restricted")
            self.assertEqual(store.profile(unrecorded)["profile"], "restricted")

    def test_adoption_records_an_audit_event_naming_the_source(self) -> None:
        refs = self._seed([("Alpha", "standard")])
        with LocalPermissionStore(self.database) as store:
            rows = store._connection.execute(
                "SELECT * FROM local_tool_events WHERE action=?",
                ("profile_legacy_adoption",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        detail = json.loads(rows[0]["detail_json"])
        self.assertEqual(detail["adopted_profile"], "standard")
        self.assertEqual(detail["most_restrictive_recorded_level"], "standard")
        self.assertEqual(detail["source_project_id"], refs["Alpha"])
        self.assertEqual(detail["unrecorded_projects"], 0)
        self.assertEqual(
            detail["rule"],
            "most_restrictive_recorded_level_within_recorded_projects",
        )

    def test_an_unknown_project_cannot_raise_the_global_level(self) -> None:
        with LocalPermissionStore(self.database) as store:
            with self.assertRaisesRegex(ValueError, "project does not exist"):
                store.set_profile(
                    project_id="not-a-project",
                    profile=PermissionProfile.FULL_COMPUTER,
                )
            self.assertEqual(store.profile("conv-any")["profile"], "restricted")


class GrantWithoutWorkspaceFolderTests(unittest.TestCase):
    """The level is global and folder-free; grants stay folder-scoped."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.folderless = conversation_ref(self.database, "Folderless chat")
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.folderless, goal="g"))
        self.store = LocalPermissionStore(self.database)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_restricted_and_standard_refuse_a_grant_without_a_folder(self) -> None:
        self.store.set_profile(
            project_id=self.folderless, profile=PermissionProfile.STANDARD
        )
        with self.assertRaisesRegex(PermissionError, "workspace folder is not configured"):
            self.store.create_grant(
                project_id=self.folderless,
                root_path=self.outside,
                capabilities=[FileCapability.READ],
                reason="No folder configured",
            )

    def test_extended_refuses_a_grant_without_a_folder(self) -> None:
        """Extended is bounded by the project folder like the levels below it.

        This test previously asserted the opposite - that Extended could grant
        any folder with no workspace at all.  The product decision is that only
        Full Access is PC-wide, so Extended adds capabilities (move, recoverable
        trash, command inspection) and no wider reach.  A standalone chat with
        no Project therefore has no folder to be contained by, and the refusal
        is the correct answer rather than a regression.
        """
        self.store.set_profile(
            project_id=self.folderless, profile=PermissionProfile.EXTENDED
        )
        with self.assertRaisesRegex(PermissionError, "workspace folder is not configured"):
            self.store.create_grant(
                project_id=self.folderless,
                root_path=self.outside,
                capabilities=[FileCapability.READ],
                reason="Folder grant without a project workspace",
            )

    def test_extended_keeps_its_extra_capabilities_inside_the_folder(self) -> None:
        """Extended still gains move/trash/command inspection - inside the folder."""
        inside = self.root / "inside"
        inside.mkdir()
        # Extended is folder-bound, and since v1.2.0 the folder belongs to a
        # Project, so the identity that carries it is a Project id.
        project = studio_project_ref(self.database, "extended-folder", inside)
        self.store.set_profile(
            project_id=project,
            profile=PermissionProfile.EXTENDED,
            workspace_root=str(inside),
        )
        grant = self.store.create_grant(
            project_id=project,
            root_path=str(inside),
            capabilities=[FileCapability.MOVE, FileCapability.TRASH],
            reason="Extended capabilities within the project folder",
        )
        self.assertTrue(grant.active)
        self.assertIn(FileCapability.MOVE.value, grant.capabilities)
        self.assertIn(FileCapability.TRASH.value, grant.capabilities)
        with self.assertRaisesRegex(PermissionError, "inside the project workspace"):
            self.store.create_grant(
                project_id=project,
                root_path=self.outside,
                capabilities=[FileCapability.READ],
                reason="Outside the project folder",
            )


class ApprovalIsSingleUseTests(unittest.TestCase):
    """A one-time approval must not be replayable while it still reads APPROVED."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite3"
        # Approvals are requested against a canonical project, so this test uses
        # a real projects row rather than a conversation-scoped id.
        self.project_id = "approval-single-use-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="g"))
        self.store = LocalPermissionStore(self.database)
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=self.root,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.root / "tool-state")

    def tearDown(self) -> None:
        self.store.close()
        # The approved argv runs in a detached subprocess that may still hold a
        # handle on the temp directory for a moment after the assertion.
        try:
            self.temporary.cleanup()
        except (PermissionError, OSError):
            pass

    def _argv_approval(self, argv: list[str]) -> str:
        scope = {
            "grant_id": "grant.test",
            "cwd": ".",
            "argv": argv,
            "argv_sha256": hashlib.sha256(
                json.dumps(argv, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        with PermissionStore(self.database) as permissions:
            request = permissions.request(
                project_id=self.project_id,
                action=PermissionAction.RUN_COMMAND,
                scope=scope,
                reason="Single-use proof",
                risk="high",
            )
            permissions.decide(request.id, approve=True, decided_by="test_user")
            return request.id

    def test_a_consumed_argv_approval_cannot_run_twice(self) -> None:
        approval_id = self._argv_approval(["whoami"])
        grant = self.store.create_grant(
            project_id=self.project_id,
            root_path=self.root,
            capabilities=[FileCapability.PROPOSE_COMMAND],
            reason="Single-use proof grant",
        )
        first = self.executor.execute_approved_argv(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
        )
        # The run is streamed, so the proof is the consumed record plus the
        # refusal of the replay.
        self.assertTrue(first.get("run_id"))
        with PermissionStore(self.database) as permissions:
            self.assertEqual(
                str(permissions.get(approval_id).status.value), "consumed"
            )
        with self.assertRaises(PermissionError):
            self.executor.execute_approved_argv(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )

    def test_a_consumed_git_approval_cannot_run_twice(self) -> None:
        import subprocess as sp

        repo = self.root / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# r\n", encoding="utf-8")
        sp.run(["git", "init", "-q"], cwd=repo, check=True)
        sp.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
        sp.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        sp.run(
            ["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=t",
             "commit", "-am", "initial"],
            check=True, capture_output=True,
        )
        grant = self.store.create_grant(
            project_id=self.project_id,
            root_path=repo,
            capabilities=[FileCapability.PROPOSE_COMMAND],
            reason="Git single-use proof grant",
        )
        approval_id = self._argv_approval(["git", "branch", "single-use-proof"])
        first = self.executor.git_write_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
        )
        self.assertEqual(first["exit_code"], 0)
        with self.assertRaises(PermissionError):
            self.executor.git_write_command(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )
