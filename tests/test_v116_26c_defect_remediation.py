"""26C regression: DEFECT-13 evidence-bound anti-fabrication + DEFECT-14 grant semantics.

Work order ZCODE-AMCA-V116-INTEGRITY-AND-GRANT-REMEDIATION-26C (base 8f1d2855).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.anti_fabrication import reconcile_anti_fabrication
from amca.chat_history import ConversationStore
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
)


class TestDefect13EvidenceBoundAntiFabrication(unittest.TestCase):
    """Model wording must never be sufficient evidence (26C P0)."""

    def test_26b_fabricated_write_success_is_blocked(self):
        text = "The write succeeded as the CREATE grant is active for the specified path."
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=[])
        self.assertTrue(altered)
        self.assertNotIn("succeeded", reconciled.lower())
        self.assertIn("not executed", reconciled.lower())

    def test_truthful_denial_is_preserved(self):
        text = (
            "The write operation was blocked because the project's permission "
            "profile does not grant access to the specified path (qual-qwen17b.txt), "
            "even though the user has a FULL_COMPUTER profile."
        )
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=[])
        self.assertFalse(altered)
        self.assertIn("blocked", reconciled)

    def test_narrated_execution_without_event_is_blocked(self):
        text = (
            "Executing local write tool with arguments: path=v116-explicit.txt, "
            "content=AMCA_OK. The operation completed successfully."
        )
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=[])
        self.assertTrue(altered)
        self.assertNotIn("completed successfully", reconciled)

    def test_cross_path_success_claim_is_not_authorized(self):
        text = "I created the file report.docx with the summary."
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_tools=[{
                "name": "filesystem.write", "action": "write",
                "state": "success", "path": "D:/scope/other.txt",
            }],
        )
        self.assertTrue(altered)
        self.assertIn("not executed", reconciled.lower())

    def test_matching_path_success_claim_is_preserved(self):
        text = "I created the file other.txt with the summary."
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_tools=[{
                "name": "filesystem.write", "action": "write",
                "state": "success", "path": "D:/scope/other.txt",
            }],
        )
        self.assertFalse(altered)
        self.assertIn("other.txt", reconciled)

    def test_generic_success_without_any_event_is_blocked(self):
        text = "Done. Everything went through without errors."
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_ops=[],
        )
        self.assertTrue(altered)
        self.assertIn("not executed", reconciled.lower())

    def test_successful_run_supports_target_less_success_claim(self):
        text = "The write succeeded as requested."
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_ops=["create:D:/scope/qual-ok.txt"],
        )
        self.assertFalse(altered)

    def test_legit_chat_answer_is_untouched(self):
        text = "Python lists are mutable. Use append() to add items."
        reconciled, altered = reconcile_anti_fabrication(text, executed_tools=[])
        self.assertFalse(altered)
        self.assertEqual(reconciled, text)

    def test_read_claim_requires_matching_read_event(self):
        text = "Here is the contents of notes.txt: invented text"
        reconciled, altered = reconcile_anti_fabrication(
            text,
            executed_tools=[{
                "name": "filesystem.read", "action": "read",
                "state": "success", "path": "D:/scope/other.txt", "content": "real",
            }],
        )
        self.assertTrue(altered)
        self.assertIn("not executed", reconciled.lower())


class TestDefect14GrantSemantics(unittest.TestCase):
    """Path-grant semantics aligned with execution (26C P0)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="26c-regress-"))
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.granted = self.tmp / "granted"
        self.granted.mkdir()
        self.store = LocalPermissionStore(self.tmp / "perm.sqlite3")
        self.pid = "desktop-26cregress"
        # v1.1.8: the PC access level is global, so a level change must name a
        # real chat.  "desktop-<id>" resolves to the conversation row.
        ConversationStore(self.tmp / "perm.sqlite3").create(
            title="26C grant semantics", conversation_id="26cregress"
        )
        # v1.2.0: Full Computer is PC-wide and carries no folder, and a
        # chat-prefixed identity may not hold one at all.
        self.store.set_profile(
            project_id=self.pid,
            profile=PermissionProfile.FULL_COMPUTER,
        )
        self.executor = LocalToolExecutor(self.store, state_root=self.tmp / "state")

    def _create_grant(self, capabilities, reason):
        return self.store.create_grant(
            project_id=self.pid,
            root_path=str(self.granted),
            capabilities=capabilities,
            reason=reason,
            ttl_seconds=3600,
        )

    def test_create_grant_authorizes_new_child_file_relative(self):
        grant = self._create_grant(
            [FileCapability.LIST, FileCapability.READ, FileCapability.CREATE],
            "create grant",
        )
        res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="new-child.txt",
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["grant_id"], grant.grant_id)
        self.assertEqual(res["capability"], "create")
        out = self.executor.execute(
            project_id=self.pid, grant_id=res["grant_id"],
            action="create", path=res["relative_path"], content="OK",
        )
        self.assertTrue(out["created"])
        self.assertEqual((self.granted / "new-child.txt").read_text(), "OK")

    def test_absolute_inside_scope_resolves_like_relative(self):
        self._create_grant([FileCapability.CREATE], "create grant")
        absolute = self.granted / "abs-child.txt"
        res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path=str(absolute),
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["relative_path"], "abs-child.txt")
        self.assertEqual(res["capability"], "create")
        out = self.executor.execute(
            project_id=self.pid, grant_id=res["grant_id"],
            action="create", path=res["relative_path"], content="ABS",
        )
        self.assertEqual(absolute.read_text(), "ABS")

    def test_absolute_outside_scope_is_denied(self):
        self._create_grant([FileCapability.CREATE], "create grant")
        res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write",
            path=str(self.tmp / "outside.txt"),
        )
        self.assertFalse(res["matched"])

    def test_create_grant_does_not_authorize_existing_file_overwrite(self):
        self._create_grant([FileCapability.CREATE], "create grant")
        target = self.granted / "existing.txt"
        target.write_text("original")
        res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="existing.txt",
        )
        self.assertFalse(res["matched"])

    def test_write_grant_resolves_existing_file_modification(self):
        self._create_grant([FileCapability.WRITE], "write grant")
        target = self.granted / "doc.txt"
        target.write_text("v1")
        res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="doc.txt",
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["capability"], "write")
        self.assertTrue(res["target_exists"])

    def test_relative_and_absolute_normalize_to_same_decision(self):
        self._create_grant([FileCapability.CREATE], "create grant")
        relative_res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="same.txt",
        )
        absolute_res = self.executor.resolve_mutation_target(
            project_id=self.pid, action="write",
            path=str(self.granted / "same.txt"),
        )
        self.assertEqual(relative_res["matched"], absolute_res["matched"])
        self.assertEqual(relative_res["capability"], absolute_res["capability"])
        self.assertEqual(relative_res["relative_path"], absolute_res["relative_path"])

    def test_audit_reports_matched_grant_and_capability(self):
        grant = self._create_grant([FileCapability.CREATE], "create grant")
        self.executor.resolve_mutation_target(
            project_id=self.pid, action="write", path="audit.txt",
        )
        rows = self.store._connection.execute(
            "SELECT action, outcome, grant_id, detail_json FROM local_tool_events "
            "WHERE action LIKE '%grant_resolved%' ORDER BY created_at DESC"
        ).fetchall()
        self.assertTrue(rows)
        allowed = [r for r in rows if r[1] == "allowed"]
        self.assertTrue(allowed)
        import json

        detail = json.loads(allowed[0][3])
        self.assertEqual(detail.get("matched_grant_id"), grant.grant_id)
        self.assertEqual(detail.get("matched_capability"), "create")

    def test_audit_reports_denied_resolution(self):
        self._create_grant([FileCapability.CREATE], "create grant")
        self.executor.resolve_mutation_target(
            project_id=self.pid, action="write",
            path=str(self.tmp / "outside.txt"),
        )
        rows = self.store._connection.execute(
            "SELECT outcome, detail_json FROM local_tool_events "
            "WHERE action LIKE '%grant_resolved%' AND outcome='denied'"
        ).fetchall()
        self.assertTrue(rows)
        import json

        detail = json.loads(rows[0][1])
        self.assertIsNone(detail.get("matched_grant_id"))
        self.assertTrue(detail.get("reason"))


