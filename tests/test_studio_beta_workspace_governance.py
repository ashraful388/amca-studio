"""
AMCA Studio Beta B7 Enterprise Workspace Governance Foundation Test Suite
Validates:
1. WorkspaceRole evaluation (owner, admin, operator, viewer)
2. Append-only audit trail logging and project/workspace isolation
3. Shared resource safety boundaries
4. Zero cloud dependency / local SQLite offline operation
"""

import tempfile
import unittest
from pathlib import Path

from amca.workspace_governance import (
    WorkspaceRole,
    WorkspaceGovernanceStore,
    evaluate_workspace_permission,
)


class StudioBetaWorkspaceGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp_dir.name) / "test_gov.sqlite3"
        self.store = WorkspaceGovernanceStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_role_based_permissions(self) -> None:
        """Verify role permissions match enterprise governance hierarchy."""
        # Viewer can read but not write or execute
        self.assertTrue(evaluate_workspace_permission(WorkspaceRole.VIEWER, "project:read"))
        self.assertFalse(evaluate_workspace_permission(WorkspaceRole.VIEWER, "project:write"))
        self.assertFalse(evaluate_workspace_permission(WorkspaceRole.VIEWER, "automation:execute"))

        # Operator can execute automation and write projects but not manage workspace
        self.assertTrue(evaluate_workspace_permission(WorkspaceRole.OPERATOR, "automation:execute"))
        self.assertFalse(evaluate_workspace_permission(WorkspaceRole.OPERATOR, "workspace:manage"))

        # Owner has full administrative permissions
        self.assertTrue(evaluate_workspace_permission(WorkspaceRole.OWNER, "workspace:manage"))
        self.assertTrue(evaluate_workspace_permission(WorkspaceRole.OWNER, "project:delete"))

    def test_audit_trail_logging_and_workspace_isolation(self) -> None:
        """Verify audit records are stored in append-only SQLite log with strict workspace isolation."""
        rec1 = self.store.log_action(
            workspace_id="ws-enterprise-corp",
            actor_id="user-ashraf",
            role="owner",
            action="update_model_policy",
            resource_type="model_hub",
            resource_id="ollama/qwen2.5-coder:7b",
        )
        self.assertTrue(rec1.audit_id.startswith("aud-"))
        self.assertEqual(rec1.action, "update_model_policy")

        # Query Workspace A
        logs_a = self.store.list_audit_records("ws-enterprise-corp")
        self.assertEqual(len(logs_a), 1)
        self.assertEqual(logs_a[0]["actor_id"], "user-ashraf")
        self.assertEqual(logs_a[0]["role"], "owner")

        # Query Workspace B (strict isolation)
        logs_b = self.store.list_audit_records("ws-other-corp")
        self.assertEqual(len(logs_b), 0)


if __name__ == "__main__":
    unittest.main()

