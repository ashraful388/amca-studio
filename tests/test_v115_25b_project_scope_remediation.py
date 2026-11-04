"""ZCODE-AMCA-V115-PROJECT-SCOPE-REMEDIATION-25B-R1 regression test suite.

Covers the 25B remediation scope ZCode inherited from Antigravity's terminated 25B:
1. P0 project-scope preflight desync: repeated project-bound turns must update the
   pending Chat state instead of raising "the pending desktop Chat project scope
   changed"; message IDs are unique per run.
2. Waiting-approval lifecycle (Gate-2 fix): waiting-approval states no longer
   block ordinary new messages — historical approvals never block, a live
   PENDING request is superseded with a fresh approval card, and only an
   explicit resume whose requests are still pending keeps strict scope
   matching.
3. Migrated/restored project binding: studio-* project ids are accepted by the
   Local PC permission store.
4. Grant capability semantics: a CREATE-capable durable grant authorizes creating a
   new file (target need not exist); WRITE still requires an existing target.
5. Small-model tool-context over-refusal: the tool-awareness prompt must not prime
   generic refusals for prompts that request no file/command operation.
6. Effective Local PC profile is stamped into run-state artifacts on first and
   subsequent turns (Run details provenance).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.governed_chat import AMCA_TOOL_AWARENESS_SYSTEM
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    LocalToolExecutor,
    PermissionProfile,
    PermissionAction,
)
from amca.state import ProjectState
from amca.service import ServiceContext
from amca.storage import SQLiteStore


def _ready_local(service: ServiceContext, ref: str = "ollama:mock-model") -> None:
    from amca.model_health import READY, record_for_config

    service.model_health_store.record(
        record_for_config(
            kind="local",
            ref=ref,
            endpoint="local://ollama",
            model=ref.split(":", 1)[1],
            state=READY,
            detail="runtime reachable",
            evidence="connection_test",
        )
    )


def _mock_inventory():
    return {
        "provider": "ollama",
        "refreshed": True,
        "runtime_reachable": True,
        "models": [{"model": "mock-model", "fit": 0}],
    }


class RepeatedTurnsTestBase(unittest.TestCase):
    """Shared fixture: one conversation, one project-bound service."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        benchmarks = self.root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        self.service = ServiceContext(
            root=self.root,
            database=self.root / "amca.db",
            benchmarks=benchmarks,
        )
        _ready_local(self.service)

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def _run(self, goal: str, conv_id: str):
        mock_inventory = _mock_inventory()
        with (
            patch.object(type(self.service), "local_models", return_value=mock_inventory),
            patch.object(
                self.service.runs,
                "start_chat",
                return_value={"run_id": "r", "project_id": f"conv-{conv_id}", "status": "queued"},
            ),
        ):
            return self.service.start_chat_run(
                goal=goal,
                participant_ids=["ollama:mock-model"],
                mode="solo",
                selection_policy="manual_model_set",
                conversation_id=conv_id,
            )

    def _load_state(self, project_id: str):
        with SQLiteStore(self.root / "amca.db") as store:
            return store.load_state(project_id)


