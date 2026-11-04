from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from amca.domain import ProjectState
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)
from amca.permissions import PermissionAction, PermissionStore
from amca.security import SensitiveDataError
from amca.storage import SQLiteStore


class LocalToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.database = self.root / "state.sqlite3"
        self.project_id = "local-tools-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="test"))
        self.store = LocalPermissionStore(self.database)
        self.executor = LocalToolExecutor(
            self.store, state_root=self.root / "local-tool-state"
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def configure(self, profile: PermissionProfile) -> None:
        self.store.set_profile(
            project_id=self.project_id,
            profile=profile,
            workspace_root=self.workspace,
        )

    def grant(self, *capabilities: FileCapability, root: Path | None = None):
        return self.store.create_grant(
            project_id=self.project_id,
            root_path=root or self.workspace,
            capabilities=capabilities,
            reason="Bounded test grant",
        )

    def approve(self, action: PermissionAction, scope: dict[str, object]) -> str:
        with PermissionStore(self.database) as permissions:
            request = permissions.request(
                project_id=self.project_id,
                action=action,
                scope=scope,
                reason="Approve this exact local operation",
                risk="high",
            )
            permissions.decide(request.id, approve=True, decided_by="test_user")
            return request.id

    def test_restricted_is_read_only_and_workspace_bound(self) -> None:
        self.configure(PermissionProfile.RESTRICTED)
        (self.workspace / "note.txt").write_text("hello", encoding="utf-8")
        grant = self.grant(FileCapability.LIST, FileCapability.READ)
        result = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action=FileCapability.READ,
            path="note.txt",
        )
        self.assertEqual(result["content"], "hello")
        with self.assertRaisesRegex(PermissionError, "exceeds"):
            self.grant(FileCapability.CREATE)
        with self.assertRaisesRegex(PermissionError, "inside"):
            self.grant(FileCapability.READ, root=self.outside)

    def test_standard_create_and_exact_approved_versioned_modify(self) -> None:
        self.configure(PermissionProfile.STANDARD)
        grant = self.grant(
            FileCapability.CREATE,
            FileCapability.WRITE,
            FileCapability.APPEND,
            FileCapability.READ,
        )
        created = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action="create",
            path="report.md",
            content="v1",
        )
        self.assertTrue(created["created"])

        with self.assertRaisesRegex(PermissionError, "exact user approval"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="write",
                path="report.md",
                content="v2",
            )
        scope = self.executor.request_scope(
            action=PermissionAction.LOCAL_FILE_MODIFY,
            grant_id=grant.grant_id,
            source="report.md",
        )
        approval_id = self.approve(PermissionAction.LOCAL_FILE_MODIFY, scope)
        modified = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action="write",
            path="report.md",
            content="v2",
            approval_id=approval_id,
        )
        self.assertEqual((self.workspace / "report.md").read_text(), "v2")
        backup = Path(str(modified["version_backup"]))
        self.assertEqual(backup.read_text(), "v1")

        wrong_scope = self.executor.request_scope(
            action=PermissionAction.LOCAL_FILE_MODIFY,
            grant_id=grant.grant_id,
            source="report.md",
            overwrite=True,
        )
        wrong_id = self.approve(PermissionAction.LOCAL_FILE_MODIFY, wrong_scope)
        with self.assertRaisesRegex(PermissionError, "exactly match"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="write",
                path="report.md",
                content="v3",
                approval_id=wrong_id,
            )

    def test_symlink_cannot_escape_grant(self) -> None:
        self.configure(PermissionProfile.STANDARD)
        secret = self.outside / "secret.txt"
        secret.write_text("outside", encoding="utf-8")
        link = self.workspace / "escape.txt"
        try:
            link.symlink_to(secret)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        grant = self.grant(FileCapability.READ)
        with self.assertRaisesRegex(PermissionError, "escapes"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="read",
                path="escape.txt",
            )

    def test_extended_move_and_recoverable_trash_are_exactly_approved(self) -> None:
        self.configure(PermissionProfile.EXTENDED)
        (self.workspace / "source.txt").write_text("payload", encoding="utf-8")
        grant = self.grant(FileCapability.MOVE, FileCapability.TRASH, FileCapability.RESTORE)
        move_scope = self.executor.request_scope(
            action=PermissionAction.LOCAL_FILE_MOVE,
            grant_id=grant.grant_id,
            source="source.txt",
            destination="moved.txt",
        )
        move_id = self.approve(PermissionAction.LOCAL_FILE_MOVE, move_scope)
        self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action="move",
            path="source.txt",
            destination="moved.txt",
            approval_id=move_id,
        )
        trash_scope = self.executor.request_scope(
            action=PermissionAction.LOCAL_FILE_TRASH,
            grant_id=grant.grant_id,
            source="moved.txt",
        )
        trash_id = self.approve(PermissionAction.LOCAL_FILE_TRASH, trash_scope)
        result = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action="trash",
            path="moved.txt",
            approval_id=trash_id,
        )
        self.assertFalse((self.workspace / "moved.txt").exists())
        self.assertTrue(result["recoverable"])
        # Restore moves an EXISTING object back into the tree, so it carries
        # the same exact-scope approval a move does.  It previously executed
        # with no approval at all, which made trash + restore a single-ask
        # round trip.
        with self.assertRaisesRegex(PermissionError, "requires an exact user approval"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="restore",
                trash_id=str(result["trash_id"]),
            )
        restore_request = self.executor.propose_filesystem_operation(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action=PermissionAction.LOCAL_FILE_MOVE,
            capability="restore",
            source=str(result["trash_id"]),
            trash_id=str(result["trash_id"]),
            reason="Restore the trashed file",
        )
        restore_id = self.approve(
            PermissionAction.LOCAL_FILE_MOVE, restore_request["approval"]["scope"]
        )
        restored = self.executor.execute(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action="restore",
            trash_id=str(result["trash_id"]),
            approval_id=restore_id,
        )
        self.assertTrue(restored["restored"])
        self.assertEqual((self.workspace / "moved.txt").read_text(), "payload")

    def test_revoked_and_expired_grants_fail_closed(self) -> None:
        self.configure(PermissionProfile.STANDARD)
        grant = self.grant(FileCapability.LIST)
        self.store.revoke_grant(grant.grant_id)
        with self.assertRaisesRegex(PermissionError, "revoked or expired"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="list",
            )

        expiring = self.grant(FileCapability.LIST)
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE local_path_grants SET expires_at=? WHERE grant_id=?",
                (expired, expiring.grant_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(PermissionError, "revoked or expired"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=expiring.grant_id,
                action="list",
            )

    def test_profile_downgrade_invalidates_outside_workspace_grant(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.READ, root=self.outside)
        self.configure(PermissionProfile.STANDARD)
        with self.assertRaisesRegex(PermissionError, "outside"):
            self.executor.execute(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action="read",
                path="missing.txt",
            )

    def test_command_and_application_controls_are_proposals_not_execution(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(
            FileCapability.PROPOSE_COMMAND,
            FileCapability.PROPOSE_APPLICATION,
        )
        command = self.executor.propose_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            command="python --version",
            cwd=".",
            reason="Inspect the configured runtime",
        )
        application = self.executor.propose_application(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            application="notepad",
            task="Open a reviewed text artifact",
            reason="User-visible application task",
        )
        self.assertFalse(command["execution_supported"])
        self.assertTrue(application["execution_supported"])
        self.assertEqual(command["approval"]["action"], "run_command")
        self.assertEqual(application["approval"]["action"], "control_application")

    def test_unknown_application_is_refused_before_proposal(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.PROPOSE_APPLICATION)
        with self.assertRaisesRegex(PermissionError, "governed application registry"):
            self.executor.propose_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                application="cmd.exe",
                task="Open a shell",
                reason="Must be refused",
            )

    def test_governed_application_launch_inspect_terminate(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.PROPOSE_APPLICATION)
        proposed = self.executor.propose_application(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            application="Notepad",
            task="Open Notepad for the governed lifecycle proof",
            reason="Physical application lifecycle test",
        )
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(self.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")

        # inspect before launch must fail closed
        with self.assertRaisesRegex(ValueError, "launch first"):
            self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="inspect",
            )

        launched = self.executor.execute_application(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            application="notepad",
            operation="launch",
        )
        self.assertEqual(launched["operation"], "launch")
        self.assertEqual(launched["status"], "running")
        self.assertTrue(str(launched["executable"]).endswith("notepad.exe"))

        try:
            inspected = self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="inspect",
            )
            self.assertTrue(inspected["alive"])
            self.assertEqual(inspected["pid"], launched["pid"])
        finally:
            terminated = self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="terminate",
            )
        self.assertEqual(terminated["operation"], "terminate")
        self.assertEqual(terminated["status"], "terminated")
        # after termination, inspect must report the session gone
        with self.assertRaisesRegex(ValueError, "launch first"):
            self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="inspect",
            )

    def test_governed_browser_loopback_url_policy(self) -> None:
        from amca.local_tools import _normalize_loopback_browser_url

        for bad in (
            "https://example.com/page",
            "ftp://127.0.0.1/x",
            "file:///C:/Windows/win.ini",
            "http://user:pw@127.0.0.1/x",
            "http://10.1.2.3/",
            "javascript:alert(1)",
            "",
        ):
            with self.assertRaises(PermissionError):
                _normalize_loopback_browser_url(bad)
        self.assertEqual(
            _normalize_loopback_browser_url("http://127.0.0.1:8901/test.html"),
            "http://127.0.0.1:8901/test.html",
        )
        self.assertEqual(
            _normalize_loopback_browser_url("https://localhost/page"),
            "https://localhost/page",
        )

    def test_governed_browser_unknown_browser_refused(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.PROPOSE_APPLICATION)
        with self.assertRaisesRegex(PermissionError, "governed browser registry"):
            self.executor.propose_browser_navigation(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                browser="iexplore",
                url="http://127.0.0.1:9/x",
                task="Navigate",
                reason="Must be refused",
            )

    def test_governed_browser_unapproved_execute_refused(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.PROPOSE_APPLICATION)
        proposed = self.executor.propose_browser_navigation(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            browser="edge",
            url="http://127.0.0.1:9/x",
            task="Navigate to loopback page",
            reason="Negative-path browser test",
        )
        approval_id = str(proposed["approval"]["id"])
        with self.assertRaisesRegex(PermissionError, "not in the approved state"):
            self.executor.execute_browser(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                operation="open",
            )

    def test_governed_browser_requires_active_grant(self) -> None:
        self.configure(PermissionProfile.RESTRICTED)
        grant = self.grant(FileCapability.LIST)
        with self.assertRaises(PermissionError):
            self.executor.propose_browser_navigation(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                browser="edge",
                url="http://127.0.0.1:9/x",
                task="Navigate",
                reason="Grant lacks propose_application",
            )

    def test_governed_application_rejects_wrong_application_and_unapproved(self) -> None:
        self.configure(PermissionProfile.FULL_COMPUTER)
        grant = self.grant(FileCapability.PROPOSE_APPLICATION)
        proposed = self.executor.propose_application(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            application="notepad",
            task="Bound to notepad only",
            reason="Negative-path application test",
        )
        approval_id = str(proposed["approval"]["id"])
        # unapproved: must refuse before any process starts
        with self.assertRaisesRegex(PermissionError, "not in the approved state"):
            self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="notepad",
                operation="launch",
            )
        with PermissionStore(self.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")
        # approved, but for notepad: cannot retarget another application
        # (calc is outside the governed registry entirely)
        with self.assertRaisesRegex(PermissionError, "governed application registry"):
            self.executor.execute_application(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
                application="calc",
                operation="launch",
            )

    def test_inspection_command_requires_exact_approval_and_blocks_shells(self) -> None:
        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        with self.assertRaisesRegex(PermissionError, "inspection-only"):
            self.executor.request_inspection_command(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                argv=["python", "-c", "print('unsafe')"],
                reason="This must be rejected",
            )
        requested = self.executor.request_inspection_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            argv=["whoami"],
            reason="Inspect the current operating-system identity",
        )
        approval_id = str(requested["approval"]["id"])
        with PermissionStore(self.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")
        result = self.executor.execute_inspection_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            argv=["whoami"],
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["stdout"].strip())
        self.assertEqual(result["execution_scope"], "inspection_only")

    def test_approved_argv_command_executes_and_streams_to_snapshot(self) -> None:
        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        argv = ["git", "--version"]
        proposed = self.executor.propose_argv_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            argv=argv,
            cwd=".",
            reason="Check the repository toolchain version",
        )
        self.assertTrue(proposed["execution_supported"])
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(self.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")

        started = self.executor.execute_approved_argv(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            timeout_seconds=30,
        )
        run_id = str(started["run_id"])
        deadline = time.monotonic() + 20
        snapshot = dict(started)
        while time.monotonic() < deadline:
            snapshot = self.executor.command_run_snapshot(run_id)
            if snapshot["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["exit_code"], 0)
        self.assertIn("git version", snapshot["output"])
        # Give the detached process handle a moment to release the cwd on
        # Windows so the temporary directory can be removed.
        time.sleep(0.3)

    def test_cancel_run_returns_flat_receipt_and_snapshot_shows_cancelled(self) -> None:
        # Round 23 regression: the cancel route returns a FLAT receipt
        # {run_id, status, process_killed} - not the {run: snapshot} envelope
        # the snapshot route uses. The Terminal tab reads res.status directly.
        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        proposed = self.executor.propose_argv_command(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            argv=["ping", "-n", "60", "127.0.0.1"],
            cwd=".",
            reason="Proving cancel against a real long-lived process",
        )
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(self.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")

        started = self.executor.execute_approved_argv(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            approval_id=approval_id,
            timeout_seconds=60,
        )
        run_id = str(started["run_id"])
        try:
            receipt = self.executor.cancel_run(run_id)
            self.assertEqual(receipt["run_id"], run_id)
            self.assertEqual(receipt["status"], "cancelled")
            self.assertIn("process_killed", receipt)
            self.assertNotIn("run", receipt)
            snapshot = self.executor.command_run_snapshot(run_id)
            self.assertEqual(snapshot["status"], "cancelled")
            # A finished-then-cancelled run cannot be cancelled again.
            with self.assertRaisesRegex(ValueError, "cancellable"):
                self.executor.cancel_run(run_id)
        finally:
            time.sleep(0.3)

    def test_denied_executable_is_refused_even_with_approval(self) -> None:
        import hashlib
        import json

        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        argv = ["reg", "query", "HKLM\\SOFTWARE"]
        scope = {
            "grant_id": grant.grant_id,
            "cwd": ".",
            "argv": argv,
            "argv_sha256": hashlib.sha256(
                json.dumps(argv, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        approval_id = self.approve(PermissionAction.RUN_COMMAND, scope)
        # reg has no allowlist policy, so the policy gate refuses first; the
        # frozen deny-list remains as the hard floor behind it.
        with self.assertRaisesRegex(PermissionError, "allowlist policy"):
            self.executor.execute_approved_argv(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )

    def test_non_allowlisted_argv_refused_even_with_approval(self) -> None:
        import hashlib
        import json

        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        argv = ["notepad", "notes.txt"]
        scope = {
            "grant_id": grant.grant_id,
            "cwd": ".",
            "argv": argv,
            "argv_sha256": hashlib.sha256(
                json.dumps(argv, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "executor": "bounded_subprocess",
        }
        approval_id = self.approve(PermissionAction.RUN_COMMAND, scope)
        with self.assertRaisesRegex(PermissionError, "allowlist policy"):
            self.executor.execute_approved_argv(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )

    def test_shell_interpreters_never_reach_proposal(self) -> None:
        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        with self.assertRaisesRegex(PermissionError, "shell interpreters"):
            self.executor.propose_argv_command(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                argv=["cmd.exe", "/c", "echo hi"],
                cwd=".",
                reason="This must be rejected",
            )

    def test_digest_mismatch_refuses_execution(self) -> None:
        import hashlib
        import json

        self.configure(PermissionProfile.EXTENDED)
        grant = self.grant(FileCapability.PROPOSE_COMMAND)
        argv = ["git", "--version"]
        scope = {
            "grant_id": grant.grant_id,
            "cwd": ".",
            "argv": argv,
            "argv_sha256": hashlib.sha256(b"tampered").hexdigest(),
            "executor": "bounded_subprocess",
        }
        approval_id = self.approve(PermissionAction.RUN_COMMAND, scope)
        with self.assertRaisesRegex(PermissionError, "recorded digest"):
            self.executor.execute_approved_argv(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                approval_id=approval_id,
            )

    def test_secret_shaped_audit_detail_is_rejected(self) -> None:
        self.configure(PermissionProfile.STANDARD)
        with self.assertRaises(SensitiveDataError):
            self.store.record_event(
                project_id=self.project_id,
                action="unsafe",
                outcome="blocked",
                actor="test",
                detail={"api_key": "must-not-be-stored"},
            )
        self.assertNotIn(b"must-not-be-stored", self.database.read_bytes())


if __name__ == "__main__":
    unittest.main()
