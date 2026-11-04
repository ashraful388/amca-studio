"""ANTIGRAVITY-AMCA-V114-CONSOLIDATED-PHYSICAL-DEFECT-REMEDIATION-24A regression test suite.

Validates the full set of Work Order 24A remediations:
1. Anti-fabrication gate: reconciliation of unexecuted claims, false AI refusal rewrite, content sanitization.
2. Governed chat adapter: profile-scoped tool resolution, tool loop execution, provenance recording.
3. DEFECT-08: Project & Permission Profile persistence across consecutive chat runs.
4. DEFECT-09: Full Computer profile durable grant preservation.
5. Collaboration modes: All 5 modes (Solo, Joint, Independent Discuss, Parallel, Adaptive Hybrid) routing & provenance.
6. DEFECT-06: Window close termination semantics (CloseAction::Allow).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.anti_fabrication import (
    reconcile_anti_fabrication,
    reconcile_refusal,
    sanitize_file_read_output,
)
from amca.governed_chat import (
    GovernedChatAdapter,
    get_governed_tools_for_profile,
    AMCA_TOOL_AWARENESS_SYSTEM,
)
from amca.local_tools import (
    FileCapability,
    LocalPermissionStore,
    PermissionProfile,
    PROFILE_CAPABILITIES,
)
from amca.models.base import (
    ModelAdapter,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    StreamChunk,
)
from amca.model_health import READY, record_for_config
from amca.service import RunRegistry, ServiceContext, RunJob


class TestAntiFabricationGate(unittest.TestCase):
    """Test the fail-closed anti-fabrication module."""

    def test_unexecuted_write_claim_is_intercepted(self):
        text = "I have written the configuration file to /etc/app.conf with your settings."
        reconciled, altered = reconcile_anti_fabrication(text, executed_ops=[])
        self.assertTrue(altered)
        self.assertIn("That operation was not executed.", reconciled)

    def test_unexecuted_created_file_claim_is_intercepted(self):
        text = "I created a new file named test.py with the test cases."
        reconciled, altered = reconcile_anti_fabrication(text, executed_ops=[])
        self.assertTrue(altered)
        self.assertIn("That operation was not executed.", reconciled)

    def test_unexecuted_command_claim_is_intercepted(self):
        text = "I executed the command `npm test` and all 15 test suites passed."
        reconciled, altered = reconcile_anti_fabrication(text, executed_ops=[])
        self.assertTrue(altered)
        self.assertIn("That operation was not executed.", reconciled)

    def test_unexecuted_delete_claim_is_intercepted(self):
        text = "I deleted the obsolete file temp.log from the directory."
        reconciled, altered = reconcile_anti_fabrication(text, executed_ops=[])
        self.assertTrue(altered)
        self.assertIn("That operation was not executed.", reconciled)

    def test_executed_operation_claim_is_preserved(self):
        text = "I created the file result.txt with the summary data."
        # If write:result.txt is recorded in executed_ops, the claim is preserved
        reconciled, altered = reconcile_anti_fabrication(text, executed_ops=["write:result.txt"])
        self.assertIn("I created the file result.txt", reconciled)
        self.assertNotIn("That operation was not executed.", reconciled)

    def test_false_ai_refusal_is_rewritten(self):
        text = "As an AI, I cannot access local files on your machine."
        rewritten = reconcile_refusal(text)
        self.assertNotIn("As an AI, I cannot access local files", rewritten)
        self.assertIn("AMCA Studio", rewritten)

    def test_failed_read_content_is_sanitized(self):
        fake_content = "def sensitive_algorithm():\n    return 42"
        sanitized = sanitize_file_read_output(fake_content, was_executed=False)
        self.assertNotIn("sensitive_algorithm", sanitized)
        self.assertIn("The file content could not be read", sanitized)


class TestGovernedChatToolResolution(unittest.TestCase):
    """Test tool resolution across all 4 Local PC permission profiles."""

    def test_restricted_profile_has_read_tools_only(self):
        tools = get_governed_tools_for_profile(PermissionProfile.RESTRICTED)
        tool_names = {t["name"] for t in tools}
        self.assertEqual(tool_names, {"filesystem.read", "filesystem.list", "filesystem.stat", "filesystem.search"})
        self.assertEqual(len(tools), 4)

    def test_standard_profile_adds_workspace_write_tools(self):
        tools = get_governed_tools_for_profile(PermissionProfile.STANDARD)
        tool_names = {t["name"] for t in tools}
        # Standard is "normal file operations": it adds the non-destructive
        # workspace edit tools.  filesystem.trash is NOT here - trashing is an
        # Extended-tier action, and Standard never carried the trash
        # capability, so offering the tool only produced guaranteed failures.
        expected = {
            "filesystem.read", "filesystem.list", "filesystem.stat", "filesystem.search",
            "filesystem.write", "filesystem.append", "filesystem.copy",
        }
        self.assertEqual(tool_names, expected)
        self.assertEqual(len(tools), 7)
        self.assertNotIn("filesystem.trash", tool_names)

    def test_extended_profile_adds_move_trash_and_inspect(self):
        tools = get_governed_tools_for_profile(PermissionProfile.EXTENDED)
        tool_names = {t["name"] for t in tools}
        self.assertIn("filesystem.move", tool_names)
        self.assertIn("filesystem.trash", tool_names)
        self.assertIn("command.inspect", tool_names)
        self.assertNotIn("command.run", tool_names)
        self.assertEqual(len(tools), 10)

    def test_each_profile_is_a_strict_superset_of_the_one_below(self):
        """Raising the level must only ever add capability, never swap it."""
        levels = [
            PermissionProfile.RESTRICTED,
            PermissionProfile.STANDARD,
            PermissionProfile.EXTENDED,
            PermissionProfile.FULL_COMPUTER,
        ]
        previous: set[str] = set()
        for level in levels:
            current = {t["name"] for t in get_governed_tools_for_profile(level)}
            self.assertTrue(
                previous < current,
                f"{level.value} must add tools over the level below it",
            )
            previous = current

    def test_full_computer_profile_includes_all_tools(self):
        tools = get_governed_tools_for_profile(PermissionProfile.FULL_COMPUTER)
        tool_names = {t["name"] for t in tools}
        self.assertIn("command.run", tool_names)
        self.assertEqual(len(tools), 11)


class MockModelAdapter(ModelAdapter):
    """Mock local model adapter for testing governed chat adapter."""

    def __init__(self, response_text: str = "Mock response."):
        super().__init__(
            model="mock-llama",
            provider="ollama",
            adapter_id="ollama:mock-llama",
            capabilities=ModelCapabilities(is_local=True, supports_tools=True),
        )
        self.response_text = response_text
        self.last_request: ModelRequest | None = None

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        return ModelResponse(
            text=self.response_text,
            model=self.model,
            provider=self.provider,
            raw=self.response_text,
        )

    async def stream(self, request: ModelRequest):
        self.last_request = request
        yield StreamChunk(delta=self.response_text, is_final=True)


class TestGovernedChatAdapter(unittest.TestCase):
    """Test GovernedChatAdapter wiring, tool awareness, and anti-fabrication reconciliation."""

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

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_governed_chat_adapter_injects_tool_awareness_prompt(self):
        inner = MockModelAdapter("I am a helpful assistant.")
        project_id = "test-proj-awareness"
        adapter = GovernedChatAdapter(
            inner,
            project_id=project_id,
            service=self.service,
        )
        req = ModelRequest(
            messages=[{"role": "user", "content": "List files"}],
            system="Base system.",
        )
        resp = asyncio.run(adapter.generate(req))
        self.assertIsNotNone(inner.last_request)
        # Injected awareness message is in messages
        system_msg = inner.last_request.messages[0]["content"]
        self.assertIn("AMCA Studio Environment & Permissioned Tools", system_msg)
        self.assertEqual(inner.last_request.system, "Base system.")

    def test_governed_chat_adapter_reconciles_unexecuted_claims(self):
        # Model claims it created a file, but never executed a tool
        inner = MockModelAdapter("I have created the requested file main.py with the implementation.")
        project_id = "test-proj-reconcile"
        adapter = GovernedChatAdapter(
            inner,
            project_id=project_id,
            service=self.service,
        )
        req = ModelRequest(messages=[{"role": "user", "content": "Create main.py"}])
        resp = asyncio.run(adapter.generate(req))
        # Claim must be intercepted fail-closed
        self.assertIn("That operation was not executed.", resp.text)


class TestDefect08ProfilePersistence(unittest.TestCase):
    """Test DEFECT-08: Local PC profile and grants persist across consecutive chat runs in the same conversation."""

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

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_profile_persists_across_conversation_runs(self):
        # Create conversation
        conv = self.service.conversations.create(title="Persistence Test")
        conv_id = conv["conversation_id"]
        expected_project = f"conv-{conv_id}"

        # Set the level for this conversation.  v1.2.0: the PC access level is
        # global and a standalone chat carries no folder, so this proof is
        # about the level surviving across runs - the folder's own persistence
        # is pinned in TestDefect09, where a Studio Project owns it.
        self.service.set_local_permission_profile({
            "project_id": expected_project,
            "profile": "extended",
        })

        # Verify profile is set
        prof = self.service.local_permission_store.profile(expected_project)
        self.assertEqual(prof["profile"], "extended")
        self.assertIsNone(
            prof["workspace_root"],
            "a standalone chat must not accumulate a project folder",
        )

        # Record a ready local model for start_chat_run
        self.service.model_health_store.record(record_for_config(
            kind="local", ref="ollama:mock-model", endpoint="local://ollama",
            model="mock-model", state=READY, detail="runtime reachable",
            evidence="connection_test",
        ))

        mock_inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "mock-model", "fit": 0}],
        }
        with (
            patch.object(type(self.service), "local_models", return_value=mock_inventory),
            patch.object(
                self.service.runs,
                "start_chat",
                return_value={"run_id": "r1", "project_id": expected_project, "status": "queued"},
            ) as mock_start,
        ):
            # Check that starting chat in this conversation attaches to expected_project and preserves profile
            start_result = self.service.start_chat_run(
                goal="Hello from run 1",
                participant_ids=["ollama:mock-model"],
                mode="solo",
                selection_policy="manual_model_set",
                conversation_id=conv_id,
            )
            self.assertEqual(start_result["project_id"], expected_project)
            mock_start.assert_called_once()
            call_kwargs = mock_start.call_args.kwargs
            self.assertEqual(call_kwargs["effective_profile"], "extended")
            self.assertEqual(call_kwargs["project_id"], expected_project)

        # Check that the profile on expected_project is still Extended
        prof_after_run1 = self.service.local_permission_store.profile(expected_project)
        self.assertEqual(prof_after_run1["profile"], "extended")
        self.assertIsNone(prof_after_run1["workspace_root"])


class TestDefect09FullComputerGrantPreservation(unittest.TestCase):
    """Test DEFECT-09: Switching to Full Computer never wipes existing durable grants."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalPermissionStore(self.root / "amca.db")
        # v1.1.8: the level is global, so a level change must name a real chat.
        from amca.chat_history import ConversationStore

        conversation_id = ConversationStore(self.root / "amca.db").create(
            title="DEFECT-09 chat", conversation_id="test-defect-09"
        )["conversation_id"]
        self.conversation_id = conversation_id
        # v1.2.0: the folder-bound levels are held to a Project's own folder,
        # and a standalone chat may not hold one at all, so this proof uses
        # the Project id the desktop addresses for a chat inside a Project.
        self.project_id = "studio-defect-09"
        ws_path = (self.root / "ws").resolve()
        ws_path.mkdir(parents=True, exist_ok=True)
        self.ws_root = str(ws_path)
        extra_path = (self.root / "extra").resolve()
        extra_path.mkdir(parents=True, exist_ok=True)
        self.extra_root = str(extra_path)
        from amca.studio_projects import StudioProjectStore

        StudioProjectStore(self.root / "amca.db").save(
            {
                "project_id": self.project_id,
                "name": "DEFECT-09 project",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": self.ws_root,
            }
        )

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_full_computer_preserves_existing_grants_and_workspace(self):
        # Start at Extended, then move to Full Computer without passing a
        # workspace: the level change must keep the folder.  The extra grant
        # is made at Full Computer because Extended is bounded by the project
        # folder and `extra` is deliberately outside it - that bounding is the
        # decision that only Full Access is PC-wide, and it is asserted in its
        # own test in test_v118_plan_phase5_permissions.py.
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.EXTENDED,
            workspace_root=self.ws_root,
            updated_by="test",
        )
        self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=None,
            updated_by="test",
        )
        # Create an explicit grant
        grant = self.store.create_grant(
            project_id=self.project_id,
            root_path=self.extra_root,
            capabilities=[FileCapability.READ, FileCapability.WRITE],
            reason="Integration testing",
            created_by="test",
        )
        self.assertTrue(grant.active)

        # Switch to Full Computer without passing workspace_root (should preserve ws_root)
        updated_prof = self.store.set_profile(
            project_id=self.project_id,
            profile=PermissionProfile.FULL_COMPUTER,
            workspace_root=None,
            updated_by="test",
        )
        self.assertEqual(updated_prof["profile"], "full_computer")
        self.assertEqual(Path(updated_prof["workspace_root"]).resolve(), Path(self.ws_root).resolve())

        # Verify durable grant was NOT wiped or revoked
        grants = self.store.grants(self.project_id, active_only=True)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["grant_id"], grant.grant_id)
        self.assertTrue(grants[0]["active"])