class TestRepeatedProjectBoundTurns(RepeatedTurnsTestBase):
    """P0: a stable project-bound chat must run repeatedly without the
    'pending desktop Chat project scope changed' preflight failure."""

    def test_second_turn_with_new_goal_updates_state_instead_of_raising(self):
        conv = self.service.conversations.create(title="Repeated turns")
        conv_id = conv["conversation_id"]
        project_id = f"conv-{conv_id}"

        self._run("Reply with exactly: FIRST-TURN-OK", conv_id)
        state_after_first = self._load_state(project_id)
        self.assertIsNotNone(state_after_first)
        self.assertEqual(state_after_first.goal, "Reply with exactly: FIRST-TURN-OK")

        # The v1.1.4 blocker: any second turn with different content raised
        # "the pending desktop Chat project scope changed". It must update instead.
        self._run("Reply with exactly: SECOND-TURN-OK", conv_id)

        state_after_second = self._load_state(project_id)
        self.assertEqual(state_after_second.goal, "Reply with exactly: SECOND-TURN-OK")
        # 25B-R1: the focus must advance with the new user message so the
        # Independent->Discuss anti-anchoring contract keys on the live focus.
        self.assertEqual(state_after_second.current_focus, "Reply with exactly: SECOND-TURN-OK")
        self.assertEqual(state_after_second.active_subproblem, "Reply with exactly: SECOND-TURN-OK")
        self.assertGreaterEqual(state_after_second.version, state_after_first.version)
        self.assertIn("effective_profile", state_after_second.artifacts)
        self.assertIn("local_pc_profile", state_after_second.artifacts)

    def test_user_message_ids_are_unique_across_runs(self):
        conv = self.service.conversations.create(title="Message id uniqueness")
        conv_id = conv["conversation_id"]

        self._run("First message", conv_id)
        self._run("Second message", conv_id)

        messages = self.service.conversations.get(conv_id)["messages"]
        user_ids = [m["message_id"] for m in messages if m["role"] == "user"]
        self.assertEqual(len(user_ids), 2)
        self.assertEqual(len(set(user_ids)), 2, "user message ids must be unique per run")

    def test_effective_profile_stamped_on_every_turn(self):
        conv = self.service.conversations.create(title="Profile provenance")
        conv_id = conv["conversation_id"]
        project_id = f"conv-{conv_id}"
        # Since v1.2.0 the folder belongs to a Project, not to a chat, so the
        # level is raised against a Project that owns one.  The level itself is
        # global, so the chat's turns must still stamp the same profile.
        self.service.studio_project_store.save(
            {
                "project_id": "studio-v115-provenance",
                "name": "v115 provenance",
                "root_path": str(self.root),
            }
        )
        self.service.set_local_permission_profile({
            "project_id": "studio-v115-provenance",
            "profile": "standard",
            "workspace_root": str(self.root),
        })

        self._run("Turn one", conv_id)
        self._run("Turn two", conv_id)

        state = self._load_state(project_id)
        self.assertEqual(str(state.artifacts.get("effective_profile")), "standard")
        self.assertEqual(str(state.artifacts.get("local_pc_profile")), "standard")


class TestWaitingApprovalLifecycle(RepeatedTurnsTestBase):
    """Gate-2 fix (manager fix order §8/§8.1/§8.2): a waiting-approval hold
    no longer blocks ordinary new messages. Historical approvals (consumed,
    expired, denied) never block a fresh turn; a live PENDING request is
    superseded (DENIED by the desktop scope lifecycle) so the user receives
    a fresh approval card instead of a generic failure. The fail-closed
    property that REMAINS: an explicit resume (approval_ids) whose requests
    are still pending still rejects a changed scope."""

    def _force_waiting_approval(self, project_id: str, goal: str):
        with SQLiteStore(self.root / "amca.db") as store:
            state = store.load_state(project_id)
            state.status = "waiting_approval"
            state.goal = goal
            state.version += 1
            store.save_state(state)

    def test_same_scope_new_message_proceeds_instead_of_blocking(self):
        conv = self.service.conversations.create(title="Pending approval same scope")
        conv_id = conv["conversation_id"]
        project_id = f"conv-{conv_id}"
        goal = "Approved task"

        self._run(goal, conv_id)
        self._force_waiting_approval(project_id, goal)

        result = self._run(goal, conv_id)
        self.assertNotEqual(result.get("status"), "waiting_for_approval")
        state = self._load_state(project_id)
        self.assertEqual(state.goal, goal)
        self.assertNotEqual(state.status, "waiting_approval")

    def test_changed_scope_advances_to_the_new_scope(self):
        conv = self.service.conversations.create(title="Pending approval scope change")
        conv_id = conv["conversation_id"]
        project_id = f"conv-{conv_id}"

        self._run("Original approved task", conv_id)
        self._force_waiting_approval(project_id, "Original approved task")

        result = self._run("Different task entirely", conv_id)
        self.assertNotEqual(result.get("status"), "waiting_for_approval")
        state = self._load_state(project_id)
        self.assertEqual(state.goal, "Different task entirely")
        self.assertNotEqual(state.status, "waiting_approval")