class Test26CVersionIdentity(unittest.TestCase):
    """Line version identity (tracks the live canonical version)."""

    def test_active_version_locations_report_116(self):
        import re
        import sys

        root = Path(__file__).resolve().parents[1]
        live_version = re.search(
            r'__version__\s*=\s*"([^"]+)"',
            (root / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
        ).group(1)
        # Active version declarations must carry the live canonical version.
        declarations = [
            root / "src" / "amca" / "__init__.py",
            root / "desktop-ui" / "package.json",
        ]
        checked = 0
        for path in declarations:
            if not path.exists():
                continue
            checked += 1
            self.assertIn(live_version,
                          path.read_text(encoding="utf-8", errors="ignore"),
                          f"{path} lost the {live_version} identity")
        # The G8 harness is version-parameterized: its version gate must
        # validate the live canonical version (a stale default parameter is
        # never a version declaration).
        harness = root / "packaging" / "g8_acceptance_harness.py"
        if harness.exists():
            checked += 1
            sys.path.insert(0, str(root / "packaging"))
            from g8_acceptance_harness import evaluate_version_gate

            res = evaluate_version_gate(root.parent, live_version)
            self.assertEqual(res.get("VERSION_ALIGNMENT"), "PASS")
        self.assertGreaterEqual(checked, 2)


if __name__ == "__main__":
    unittest.main()
