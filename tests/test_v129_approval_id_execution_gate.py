"""A governed mutation must never execute without a validated approval.

WHY THIS EXISTS

The 32F work-in-progress adds an `approval_id` path to the governed tool
loop: when the model re-issues an operation with an approval id, the
adapter calls the executor directly instead of proposing a new approval.
The path is only as safe as the executor's own approval check.

The executor's COPY branch validated the approval only when the
DESTINATION ALREADY EXISTED.  A copy to a NEW destination therefore never
checked the approval at all, so a fabricated (or merely proposed, still
pending) approval id executed the copy and the file appeared on disk.  That
is a human-approval bypass, and the anti-fabrication gate would then
publish "I copied source.txt to copy.txt" as verified work.

The executor's behaviour must not depend on whether the destination
happens to exist.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.service import ServiceContext

PROJECT_ID = "studio-approval-gate"
SOURCE_NAME = "source.txt"
DESTINATION_NAME = "copy-target.txt"


class ApprovalExecutionGate(unittest.TestCase):
    """A copy must not execute without a validated, approved id."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        (root / "benchmarks").mkdir()
        (root / "benchmarks" / "tasks.json").write_text("[]", encoding="utf-8")
        self.context = ServiceContext(
            root=root, database=root / "amca.db",
            benchmarks=root / "benchmarks" / "tasks.json",
        )
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.context.studio_project_store.save({
            "project_id": PROJECT_ID, "name": "approval gate",
            "description": "", "instructions": "", "template": "general",
            "root_path": str(self.workspace),
        })
        self.context.set_local_permission_profile({
            "project_id": PROJECT_ID, "profile": "full_computer",
            "workspace_root": str(self.workspace),
        })
        grant = self.context.create_local_grant({
            "project_id": PROJECT_ID, "root_path": str(self.workspace),
            "capabilities": ["list", "read", "stat", "search", "create",
                              "write", "append", "copy", "move", "trash",
                              "propose_command"],
            "reason": "approval gate proof",
        })
        self.grant_id = (
            grant["grant_id"] if isinstance(grant, dict) else grant.grant_id
        )
        (self.workspace / SOURCE_NAME).write_text("hello", encoding="utf-8")

    def tearDown(self) -> None:
        try:
            self._temporary.cleanup()
        except Exception:
            pass

    def _copy(self, approval_id: str) -> dict:
        return self.context.execute_local_tool({
            "project_id": PROJECT_ID, "grant_id": self.grant_id,
            "action": "copy", "path": SOURCE_NAME,
            "destination": DESTINATION_NAME, "overwrite": False,
            "approval_id": approval_id,
        })

    def test_a_fabricated_approval_id_cannot_execute_a_copy(self) -> None:
        with self.assertRaises(Exception) as caught:
            self._copy("approval.does-not-exist")
        self.assertNotIsInstance(caught.exception, AssertionError)
        self.assertFalse(
            (self.workspace / DESTINATION_NAME).exists(),
            "a copy must not create a file without a validated approval",
        )

    def test_a_pending_approval_cannot_execute_a_copy(self) -> None:
        """An approval that was PROPOSED but never approved is not a grant."""
        approval_id = self._propose_copy()
        with self.assertRaises(Exception):
            self._copy(str(approval_id))
        self.assertFalse(
            (self.workspace / DESTINATION_NAME).exists(),
            "a pending (unapproved) approval must not execute a copy",
        )

    def test_a_validly_approved_copy_still_executes(self) -> None:
        """The gate must refuse invalid approvals WITHOUT disabling valid ones.

        The approval scope binds the request's own overwrite flag, so the
        executor's scope must be built the same way.  Building it with a
        hard-coded overwrite would refuse every approved copy.
        """
        approval_id = self._propose_copy()
        from amca.permissions import PermissionStore

        with PermissionStore(self.context.database) as approvals:
            approvals.decide(
                str(approval_id), approve=True, decided_by="desktop_user",
                reason="approved copy proof",
            )
        self._copy(str(approval_id))
        self.assertTrue(
            (self.workspace / DESTINATION_NAME).exists(),
            "a validly approved copy must still execute",
        )

    def _propose_copy(self) -> str:
        proposal = self.context.propose_local_filesystem_approval({
            "project_id": PROJECT_ID, "grant_id": self.grant_id,
            "capability": "copy", "approval_action": "local_file_modify",
            "source": SOURCE_NAME,
            "destination": DESTINATION_NAME, "overwrite": False,
            "reason": "copy approval proof",
        })
        approval_id = (
            proposal.get("approval", {}).get("id")
            or proposal.get("approval_id") or proposal.get("id")
        )
        self.assertTrue(approval_id, "the proposal must carry an approval id")
        return str(approval_id)


if __name__ == "__main__":
    unittest.main()