class TestStudioProjectIdAcceptance(unittest.TestCase):
    """A prefixed id is accepted only when it resolves to a real row.

    v1.1.8 made the PC access level global, so accepting the prefix alone
    would let any caller invent ``conv-<anything>`` and raise the level for
    every chat on the machine.  These tests previously asserted that the
    prefix was sufficient; they now assert that the row is required.
    """

    def setUp(self):
        from amca.chat_history import ConversationStore

        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "state.sqlite3"
        self.store = LocalPermissionStore(self.database)
        self.conversation_id = ConversationStore(self.database).create(
            title="Prefixed id chat", conversation_id="123"
        )["conversation_id"]

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_a_prefixed_id_resolves_to_its_real_conversation(self):
        self.assertTrue(self.store._project_exists(f"conv-{self.conversation_id}"))

    def test_an_invented_prefixed_id_is_refused(self):
        self.assertFalse(self.store._project_exists("conv-"))
        self.assertFalse(self.store._project_exists("conv-999"))
        self.assertFalse(self.store._project_exists("desktop-abc"))
        self.assertFalse(self.store._project_exists("studio-anything"))


class TestGrantCreateCapability(unittest.TestCase):
    """Grant capability semantics: CREATE authorizes creating a new file."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.database = self.root / "state.sqlite3"
        self.project_id = "create-capability-project"
        with SQLiteStore(self.database) as store:
            store.save_state(ProjectState(project_id=self.project_id, goal="test"))
        self.store = LocalPermissionStore(self.database)
        self.executor = LocalToolExecutor(
            self.store, state_root=self.root / "local-tool-state"
        )
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.STANDARD,
            workspace_root=self.workspace,
        )

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def _grant(self, *capabilities: FileCapability):
        return self.store.create_grant(
            project_id=self.project_id,
            root_path=self.workspace,
            capabilities=capabilities,
            reason="Create-capability test grant",
        )

    def test_create_capability_allows_new_file_proposal(self):
        grant = self._grant(FileCapability.CREATE)
        target = self.workspace / "smoke-v114.txt"
        self.assertFalse(target.exists())
        # Must not raise: CREATE targets are allowed to not exist yet, and the
        # source path is grant-root-relative.
        self.executor.propose_filesystem_operation(
            project_id=self.project_id,
            grant_id=grant.grant_id,
            action=PermissionAction.LOCAL_FILE_MODIFY,
            capability=FileCapability.CREATE,
            source="smoke-v114.txt",
            reason="Create a new smoke file",
        )

    def test_write_capability_still_requires_existing_target(self):
        grant = self._grant(FileCapability.WRITE)
        target = self.workspace / "does-not-exist.txt"
        with self.assertRaises(Exception):
            self.executor.propose_filesystem_operation(
                project_id=self.project_id,
                grant_id=grant.grant_id,
                action=PermissionAction.LOCAL_FILE_MODIFY,
                capability=FileCapability.WRITE,
                source="does-not-exist.txt",
                reason="Write to a nonexistent file must fail closed",
            )


class TestToolAwarenessPromptNoOverRefusal(unittest.TestCase):
    """P1: the tool-awareness prompt must not prime generic refusals for
    prompts that request no file/command operation."""

    def test_qualified_refusal_guidance_only(self):
        self.assertIn(
            "or similar generic refusals when a file operation is requested",
            AMCA_TOOL_AWARENESS_SYSTEM,
        )
        self.assertNotIn(
            "Do NOT claim 'As an AI, I cannot access local files' or similar generic refusals." + chr(10),
            AMCA_TOOL_AWARENESS_SYSTEM,
        )

    def test_direct_reply_guidance_for_conversational_prompts(self):
        self.assertIn(
            "For standard conversational prompts",
            AMCA_TOOL_AWARENESS_SYSTEM,
        )
        self.assertIn(
            "without mentioning workspace or filesystem status",
            AMCA_TOOL_AWARENESS_SYSTEM,
        )

    def test_fail_closed_integrity_rule_retained(self):
        self.assertIn("FAIL-CLOSED INTEGRITY RULE", AMCA_TOOL_AWARENESS_SYSTEM)


if __name__ == "__main__":
    unittest.main()