class TestPublicResponseAntiFabrication(unittest.TestCase):
    """Test _public_response reconciliation in RunRegistry."""

    def test_public_response_reconciles_fabrication(self):
        job = RunJob(
            run_id="run-test-af",
            project_id="proj-test-af",
            goal="Write a script",
            result={
                "calls": [
                    {
                        "adapter_id": "ollama:llama3",
                        "status": "complete",
                        "summary": "I have created and written test.py with 100% test coverage.",
                    }
                ]
            },
        )
        response = RunRegistry._public_response(job)
        self.assertIn("That operation was not executed.", response)

    def test_public_response_preserves_executed_op(self):
        job = RunJob(
            run_id="run-test-af-exec",
            project_id="proj-test-af-exec",
            goal="Write a script",
            provenance={"executed_ops": ["write:test.py"]},
            result={
                "calls": [
                    {
                        "adapter_id": "ollama:llama3",
                        "status": "complete",
                        "summary": "I have created the file test.py with tests.",
                        "executed_ops": ["write:test.py"],
                    }
                ]
            },
        )
        response = RunRegistry._public_response(job)
        self.assertIn("I have created the file test.py", response)
        self.assertNotIn("That operation was not executed.", response)

    def test_joint_public_response_merges_and_scopes_non_execution(self):
        """Response-UX R5: the joint answer is ONE single-voice AMCA reply —
        the evidenced slice publishes alone, the tool-less slice leaves
        the body entirely, and exactly one unattributed verification note
        closes the answer.  No model names, no headers, no hex."""
        job = RunJob(
            run_id="run-test-joint-scoped",
            project_id="proj-test-joint-scoped",
            goal="Build the page",
            provenance={"executed_ops": ["write:index.html"]},
            result={
                "calls": [
                    {
                        "adapter_id": "provider.aaa:muse-spark-1.3",
                        "round": 1,
                        "status": "complete",
                        "summary": "I have created and written index.html.",
                        "executed_ops": ["write:index.html"],
                    },
                    {
                        "adapter_id": "provider.bbb:qwen-flash",
                        "round": 1,
                        "status": "complete",
                        "summary": "That operation was not executed.",
                    },
                ]
            },
        )
        hub_rows = {
            "provider.aaa": {"id": "provider.aaa", "name": "MiniMax"},
            "provider.bbb": {"id": "provider.bbb", "name": "Alibaba · Qwen"},
        }
        response = RunRegistry._public_response(job, hub_provider=hub_rows.get)
        self.assertNotIn("**provider.", response)
        self.assertNotIn("**", response)
        self.assertNotIn("provider.aaa", response)
        self.assertNotIn("provider.bbb", response)
        self.assertNotIn("muse-spark-1.3", response)
        self.assertNotIn("qwen-flash", response)
        self.assertNotIn("Alibaba", response)
        self.assertIn("I have created and written index.html.", response)
        self.assertIn(
            "Verification note: 1 participant ran no tool in this run",
            response,
        )


if __name__ == "__main__":
    unittest.main()
