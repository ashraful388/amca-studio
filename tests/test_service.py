from __future__ import annotations

import base64
import json
import tempfile
import asyncio
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.credentials import CredentialNotFound, MemoryCredentialStore
from amca.collaboration import DeterministicDecompositionPlanner
from amca.domain import (
    CollaborationMode,
    ComputePolicy,
    GoalContract,
    PrivacyMode,
    ProjectState,
    SyncMode,
)
from amca.models import ModelResponse, ScriptedAdapter
from amca.permissions import ApprovalStatus, PermissionStore
from amca.local_runtime import LocalModelRecord
from amca.governed_chat import GovernedChatAdapter
from amca.service import RunRegistry, create_server
from amca.storage import SQLiteStore


class DesktopNanoIntegrationTests(unittest.TestCase):
    def test_legacy_session_cannot_run_desktop_nano_validation(self) -> None:
        import os
        from amca.service import ServiceContext
        from amca.intelligence.local_readiness import LocalReadinessService

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ), patch.object(
            LocalReadinessService, "refresh"
        ):
            os.environ.pop("AMCA_SESSION_TOKEN", None)
            root = Path(__file__).resolve().parents[1]
            context = ServiceContext(
                root=root, database=Path(temporary) / "state.sqlite3",
                benchmarks=root / "benchmarks", credential_store=MemoryCredentialStore(),
            )
            try:
                self.assertFalse(context.nano_agents()["gate_enabled"])
                context.intelligence_runtime.configure_gates(
                    {"nano_agents": True}, contract_version=1, correlation_id="legacy-test",
                )
                with patch.object(type(context.intelligence_runtime), "nano_spawn") as spawn:
                    result = context.run_chat_nano_validation(None, [])
                self.assertEqual(result["status"], "blocked")
                spawn.assert_not_called()
            finally:
                context.close()

    def test_nano_chat_executes_real_validation(self) -> None:
        from amca.service import ServiceContext
        from amca.intelligence.nano_execution import NanoExecutionCoordinator
        from amca.intelligence.local_readiness import LocalReadinessService

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            LocalReadinessService, "refresh"
        ), patch.dict("os.environ", {
            "AMCA_SESSION_TOKEN": "nano-desktop-test-identity-000000000000",
            "AMCA_DESKTOP_AUTOMATIC_CLOUD": "1",
        }):
            root = Path(__file__).resolve().parents[1]
            context = ServiceContext(
                root=root, database=Path(temporary) / "state.sqlite3",
                benchmarks=root / "benchmarks", credential_store=MemoryCredentialStore(),
            )
            state = ProjectState(
                project_id="nano-chat", goal="Reply ok",
                privacy_mode=PrivacyMode.OFFLINE, compute_policy=ComputePolicy.ECONOMY,
            )
            with SQLiteStore(context.database) as store:
                store.save_state(state)
            adapter = ScriptedAdapter([json.dumps({
                "summary": "ok", "events": [], "criterion_evidence": [],
                "focus_resolved": False,
            })], adapter_id="nano-local", is_local=True)
            executed = []
            original = NanoExecutionCoordinator.run

            async def observed(coordinator, assignments, **kwargs):
                executed.extend(assignments)
                return await original(coordinator, assignments, **kwargs)

            with patch.object(NanoExecutionCoordinator, "run", observed):
                started = context.runs.start_chat(
                    goal=state.goal, project_id=state.project_id, adapters=(adapter,),
                    mode=CollaborationMode.SOLO, privacy=PrivacyMode.OFFLINE,
                )
                for thread in threading.enumerate():
                    if thread.name.startswith("amca-chat-"):
                        thread.join(timeout=30)
            current = context.runs.get(started["run_id"])
            context.close()
            self.assertEqual(current["status"], "succeeded", current.get("error"))
            self.assertEqual(len(executed), 1, "Chat must execute one real Nano validation task")
            snapshot = context.nano_agents()
            assignment, = snapshot["assignments"]
            self.assertEqual(assignment["run_ref"], started["run_id"])
            self.assertEqual(assignment["executor_kind"], "deterministic_tool")
            self.assertIn(snapshot["statuses"][assignment["nano_id"]], ("completed", "retired"))
            self.assertIn("Validated", snapshot["results"][assignment["nano_id"]]["summary"])
            self.assertEqual(adapter.calls_consumed, 1)


class DesktopServiceTests(unittest.TestCase):
    def test_nano_status_reports_runtime_gate(self) -> None:
        context = self.server.RequestHandlerClass.context
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(
                type(context.intelligence_runtime), "nano_context",
                return_value={"enabled": enabled},
            ):
                self.assertIs(context.nano_agents()["gate_enabled"], enabled)

    def _await_terminal_run(self, registry, started, timeout_seconds=120):
        """Deterministically wait for a chat run to leave queued/running.

        Uses a generous deadline (the runtime legitimately staggers a second
        admitted invocation by ~15s and serializes chat executions behind a
        global execution lock) and then JOINS the run's chat thread so the
        teardown never races live execution.
        """
        deadline = time.monotonic() + timeout_seconds
        current = started
        while current["status"] in {"queued", "running", "cancelling"} and (
            time.monotonic() < deadline
        ):
            time.sleep(0.05)
            current = registry.get(str(started["run_id"])) or current
        chat_threads = [
            thread for thread in threading.enumerate()
            if thread.name.startswith("amca-chat-")
        ]
        for thread in chat_threads:
            thread.join(timeout=timeout_seconds)
        return registry.get(str(started["run_id"])) or current

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        database = Path(self.temporary.name) / "state.sqlite3"
        self.credential_store = MemoryCredentialStore()
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=database,
            credential_store=self.credential_store,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session")
        self.token = str(session["session_token"])

    def tearDown(self) -> None:
        # 09E: deterministic teardown — stop any live chat execution, join
        # every chat thread, close the server, THEN remove the temp dir.
        # Windows refuses to unlink a sqlite file while a chat thread's
        # connection is still open, which produced cleanup flakes.
        context = getattr(getattr(self.server, "RequestHandlerClass", None), "context", None)
        registry = getattr(context, "runs", None)
        for run_id, (loop, task) in list(getattr(registry, "_active", {}).items()):
            if not task.done():
                loop.call_soon_threadsafe(task.cancel)
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        asyncio.sleep(0), loop)
                    future.result(timeout=5)
                except Exception:
                    pass
        for thread in threading.enumerate():
            if thread.name.startswith("amca-chat-"):
                thread.join(timeout=30)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=30)
        self.temporary.cleanup()

    def _json(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
        authorized: bool = False,
    ) -> dict[str, object]:
        headers = {"Origin": "http://localhost:1420"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["X-AMCA-Session"] = self.token
        request = Request(
            self.base + path, method=method, data=data, headers=headers
        )
        with urlopen(request, timeout=10) as response:
            self.assertEqual(
                response.headers["Access-Control-Allow-Origin"],
                "http://localhost:1420",
            )
            return json.load(response)

    def test_health_is_public_but_state_requires_session(self) -> None:
        health = self._json("/api/health")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["protocol_version"], 15)
        with self.assertRaises(HTTPError) as raised:
            self._json("/api/dashboard")
        self.assertEqual(raised.exception.code, 401)
        dashboard = self._json("/api/dashboard", authorized=True)
        self.assertEqual(dashboard["projects"], [])
        media = self._json("/api/media-runtimes", authorized=True)
        self.assertFalse(media["network_used"])
        self.assertIn("available", media["ocr"])
        self.assertIn("available", media["transcription"])

    def test_studio_project_contains_multiple_chats_shared_files_and_instructions(self) -> None:
        created = self._json(
            "/api/studio-projects", method="POST", authorized=True,
            body={
                "name": "Product Alpha",
                "description": "One workspace with many conversations",
                "instructions": "Use the shared specification.",
                "template": "software",
            },
        )["project"]
        project_id = str(created["project_id"])
        first = self._json(
            "/api/conversations", method="POST", authorized=True,
            body={"mode": "solo", "participant_ids": [], "workspace_project_id": project_id},
        )["conversation"]
        self._json(
            "/api/conversations", method="POST", authorized=True,
            body={"mode": "solo", "participant_ids": [], "workspace_project_id": project_id},
        )
        uploaded = self._json(
            "/api/studio-assets", method="POST", authorized=True,
            body={
                "workspace_project_id": project_id,
                "filename": "shared.md",
                "data_base64": base64.b64encode(b"shared project context").decode("ascii"),
            },
        )["asset"]

        document = self._json(f"/api/studio-projects/{project_id}", authorized=True)
        self.assertEqual(document["chat_count"], 2)
        self.assertEqual(len(document["chats"]), 2)
        self.assertEqual(document["instructions"], "Use the shared specification.")
        self.assertEqual(document["files"][0]["record_id"], uploaded["record_id"])

        updated = self._json(
            f"/api/conversations/{first['conversation_id']}/update",
            method="POST", authorized=True, body={"workspace_project_id": None},
        )["conversation"]
        self.assertIsNone(updated["workspace_project_id"])

    def test_local_tools_profile_grant_approval_execution_and_revoke(self) -> None:
        # v1.2.0: the Project folder is chosen when the Project is created and
        # is then fixed, so the proof creates the Project with its folder.
        workspace = Path(self.temporary.name) / "workspace"
        workspace.mkdir()
        created = self._json(
            "/api/studio-projects",
            method="POST",
            authorized=True,
            body={
                "name": "Local workspace",
                "template": "software",
                "root_path": str(workspace),
            },
        )["project"]
        project_id = str(created["project_id"])
        profile = self._json(
            "/api/local-tools/profile",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "profile": "standard",
                "workspace_root": str(workspace),
            },
        )["profile"]
        self.assertEqual(profile["profile"], "standard")
        grant = self._json(
            "/api/local-tools/grants",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "root_path": str(workspace),
                "capabilities": ["list", "read", "create", "write"],
                "reason": "Use this Project workspace",
            },
        )["grant"]
        grant_id = str(grant["grant_id"])
        with self.assertRaises(HTTPError) as agent_denied:
            self._json(
                "/api/local-tools/execute", method="POST", authorized=True,
                body={
                    "project_id": project_id,
                    "grant_id": grant_id,
                    "action": "create",
                    "path": "agent-denied.txt",
                    "content": "must not be created",
                    "agent_id": "specialist.deep-research",
                },
            )
        self.assertEqual(agent_denied.exception.code, 403)
        self._json(
            "/api/local-tools/execute", method="POST", authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant_id,
                "action": "create",
                "path": "agent-created.txt",
                "content": "created by assigned coding capability",
                "agent_id": "specialist.coding",
            },
        )
        self._json(
            "/api/local-tools/execute",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant_id,
                "action": "create",
                "path": "notes.md",
                "content": "first",
            },
        )
        proposal = self._json(
            "/api/local-tools/approval-proposals",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant_id,
                "approval_action": "local_file_modify",
                "capability": "write",
                "source": "notes.md",
                "reason": "Replace the reviewed Project file",
            },
        )["approval"]
        self._json(
            f"/api/approvals/{proposal['id']}/decision",
            method="POST",
            authorized=True,
            body={"approve": True},
        )
        result = self._json(
            "/api/local-tools/execute",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant_id,
                "action": "write",
                "path": "notes.md",
                "content": "second",
                "approval_id": proposal["id"],
            },
        )["result"]
        self.assertEqual(result["size_bytes"], 6)
        self.assertEqual((workspace / "notes.md").read_text(), "second")

        snapshot = self._json(
            f"/api/local-tools?project_id={project_id}", authorized=True
        )
        self.assertEqual(snapshot["profile"]["profile"], "standard")
        self.assertGreaterEqual(len(snapshot["events"]), 4)
        self._json(
            f"/api/local-tools/grants/{grant_id}/revoke",
            method="POST",
            authorized=True,
            body={"reason": "Test complete"},
        )
        with self.assertRaises(HTTPError) as raised:
            self._json(
                "/api/local-tools/execute",
                method="POST",
                authorized=True,
                body={
                    "project_id": project_id,
                    "grant_id": grant_id,
                    "action": "list",
                },
            )
        self.assertEqual(raised.exception.code, 403)

    def test_local_inspection_command_endpoint_is_exact_approved_and_allowlisted(self) -> None:
        # v1.2.0: the Project folder is chosen when the Project is created.
        workspace = Path(self.temporary.name) / "inspection-workspace"
        workspace.mkdir()
        created = self._json(
            "/api/studio-projects", method="POST", authorized=True,
            body={
                "name": "Inspection workspace",
                "template": "software",
                "root_path": str(workspace),
            },
        )["project"]
        project_id = str(created["project_id"])
        self._json(
            "/api/local-tools/profile", method="POST", authorized=True,
            body={"project_id": project_id, "profile": "extended", "workspace_root": str(workspace)},
        )
        grant = self._json(
            "/api/local-tools/grants", method="POST", authorized=True,
            body={
                "project_id": project_id,
                "root_path": str(workspace),
                "capabilities": ["propose_command"],
                "reason": "Allow bounded repository inspection",
            },
        )["grant"]
        request = self._json(
            "/api/local-tools/commands/request", method="POST", authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant["grant_id"],
                "argv": ["whoami"],
                "cwd": ".",
                "timeout_seconds": 15,
                "reason": "Inspect the operating-system identity",
            },
        )
        approval_id = str(request["approval"]["id"])
        self._json(
            f"/api/approvals/{approval_id}/decision", method="POST", authorized=True,
            body={"approve": True},
        )
        executed = self._json(
            "/api/local-tools/commands/execute", method="POST", authorized=True,
            body={
                "project_id": project_id,
                "grant_id": grant["grant_id"],
                "approval_id": approval_id,
                "argv": ["whoami"],
                "cwd": ".",
                "timeout_seconds": 15,
            },
        )["result"]
        self.assertEqual(executed["exit_code"], 0)
        self.assertEqual(executed["execution_scope"], "inspection_only")
        with self.assertRaises(HTTPError) as blocked:
            self._json(
                "/api/local-tools/commands/request", method="POST", authorized=True,
                body={
                    "project_id": project_id,
                    "grant_id": grant["grant_id"],
                    "argv": ["python", "-c", "print('blocked')"],
                    "cwd": ".",
                    "reason": "Must remain blocked",
                },
            )
        self.assertEqual(blocked.exception.code, 403)

    def test_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(host="0.0.0.0", port=0)

    def test_agent_fabric_endpoints_share_persistent_governed_state(self) -> None:
        catalog = self._json("/api/agent-fabric", authorized=True)
        self.assertEqual(catalog["counts"]["core"], 41)
        self.assertGreaterEqual(catalog["counts"]["built_in_specialists"], 150)

        created = self._json(
            "/api/agent-fabric/custom-agents", method="POST", authorized=True,
            body={
                "name": "Evidence Table Reviewer", "category": "Research & Web",
                "description": "Checks a structured evidence table.",
                "instructions": "Report missing provenance and contradictory rows.",
            },
        )["agent"]
        self.assertEqual(created["origin"], "custom")
        updated = self._json(
            "/api/agent-fabric/settings", method="POST", authorized=True,
            body={"scaling": "conservative", "recursive_spawning": False},
        )["settings"]
        self.assertEqual(updated["scaling"], "conservative")
        plan = self._json(
            "/api/agent-fabric/plan", method="POST", authorized=True,
            body={"goal": "Research evidence and test repository code."},
        )["plan"]
        self.assertTrue(plan["governor"]["authorized"])
        self.assertFalse(plan["model_calls_started"])
        deleted = self._json(
            f"/api/agent-fabric/custom-agents/{created['id']}",
            method="DELETE", authorized=True,
        )
        self.assertEqual(deleted["deleted"], created["id"])

    def test_conversation_endpoints_create_search_rename_update_and_delete(self) -> None:
        created = self._json(
            "/api/conversations",
            method="POST",
            authorized=True,
            body={"mode": "joint", "participant_ids": ["ollama:a", "api:b"]},
        )["conversation"]
        conversation_id = str(created["conversation_id"])
        updated = self._json(
            f"/api/conversations/{conversation_id}/update",
            method="POST",
            authorized=True,
            body={
                "title": "Saved team chat",
                "mode": "solo",
                "participant_ids": ["ollama:a"],
            },
        )["conversation"]
        self.assertEqual(updated["title"], "Saved team chat")
        self.assertEqual(updated["participant_ids"], ["ollama:a"])
        found = self._json(
            "/api/conversations?q=team", authorized=True
        )["conversations"]
        self.assertEqual([item["conversation_id"] for item in found], [conversation_id])
        document = self._json(
            f"/api/conversations/{conversation_id}", authorized=True
        )
        self.assertEqual(document["conversation"]["mode"], "solo")
        self.assertEqual(document["messages"], [])
        deleted = self._json(
            f"/api/conversations/{conversation_id}",
            method="DELETE",
            authorized=True,
        )
        self.assertEqual(deleted["deleted"], conversation_id)

    def test_credential_shaped_chat_is_rejected_before_any_project_write(self) -> None:
        canary = "gsk_abcdefghijklmnopqrstuvwxyz1234567890"
        context = self.server.RequestHandlerClass.context
        with patch.object(
            type(context),
            "local_models",
            return_value={
                "provider": "ollama",
                "refreshed": True,
                "models": [{"model": "safe:1b", "fit": 0}],
            },
        ):
            with self.assertRaises(HTTPError) as rejected:
                self._json(
                    "/api/chat-runs",
                    method="POST",
                    authorized=True,
                    body={
                        "goal": f"Use this secret {canary}",
                        "mode": "solo",
                        "participant_ids": ["ollama:safe:1b"],
                    },
                )
        self.assertEqual(rejected.exception.code, 400)
        self.assertEqual(
            self._json("/api/dashboard", authorized=True)["projects"], []
        )
        self.assertNotIn(canary.encode(), context.database.read_bytes())

    def test_demo_runs_end_to_end_and_exposes_canonical_project(self) -> None:
        started = self._json(
            "/api/demo",
            method="POST",
            authorized=True,
            body={"goal": "Verify the deterministic desktop bridge."},
        )
        run_id = str(started["run_id"])
        deadline = time.monotonic() + 20
        current = started
        while (
            current["status"] in {"queued", "running"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
            current = self._json(f"/api/runs/{run_id}", authorized=True)
        self.assertIn(current["status"], {"succeeded", "partial"}, current)
        self.assertIsNotNone(current["result"])
        project_id = str(current["project_id"])
        project = self._json(
            f"/api/projects/{project_id}", authorized=True
        )
        self.assertEqual(project["project_id"], project_id)
        self.assertGreaterEqual(int(project["version"]), 1)
        events = self._json(
            f"/api/projects/{project_id}/events", authorized=True
        )
        self.assertTrue(events["events"])
        uploaded = self._json(
            "/api/assets",
            method="POST",
            authorized=True,
            body={
                "project_id": project_id,
                "filename": "evidence.md",
                "data_base64": base64.b64encode(
                    b"# Evidence\n\nThe boundary check passed.\n"
                ).decode("ascii"),
            },
        )
        self.assertEqual(uploaded["asset"]["filename"], "evidence.md")
        assets = self._json(
            f"/api/projects/{project_id}/assets", authorized=True
        )
        self.assertEqual(len(assets["assets"]), 1)
        updated_project = self._json(
            f"/api/projects/{project_id}", authorized=True
        )
        self.assertTrue(updated_project["artifacts"])
        database = self.server.RequestHandlerClass.context.database
        with PermissionStore(database) as permissions:
            approval = permissions.request(
                project_id=project_id,
                action="run_code",
                scope={"source_sha256": "c" * 64, "sandbox": "benchmark"},
                reason="Run the selected bounded benchmark candidate.",
            )
        queue = self._json(
            f"/api/approvals?project_id={project_id}", authorized=True
        )
        self.assertEqual(queue["approvals"][0]["status"], "pending")
        decision = self._json(
            f"/api/approvals/{approval.id}/decision",
            method="POST",
            authorized=True,
            body={"approve": True},
        )
        self.assertEqual(decision["approval"]["status"], "approved")

    def test_local_model_library_refresh_and_installed_model_admission(self) -> None:
        installed = LocalModelRecord(
            provider="ollama",
            model="test-local:1b",
            digest="a" * 64,
            size_bytes=1_000_000,
            parameter_size="1B",
            quantization_level="Q4_K_M",
        )
        with patch("amca.service.discover_ollama_models", return_value=(installed,)):
            refreshed = self._json(
                "/api/local-models/refresh",
                method="POST",
                body={},
                authorized=True,
            )
            self.assertEqual(refreshed["models"][0]["model"], "test-local:1b")
            with patch.object(
                self.server.RequestHandlerClass.context.runs,
                "start_local",
                return_value={"run_id": "local-run", "model": "test-local:1b"},
            ) as start_local:
                started = self._json(
                    "/api/local-runs",
                    method="POST",
                    body={
                        "goal": "Answer locally.",
                        "provider": "ollama",
                        "model": "test-local:1b",
                        "mode": "solo",
                    },
                    authorized=True,
                )
                self.assertEqual(started["run_id"], "local-run")
                start_local.assert_called_once()

        with self.assertRaises(HTTPError) as rejected:
            self._json(
                "/api/local-runs",
                method="POST",
                body={"goal": "No", "model": "not-installed", "mode": "solo"},
                authorized=True,
            )
        self.assertEqual(rejected.exception.code, 400)

    def test_unreachable_ollama_is_not_offered_as_an_available_model(self) -> None:
        context = self.server.RequestHandlerClass.context
        with patch("amca.service.discover_ollama_models", side_effect=OSError("offline")):
            catalog = context.local_models(probe_runtime=True)
        self.assertFalse(catalog["runtime_reachable"])
        self.assertEqual(catalog["models"], [])

    def test_chat_registry_surfaces_provider_failure_instead_of_claiming_response(self) -> None:
        context = self.server.RequestHandlerClass.context
        state = ProjectState(
            project_id="desktop-failed-model",
            goal="Return a bounded response.",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(context.database) as store:
            store.save_state(state)
        conversation = context.conversations.create(mode="solo", participant_ids=["broken"])
        conversation_id = str(conversation["conversation_id"])
        context.conversations.add_message(
            conversation_id,
            message_id="failed-user-message",
            role="user",
            content=state.goal,
            project_id=state.project_id,
        )
        adapter = ScriptedAdapter(
            [ModelResponse(text="truncated", model="broken", provider="test", finish_reason="length")],
            adapter_id="broken",
            model="broken",
            is_local=True,
        )
        started = context.runs.start_chat(
            goal=state.goal,
            project_id=state.project_id,
            adapters=[adapter],
            mode=CollaborationMode.SOLO,
            privacy=PrivacyMode.OFFLINE,
            conversation_id=conversation_id,
        )
        deadline = time.monotonic() + 5
        current = started
        while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.02)
            current = context.runs.get(str(started["run_id"])) or current
        self.assertEqual(current["status"], "failed", current)
        saved = context.conversations.get(conversation_id)
        assert saved is not None
        response = saved["messages"][-1]["content"]
        self.assertIn("No model completed", response)
        self.assertIn("ModelOutputTruncatedError", response)
        self.assertNotIn("Models responded", response)

    def test_chat_joint_admits_two_installed_local_models_without_approval(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "models": [
                {"model": "local-a:1b", "fit": 0},
                {"model": "local-b:1b", "fit": 1},
            ],
        }
        context = self.server.RequestHandlerClass.context
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "joint-local", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Compare two local answers.",
                    "mode": "joint",
                    "participant_ids": ["ollama:local-a:1b", "ollama:local-b:1b"],
                },
            )
        self.assertEqual(started["run_id"], "joint-local")
        kwargs = start_chat.call_args.kwargs
        self.assertEqual(kwargs["mode"].value, "joint")
        self.assertEqual(len(kwargs["adapters"]), 2)
        self.assertTrue(all(adapter.capabilities().is_local for adapter in kwargs["adapters"]))

    def test_manual_independent_discuss_lock_routes_without_count_mapping(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "models": [
                {"model": "local-a:1b", "fit": 0},
                {"model": "local-b:1b", "fit": 1},
            ],
        }
        context = self.server.RequestHandlerClass.context
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "independent-local", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Verify both claims independently before discussing.",
                    "mode": "independent_discuss",
                    "participant_ids": ["ollama:local-a:1b", "ollama:local-b:1b"],
                },
            )
        self.assertEqual(started["run_id"], "independent-local")
        kwargs = start_chat.call_args.kwargs
        self.assertEqual(kwargs["mode"].value, "independent_discuss")
        project = self._json(f"/api/projects/{kwargs['project_id']}", authorized=True)
        decision = project["artifacts"]["collaboration_decision"]
        self.assertEqual(decision["architecture"]["mode"], "independent_discuss")
        self.assertEqual(decision["architecture"]["requested_by"], "user_lock")
        self.assertEqual(decision["architecture"]["policy_rule"], "user.mode_lock")

    def test_manual_parallel_lock_plans_and_dispatches_work_units(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "models": [
                {"model": "local-a:1b", "fit": 0},
                {"model": "local-b:1b", "fit": 1},
            ],
        }
        context = self.server.RequestHandlerClass.context
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "parallel-local", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Research sources then analyze the data.",
                    "mode": "parallel",
                    "participant_ids": ["ollama:local-a:1b", "ollama:local-b:1b"],
                },
            )
        self.assertEqual(started["run_id"], "parallel-local")
        kwargs = start_chat.call_args.kwargs
        self.assertEqual(kwargs["mode"].value, "parallel")
        plan = kwargs["decomposition_plan"]
        self.assertGreaterEqual(len(plan.work_units), 2)
        integration = plan.unit("integration")
        self.assertTrue(integration.required)
        project = self._json(f"/api/projects/{kwargs['project_id']}", authorized=True)
        decision = project["artifacts"]["collaboration_decision"]
        decomposition = decision["decomposition"]
        self.assertEqual(decomposition["basis"], "sequencing")
        organization_protocol = project["artifacts"]["agent_organization"][
            "collaboration_protocol"
        ]
        self.assertIn("decomposition", organization_protocol)
        self.assertEqual(organization_protocol["rounds"], 2)

    def test_auto_intelligence_resolves_models_and_live_agent_plan_from_task(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "models": [
                {"model": "qwen-coder:14b", "fit": 0},
                {"model": "qwen-general:32b", "fit": 0},
            ],
        }
        context = self.server.RequestHandlerClass.context
        conversation = context.conversations.create(mode="solo", participant_ids=[])
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "auto-routed", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Research the evidence, update repository code, run tests, and verify the result.",
                    "mode": "solo",
                    "selection_policy": "auto_intelligence",
                    "participant_ids": [],
                    "conversation_id": conversation["conversation_id"],
                },
            )
        self.assertEqual(started["run_id"], "auto-routed")
        kwargs = start_chat.call_args.kwargs
        # The goal asks to "verify the result", so the reviewed C1.1 decision
        # table routes verification-heavy collaborative work to sealed
        # Independent -> Discuss instead of Joint - never by participant count.
        self.assertEqual(kwargs["mode"], CollaborationMode.INDEPENDENT_DISCUSS)
        self.assertEqual(len(kwargs["adapters"]), 2)
        project = self._json(
            f"/api/projects/{kwargs['project_id']}", authorized=True
        )
        self.assertEqual(
            project["artifacts"]["model_selection"]["policy"],
            "auto_intelligence",
        )
        decision = project["artifacts"]["collaboration_decision"]
        self.assertEqual(decision["architecture"]["mode"], "independent_discuss")
        self.assertEqual(
            decision["architecture"]["policy_rule"],
            "task.verification_or_novelty_to_independent",
        )
        self.assertEqual(decision["eligible_model_count"], 2)
        organization = project["artifacts"]["agent_organization"]
        self.assertTrue(organization["governor"]["authorized"])
        self.assertIn("specialist.coding", organization["selected_agents"])
        self.assertEqual(len(organization["model_assignments"]), 2)
        self.assertEqual(organization["collaboration_protocol"]["rounds"], 2)
        selection = project["artifacts"]["model_selection"]
        self.assertEqual(selection["task_complexity"], "collaborative")
        self.assertEqual(selection["collaboration_rounds"], 2)

    def test_auto_intelligence_prefers_small_local_model_for_simple_task(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [
                {"model": "qwen3:1.7b", "fit": 0},
                {"model": "dolphin-llama3:8b", "fit": 0},
            ],
        }
        context = self.server.RequestHandlerClass.context
        conversation = context.conversations.create(mode="solo", participant_ids=[])
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "auto-local", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Reply only: AUTO_OK",
                    "mode": "solo",
                    "selection_policy": "auto_intelligence",
                    "participant_ids": [],
                    "conversation_id": conversation["conversation_id"],
                },
            )
        self.assertEqual(started["run_id"], "auto-local")
        kwargs = start_chat.call_args.kwargs
        self.assertEqual(kwargs["mode"], CollaborationMode.SOLO)
        self.assertEqual(
            [adapter.adapter_id for adapter in kwargs["adapters"]],
            ["ollama:qwen3:1.7b"],
        )
        self.assertTrue(kwargs["adapters"][0].capabilities().is_local)

    def test_chat_attachment_is_persisted_and_injected_into_local_model_context(self) -> None:
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "models": [{"model": "local-a:1b", "fit": 0}],
        }
        context = self.server.RequestHandlerClass.context
        conversation = context.conversations.create(
            mode="solo", participant_ids=["ollama:local-a:1b"]
        )
        with (
            patch.object(type(context), "local_models", return_value=inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "attachment-local", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Use the boundary evidence.",
                    "mode": "solo",
                    "participant_ids": ["ollama:local-a:1b"],
                    "conversation_id": conversation["conversation_id"],
                    "attachments": [
                        {
                            "filename": "evidence.txt",
                            "data_base64": base64.b64encode(
                                b"The attachment boundary passed."
                            ).decode("ascii"),
                        }
                    ],
                },
            )

        self.assertEqual(started["run_id"], "attachment-local")
        kwargs = start_chat.call_args.kwargs
        adapter = kwargs["adapters"][0]
        # v1.1.5: local adapters are wrapped GovernedChatAdapter(inner=_AttachmentContextAdapter);
        # the extracted attachment context is injected via the inner context adapter.
        self.assertIsInstance(adapter, GovernedChatAdapter)
        self.assertIn("The attachment boundary passed.", adapter.inner.context)
        project_id = str(kwargs["project_id"])
        assets = self._json(
            f"/api/projects/{project_id}/assets", authorized=True
        )
        self.assertEqual(assets["assets"][0]["filename"], "evidence.txt")
        self.assertEqual(assets["assets"][0]["extraction_status"], "text_extracted")
        saved = self._json(
            f"/api/conversations/{conversation['conversation_id']}", authorized=True
        )
        self.assertIn("Attachments: evidence.txt", saved["messages"][0]["meta"])

    def test_explicit_session_cloud_policy_starts_without_approval_pause(self) -> None:
        import os

        credential_id = "tabitoken.claude-opus-4-8"
        self.credential_store.set(credential_id, "test-key")
        context = self.server.RequestHandlerClass.context
        with (
            patch.dict(os.environ, {"AMCA_DESKTOP_AUTOMATIC_CLOUD": "1"}),
            patch.object(type(context), "local_models", return_value={
                "provider": "ollama", "refreshed": True, "models": [],
            }),
            patch.object(context.runs, "start_chat", return_value={
                "run_id": "policy-cloud", "status": "queued",
            }) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs", method="POST", authorized=True,
                body={"goal": "Reply ok", "mode": "solo", "participant_ids": [credential_id]},
            )
        self.assertEqual(started["status"], "queued")
        kwargs = start_chat.call_args.kwargs
        adapter = kwargs["adapters"][0]
        from amca.privacy import PrivateHybridAdapter
        self.assertIsInstance(adapter, PrivateHybridAdapter)
        with PermissionStore(context.database) as permissions:
            requests = permissions.list(project_id=kwargs["project_id"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].status.value, "approved")
        self.assertEqual(requests[0].decided_by, "desktop-session-policy")
        self.assertTrue(requests[0].scope_sha256)

    def test_literature_review_chat_retrieves_cites_and_authors_bib(self) -> None:
        """The reported literature-review failure, as a source regression.

        A literature-review Chat request must retrieve scholarly metadata,
        put the fetched citation into the cloud model input, author a native
        .bib artifact, and start without an approval pause.
        """
        import os

        credential_id = "tabitoken.claude-opus-4-8"
        self.credential_store.set(credential_id, "test-key")
        context = self.server.RequestHandlerClass.context

        fake_result = {
            "results": [{
                "provider": "arxiv", "source": "arxiv",
                "query": "AI agent evaluation", "rank": 1,
                "title": "Agents Under Test",
                "url": "https://arxiv.org/abs/2401.00001",
                "authors": ["A. Researcher"],
                "snippet": "We evaluate AI agents on bounded tasks.",
                "published": "2024-01-01T00:00:00Z", "year": 2024,
                "doi": "10.1234/agents-under-test",
                "observed_at": "2026-09-18T00:00:00+00:00",
            }],
            "providers": ["arxiv"], "errors": [],
            "evidence_scope": "bibliographic_metadata_and_abstracts_only",
        }
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with (
            patch.dict(os.environ, {"AMCA_DESKTOP_AUTOMATIC_CLOUD": "1"}),
            patch.object(type(context), "local_models", return_value=empty_inventory),
            patch.object(
                context.runs, "start_chat",
                return_value={"run_id": "lit-review", "status": "queued"},
            ) as start_chat,
            patch(
                "amca.scholarly.search_scholarly",
                return_value=fake_result,
            ) as research,
        ):
            started = self._json(
                "/api/chat-runs", method="POST", authorized=True,
                body={
                    "goal": "Find the latest AI-agent papers and write a literature review with citations and BibTeX.",
                    "mode": "solo",
                    "participant_ids": [credential_id],
                },
            )
        self.assertEqual(started["status"], "queued")
        self.assertNotEqual(started.get("status"), "waiting_for_approval")
        research.assert_called_once()
        query = research.call_args.args[0]
        self.assertIn("AI-agent", query)
        adapter = start_chat.call_args.kwargs["adapters"][0]
        excerpt = adapter.excerpt
        self.assertIn("Retrieved scholarly evidence", excerpt)
        self.assertIn("Agents Under Test", excerpt)
        self.assertIn("https://arxiv.org/abs/2401.00001", excerpt)
        self.assertIn("bibliographic_metadata_and_abstracts_only", excerpt)
        self.assertIn("Bibliography artifact", excerpt)
        project_id = start_chat.call_args.kwargs["project_id"]
        bibliographies = [item for item in context.artifact_list(project_id)
                          if item["artifact_type"] == "bib"]
        self.assertEqual(len(bibliographies), 1)
        bibliography = context.artifact_store.read_source(bibliographies[0]["artifact_id"])
        self.assertIn("@misc{amca1,", bibliography["content"])
        self.assertIn("10.1234/agents-under-test", bibliography["content"])
        # The bibliographic evidence must not leak the credential store.
        self.assertNotIn("test-key", excerpt)

    def test_research_chat_completes_generation_and_saves_bibliography(self) -> None:
        import os

        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "test-key")
        context = self.server.RequestHandlerClass.context
        seen = []

        class RecordedAdapter(ScriptedAdapter):
            async def stream(self, request):
                seen.append(request)
                async for chunk in super().stream(request):
                    yield chunk

        recorded = RecordedAdapter(
            [json.dumps({"summary": "Evidence review [amca1]. Abstract-only evidence.",
                         "events": [], "criterion_evidence": [], "focus_resolved": False})],
            adapter_id=participant, is_local=False,
        )

        evidence = {"results": [{"title": "Agent evaluation", "authors": ["A. Researcher"],
                                  "year": 2025, "url": "https://example.org/paper",
                                  "snippet": "Bounded evaluation of agents."}],
                    "providers": ["crossref"], "errors": [],
                    "evidence_scope": "bibliographic_metadata_and_abstracts_only"}
        with (
            patch.dict(os.environ, {"AMCA_DESKTOP_AUTOMATIC_CLOUD": "1"}),
            patch.object(type(context), "local_models", return_value={
                "provider": "ollama", "refreshed": True, "models": []}),
            patch("amca.scholarly.search_scholarly", return_value=evidence),
            patch.object(type(context), "_adapter_for_profile", return_value=recorded),
        ):
            started = context.start_chat_run(
                goal="Write a literature review of agent evaluation with BibTeX.",
                participant_ids=[participant], mode="solo",
            )
            result = self._await_terminal_run(context.runs, started)
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertEqual(len(seen), 1)
        self.assertIn("Agent evaluation", seen[0].messages[0]["content"])
        self.assertIn("[amca1]", result["result"]["calls"][0]["summary"])
        artifacts = context.artifact_list(result["project_id"])
        bibliography, = [item for item in artifacts if item["artifact_type"] == "bib"]
        self.assertIn("@misc{amca1,", context.artifact_store.read_source(bibliography["artifact_id"])["content"])

    def test_cloud_chat_is_network_free_until_exact_approvals_are_supplied(self) -> None:
        credential_id = "tabitoken.claude-opus-4-8"
        self.credential_store.set(credential_id, "saved-model-key-canary")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            prepared = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "One bounded provider check.",
                    "mode": "solo",
                    "participant_ids": [credential_id],
                    "attachments": [
                        {
                            "filename": "cloud-evidence.txt",
                            "data_base64": base64.b64encode(
                                b"Approved attachment excerpt."
                            ).decode("ascii"),
                        }
                    ],
                },
            )
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertFalse(prepared["network_started"])
        self.assertEqual(prepared["max_api_cost_usd"], 0.5)
        self.assertEqual(len(prepared["approval_ids"]), 1)
        self.assertGreater(
            int(prepared["approvals"][0]["scope"]["excerpt_utf8_bytes"]),
            len("One bounded provider check."),
        )
        self.assertNotIn("saved-model-key-canary", json.dumps(prepared))

        with SQLiteStore(context.database) as store:
            state = store.load_state(str(prepared["project_id"]))
            self.assertIsNotNone(state)
            self.assertTrue(state.goal_contract.budget_is_telemetry)
            state.goal_contract.max_api_cost_usd = 0.0
            state.version += 1
            store.save_state(state)

        approval_id = str(prepared["approval_ids"][0])
        self._json(
            f"/api/approvals/{approval_id}/decision",
            method="POST",
            authorized=True,
            body={"approve": True},
        )
        with (
            patch.object(type(context), "local_models", return_value=empty_inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "approved-cloud", "status": "queued"},
            ) as start_chat,
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "One bounded provider check.",
                    "mode": "solo",
                    "participant_ids": [credential_id],
                    "project_id": prepared["project_id"],
                    "approval_ids": [approval_id],
                    "conversation_id": prepared["conversation_id"],
                },
            )
        self.assertEqual(started["run_id"], "approved-cloud")
        guarded = start_chat.call_args.kwargs["adapters"]
        self.assertEqual(len(guarded), 1)
        self.assertEqual(guarded[0].provider_metadata()["privacy_boundary"], "private-hybrid-excerpt-v1")
        self.assertIn("# Chat response contract", guarded[0].excerpt)
        self.assertIn("Put the direct answer in the JSON summary field", guarded[0].excerpt)
        self.assertIn("no Goal Contract or artifact is required", guarded[0].excerpt)
        self.assertIn("Never invent executed operations or evidence", guarded[0].excerpt)
        self.assertNotIn("Follow the Goal Contract", guarded[0].excerpt)
        self.assertEqual(guarded[0].scope, prepared["approvals"][0]["scope"])

    def test_second_turn_after_decided_approval_gets_fresh_request(self) -> None:
        """Gate-2 fix (manager fix order §8/§8.2, matrix H): a decided
        Turn-1 approval (granted here; consumed later by the governed adapter
        at transmission time) is historical evidence and must not block
        Turn 2 — the second turn plans its own fresh PENDING request instead
        of failing with "the pending desktop Chat project scope changed"."""
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "turn-key")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            first = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "First bounded turn.",
                    "mode": "solo",
                    "participant_ids": [participant],
                },
            )
        self.assertEqual(first["status"], "waiting_for_approval")
        first_approval = str(first["approval_ids"][0])
        decided = self._json(
            f"/api/approvals/{first_approval}/decision",
            method="POST",
            authorized=True,
            body={"approve": True},
        )
        # Matrix L: a human decision is recorded as the user, never as the
        # desktop session policy.
        self.assertEqual(decided["approval"]["decided_by"], "desktop_user")
        with (
            patch.object(type(context), "local_models", return_value=empty_inventory),
            patch.object(
                context.runs,
                "start_chat",
                return_value={"run_id": "turn-one", "status": "queued"},
            ),
        ):
            started = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "First bounded turn.",
                    "mode": "solo",
                    "participant_ids": [participant],
                    "project_id": first["project_id"],
                    "approval_ids": [first_approval],
                    "conversation_id": first["conversation_id"],
                },
            )
        self.assertEqual(started["run_id"], "turn-one")
        # The grant is APPROVED here and becomes CONSUMED by the governed
        # adapter at transmission time (covered by test_permissions.py); the
        # mocked run start never transmits, so APPROVED is the correct state.
        with PermissionStore(context.database) as permissions:
            granted = permissions.get(first_approval)
        self.assertIsNotNone(granted)
        self.assertIn(granted.status, (ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED))

        # Turn 2, well inside Turn 1's 15-minute approval TTL and with a new
        # scope, must plan a fresh PENDING request instead of failing.
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            second = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Second bounded turn with a different scope.",
                    "mode": "solo",
                    "participant_ids": [participant],
                    "project_id": first["project_id"],
                    "conversation_id": first["conversation_id"],
                },
            )
        self.assertEqual(second["status"], "waiting_for_approval")
        self.assertNotIn(first_approval, [str(item) for item in second["approval_ids"]])
        with PermissionStore(context.database) as permissions:
            fresh = permissions.get(str(second["approval_ids"][0]))
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh.status, ApprovalStatus.PENDING)

    def test_new_message_supersedes_pending_scope_changed_request(self) -> None:
        """Gate-2 fix (manager fix order §8.1, matrix I): a newer message
        while a request is still PENDING supersedes the stale request (DENIED
        by the desktop scope lifecycle, never labeled a user decision) and
        produces a fresh approval request instead of a generic chat
        failure."""
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "supersede-key")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            first = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Original pending task.",
                    "mode": "solo",
                    "participant_ids": [participant],
                },
            )
        self.assertEqual(first["status"], "waiting_for_approval")
        stale_id = str(first["approval_ids"][0])
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            second = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Different task entirely with a new scope.",
                    "mode": "solo",
                    "participant_ids": [participant],
                    "project_id": first["project_id"],
                    "conversation_id": first["conversation_id"],
                },
            )
        self.assertEqual(second["status"], "waiting_for_approval")
        self.assertNotIn(stale_id, [str(item) for item in second["approval_ids"]])
        with PermissionStore(context.database) as permissions:
            stale = permissions.get(stale_id)
            fresh = permissions.get(str(second["approval_ids"][0]))
        self.assertIsNotNone(stale)
        self.assertEqual(stale.status, ApprovalStatus.DENIED)
        self.assertEqual(stale.decided_by, "desktop-scope-lifecycle")
        self.assertIn("superseded", str(stale.decision_reason))
        self.assertIsNotNone(fresh)
        self.assertEqual(fresh.status, ApprovalStatus.PENDING)
        self.assertIsNone(fresh.decided_by)

    def test_new_message_with_denied_approval_ids_advances(self) -> None:
        """Gate-2 fix (manager fix order §8.2): resuming with a DENIED
        approval id is resuming with history — the turn must advance as a
        fresh turn (planning a new approval for cloud scopes) instead of
        being blocked by the denied request's scope guard."""
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "denied-resume-key")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            first = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Task that will be denied.",
                    "mode": "solo",
                    "participant_ids": [participant],
                },
            )
        self.assertEqual(first["status"], "waiting_for_approval")
        denied_id = str(first["approval_ids"][0])
        denied = self._json(
            f"/api/approvals/{denied_id}/decision",
            method="POST",
            authorized=True,
            body={"approve": False},
        )
        self.assertEqual(denied["approval"]["status"], "denied")
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            resumed = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Task that will be denied.",
                    "mode": "solo",
                    "participant_ids": [participant],
                    "project_id": first["project_id"],
                    "approval_ids": [denied_id],
                    "conversation_id": first["conversation_id"],
                },
            )
        # Local scopes run without approvals; the historical id must not
        # trigger the old "scope changed" failure.
        self.assertNotEqual(resumed.get("status"), "waiting_for_approval")
        with PermissionStore(context.database) as permissions:
            still_denied = permissions.get(denied_id)
        self.assertIsNotNone(still_denied)
        self.assertEqual(still_denied.status, ApprovalStatus.DENIED)

    def test_default_launch_keeps_transmission_pending(self) -> None:
        """Gate-2 fix (matrix J/A): with no AMCA_DESKTOP_AUTOMATIC_CLOUD in
        the environment — exactly how the corrected desktop shell launches
        the backend — the transmission request stays PENDING for a human
        decision: never auto-approved, network never started."""
        import os

        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "pending-default-key")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
            try:
                prepared = self._json(
                    "/api/chat-runs",
                    method="POST",
                    authorized=True,
                    body={
                        "goal": "Pending by default.",
                        "mode": "solo",
                        "participant_ids": [participant],
                    },
                )
            finally:
                os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertFalse(prepared["network_started"])
        self.assertEqual(len(prepared["approval_ids"]), 1)
        with PermissionStore(context.database) as permissions:
            request = permissions.get(str(prepared["approval_ids"][0]))
        self.assertIsNotNone(request)
        self.assertEqual(request.status, ApprovalStatus.PENDING)
        self.assertIsNone(request.decided_by)

    def test_cloud_approval_mode_preference_automatic(self) -> None:
        """Owner governance decision (v1.1.9): with the persistent preference
        cloud_approval_mode=automatic and NO env override, transmission
        requests are decided approved at creation with the distinct
        ``owner-auto-policy`` provenance, no waiting_for_approval is returned,
        and the run proceeds - the digest/single-use/expiry chain unchanged."""
        import os

        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "owner-auto-policy-key")
        context = self.server.RequestHandlerClass.context
        context.preferences_store.set_cloud_approval_mode("automatic")
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
        try:
            with (
                patch.object(type(context), "local_models", return_value=empty_inventory),
                patch.object(context.runs, "start_chat", return_value={
                    "run_id": "owner-auto-policy", "status": "queued",
                }) as start_chat,
            ):
                prepared = self._json(
                    "/api/chat-runs",
                    method="POST",
                    authorized=True,
                    body={
                        "goal": "Automatic owner policy turn.",
                        "mode": "solo",
                        "participant_ids": [participant],
                    },
                )
        finally:
            os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
            context.preferences_store.set_cloud_approval_mode("exact_scope")
        self.assertEqual(prepared["status"], "queued")
        self.assertTrue(start_chat.called)
        with PermissionStore(context.database) as permissions:
            decided = permissions.list(project_id=start_chat.call_args.kwargs["project_id"])
        self.assertTrue(decided)
        for request in decided:
            self.assertEqual(request.status, ApprovalStatus.APPROVED)
            self.assertEqual(request.decided_by, "owner-auto-policy")
        prefs = context.preferences_store.to_dict()
        self.assertIn("cloud_approval_mode", prefs)
        self.assertIn("cloud_approval_automatic", prefs)
        self.assertEqual(prefs["cloud_approval_mode"], "exact_scope")

    def test_cloud_approval_mode_default_exact_scope(self) -> None:
        """With the preference at its default (exact_scope) and no env
        override, the certified per-turn human gate stays exactly as shipped:
        PENDING, no decided_by, network never started."""
        import os

        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "exact-scope-default-key")
        context = self.server.RequestHandlerClass.context
        context.preferences_store.set_cloud_approval_mode("exact_scope")
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
        try:
            with patch.object(type(context), "local_models", return_value=empty_inventory):
                prepared = self._json(
                    "/api/chat-runs",
                    method="POST",
                    authorized=True,
                    body={
                        "goal": "Exact scope default turn.",
                        "mode": "solo",
                        "participant_ids": [participant],
                    },
                )
        finally:
            os.environ.pop("AMCA_DESKTOP_AUTOMATIC_CLOUD", None)
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertFalse(prepared["network_started"])
        with PermissionStore(context.database) as permissions:
            request = permissions.get(str(prepared["approval_ids"][0]))
        self.assertEqual(request.status, ApprovalStatus.PENDING)
        self.assertIsNone(request.decided_by)

    def test_artifact_file_instruction_present(self) -> None:
        """The tool-awareness prompt instructs models to deliver file outputs
        as artifact blocks (the v1.1.9 'write a file' usability fix)."""
        from amca.governed_chat import AMCA_TOOL_AWARENESS_SYSTEM

        self.assertIn("CREATING FILES AS ARTIFACTS", AMCA_TOOL_AWARENESS_SYSTEM)
        self.assertIn("```artifact", AMCA_TOOL_AWARENESS_SYSTEM)
        self.assertIn("Artifacts panel", AMCA_TOOL_AWARENESS_SYSTEM)

    def test_denied_approval_recovery(self) -> None:
        # 32F Fix 1: denying a desktop Chat transmission approval is terminal.
        # Once no approvals remain open for the project, the conversation must
        # return to its normal resting state so the composer is usable again.
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "denied-approval-key")
        context = self.server.RequestHandlerClass.context
        empty_inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            prepared = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Draft one bounded provider check.",
                    "mode": "solo",
                    "participant_ids": [participant],
                },
            )
        self.assertEqual(prepared["status"], "waiting_for_approval")
        conversation_id = str(prepared["conversation_id"])
        project_id = str(prepared["project_id"])
        approval_id = str(prepared["approval_ids"][0])
        pending = self._json(f"/api/conversations/{conversation_id}", authorized=True)
        self.assertEqual(pending["conversation"]["status"], "waiting_approval")

        denied = self._json(
            f"/api/approvals/{approval_id}/decision",
            method="POST",
            authorized=True,
            body={"approve": False},
        )
        self.assertEqual(denied["approval"]["status"], "denied")

        recovered = self._json(f"/api/conversations/{conversation_id}", authorized=True)
        self.assertEqual(recovered["conversation"]["status"], "idle")
        self.assertIsNone(recovered["conversation"]["active_run_id"])
        with SQLiteStore(context.database) as store:
            state = store.load_state(project_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "created")

        # Boundary: while another approval for the same project is still open,
        # the conversation legitimately stays blocked until every decision is
        # made; the final denial then releases it.
        joint_participants = [
            "tabitoken.claude-opus-4-8",
            "tabitoken.claude-opus-4-8-thinking",
        ]
        for joint_participant in joint_participants:
            self.credential_store.set(joint_participant, f"key-for-{joint_participant}")
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            replanned = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Compare two bounded provider checks.",
                    "mode": "joint",
                    "participant_ids": joint_participants,
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                },
            )
        self.assertEqual(replanned["status"], "waiting_for_approval")
        self.assertEqual(len(replanned["approval_ids"]), 2)
        first_joint_approval, second_joint_approval = (
            str(replanned["approval_ids"][0]),
            str(replanned["approval_ids"][1]),
        )
        self._json(
            f"/api/approvals/{first_joint_approval}/decision",
            method="POST",
            authorized=True,
            body={"approve": False},
        )
        partially_decided = self._json(
            f"/api/conversations/{conversation_id}", authorized=True
        )
        self.assertEqual(
            partially_decided["conversation"]["status"], "waiting_approval"
        )
        self._json(
            f"/api/approvals/{second_joint_approval}/decision",
            method="POST",
            authorized=True,
            body={"approve": False},
        )
        released = self._json(f"/api/conversations/{conversation_id}", authorized=True)
        self.assertEqual(released["conversation"]["status"], "idle")

        # A completed conversation can be idle while its project retains an
        # approval hold with no open permissions.
        context.conversations.attach_project(
            conversation_id, project_id=project_id, status="idle"
        )
        with SQLiteStore(context.database) as store:
            stranded = store.load_state(project_id)
        self.assertIsNotNone(stranded)
        assert stranded is not None
        stranded.version += 1
        stranded.status = "waiting_approval"
        with SQLiteStore(context.database) as store:
            store.save_state(stranded)
        with patch.object(type(context), "local_models", return_value=empty_inventory):
            healed = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Continue after the legacy strand.",
                    "mode": "solo",
                    "participant_ids": [participant],
                    "project_id": project_id,
                    "conversation_id": conversation_id,
                },
            )
        self.assertEqual(healed["status"], "waiting_for_approval")
        self.assertEqual(len(healed["approval_ids"]), 1)

    def test_expired_unused_approval_does_not_hold_chat(self) -> None:
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "expiry-test-key")
        context = self.server.RequestHandlerClass.context
        inventory = {"provider": "ollama", "refreshed": True, "models": []}
        with patch.object(type(context), "local_models", return_value=inventory):
            prepared = self._json(
                "/api/chat-runs", method="POST", authorized=True,
                body={"goal": "First message", "mode": "solo", "participant_ids": [participant]},
            )
        project_id = prepared["project_id"]
        conversation_id = prepared["conversation_id"]
        with PermissionStore(context.database) as permissions:
            request = permissions.decide(prepared["approval_ids"][0], approve=True)
            self.assertFalse(context._release_stale_approval_hold(project_id, permissions))
            from dataclasses import replace
            for status in (request.status, type(request.status).PENDING):
                with self.subTest(status=status):
                    expired = replace(request, status=status, expires_at="2000-01-01T00:00:00Z")
                    context.conversations.attach_project(
                        conversation_id, project_id=project_id, status="waiting_approval"
                    )
                    with patch.object(permissions, "list", return_value=(expired,)):
                        self.assertTrue(context._release_stale_approval_hold(project_id, permissions))
                    restored = context.conversations.find_by_project(project_id)
                    self.assertEqual(restored["conversation"]["status"], "idle")
            self.assertEqual(permissions.get(request.id).status, request.status)

    def test_cloud_chat_rejects_a_model_without_a_saved_credential(self) -> None:
        with patch.object(
            type(self.server.RequestHandlerClass.context),
            "local_models",
            return_value={"provider": "ollama", "refreshed": True, "models": []},
        ):
            with self.assertRaises(HTTPError) as rejected:
                self._json(
                    "/api/chat-runs",
                    method="POST",
                    authorized=True,
                    body={
                        "goal": "Do not call the network.",
                        "mode": "solo",
                        "participant_ids": ["tabitoken.claude-opus-5"],
                    },
                )
        self.assertEqual(rejected.exception.code, 400)

    def test_chat_registry_executes_two_participants_through_joint_kernel(self) -> None:
        context = self.server.RequestHandlerClass.context
        registry = context.runs
        state = ProjectState(
            project_id="desktop-joint-runner",
            goal="Compare two bounded contributions.",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(registry.database) as store:
            store.save_state(state)
        response = json.dumps(
            {
                "summary": "A bounded participant contribution.",
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        adapters = tuple(
            ScriptedAdapter(
                [response, response],
                adapter_id=f"local-test-{index}",
                model=f"local-test-{index}",
                is_local=True,
            )
            for index in range(2)
        )
        conversation = context.conversations.create(
            mode="joint",
            participant_ids=tuple(adapter.adapter_id for adapter in adapters),
        )
        conversation_id = str(conversation["conversation_id"])
        context.conversations.add_message(
            conversation_id,
            message_id="joint-user-message",
            role="user",
            content=state.goal,
            project_id=state.project_id,
        )
        started = registry.start_chat(
            goal=state.goal,
            project_id=state.project_id,
            adapters=adapters,
            mode=CollaborationMode.JOINT,
            privacy=PrivacyMode.OFFLINE,
            conversation_id=conversation_id,
        )
        deadline = time.monotonic() + 30
        current = started
        while current["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.02)
            current = registry.get(str(started["run_id"])) or current
        self.assertEqual(current["status"], "succeeded", current)
        self.assertEqual(current["mode"], "joint")
        self.assertEqual(current["collaboration_rounds"], 2)
        self.assertEqual(len(current["result"]["calls"]), 4)
        live_org = current["result"]["live_organization"]
        self.assertEqual(len(live_org["tasks"]), 4)
        self.assertEqual(live_org["active_count"], 0)
        self.assertTrue(all(
            task["status"] == "retired" for task in live_org["tasks"]
        ))
        saved = context.conversations.get(conversation_id)
        assert saved is not None
        self.assertIsNone(saved["conversation"]["active_run_id"])
        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["messages"][-1]["run_id"], started["run_id"])

    def test_chat_registry_cloud_joint_uses_planned_round_allowance(self) -> None:
        registry = self.server.RequestHandlerClass.context.runs
        state = ProjectState(
            project_id="desktop-cloud-joint-allowance",
            goal="Reply ok together",
            privacy_mode=PrivacyMode.FULL_FRONTIER,
            compute_policy=ComputePolicy.BALANCED,
            goal_contract=GoalContract(goal="Reply ok together", budget_is_telemetry=True),
        )
        with SQLiteStore(registry.database) as store:
            store.save_state(state)
        response = json.dumps({
            "summary": "ok", "events": [], "criterion_evidence": [],
            "focus_resolved": False,
        })
        adapters = tuple(
            ScriptedAdapter([response, response], adapter_id=f"cloud-{index}")
            for index in range(2)
        )
        started = registry.start_chat(
            goal=state.goal, project_id=state.project_id, adapters=adapters,
            mode=CollaborationMode.JOINT, privacy=PrivacyMode.FULL_FRONTIER,
        )
        current = self._await_terminal_run(registry, started)
        self.assertEqual(current["status"], "succeeded", current.get("error"))
        self.assertEqual(current["collaboration_rounds"], 2)
        self.assertEqual(len(current["result"]["calls"]), 4)
        self.assertTrue(current["result"]["synchronization_passed"])
        self.assertEqual([adapter.calls_consumed for adapter in adapters], [2, 2])

    def test_chat_registry_executes_independent_discuss_through_kernel(self) -> None:
        context = self.server.RequestHandlerClass.context
        registry = context.runs
        state = ProjectState(
            project_id="desktop-independent-runner",
            goal="Verify two bounded contributions independently.",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(registry.database) as store:
            store.save_state(state)
        response = json.dumps(
            {
                "summary": "A sealed independent contribution.",
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        adapters = tuple(
            ScriptedAdapter(
                [response] * 6,
                adapter_id=f"local-test-{index}",
                model=f"local-test-{index}",
                is_local=True,
            )
            for index in range(2)
        )
        started = registry.start_chat(
            goal=state.goal,
            project_id=state.project_id,
            adapters=adapters,
            mode=CollaborationMode.INDEPENDENT_DISCUSS,
            privacy=PrivacyMode.OFFLINE,
            collaboration_decision={
                "contract_version": 1,
                "architecture": {"mode": "independent_discuss"},
            },
        )
        current = self._await_terminal_run(registry, started)
        self.assertIn(current["status"], {"partial", "succeeded"}, current)
        self.assertEqual(current["mode"], "independent_discuss")
        self.assertEqual(current["collaboration_rounds"], 2)
        self.assertEqual(
            current["collaboration_decision"]["architecture"]["mode"],
            "independent_discuss",
        )
        mode_trace = current["result"]["mode_trace"]
        self.assertEqual(mode_trace[0]["type"], "independent_phase")
        receipts = mode_trace[0]["receipts"]
        self.assertEqual(
            {receipt["participant_id"] for receipt in receipts},
            {adapter.adapter_id for adapter in adapters},
        )

    def test_chat_registry_executes_parallel_plan_through_kernel(self) -> None:
        context = self.server.RequestHandlerClass.context
        registry = context.runs
        goal = "Research the sources; then analyze the csv data."
        state = ProjectState(
            project_id="desktop-parallel-runner",
            goal=goal,
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(registry.database) as store:
            store.save_state(state)
        response = json.dumps(
            {
                "summary": "A completed work unit result.",
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": True,
            }
        )
        adapters = tuple(
            ScriptedAdapter(
                [response] * 4,
                adapter_id=f"local-test-{index}",
                model=f"local-test-{index}",
                is_local=True,
            )
            for index in range(2)
        )
        plan, meta = DeterministicDecompositionPlanner().plan(
            goal, [adapter.adapter_id for adapter in adapters]
        )
        started = registry.start_chat(
            goal=goal,
            project_id=state.project_id,
            adapters=adapters,
            mode=CollaborationMode.PARALLEL,
            privacy=PrivacyMode.OFFLINE,
            decomposition_plan=plan,
        )
        self.assertEqual(started["collaboration_rounds"], meta["levels"])
        current = self._await_terminal_run(registry, started)
        self.assertIn(current["status"], {"partial", "succeeded"}, current)
        self.assertEqual(current["mode"], "parallel")
        self.assertEqual(len(current["result"]["calls"]), len(plan.work_units))
        parallel_record = current["result"]["mode_trace"][0]
        self.assertEqual(parallel_record["type"], "parallel_decomposition")
        self.assertEqual(
            parallel_record["topological_levels"],
            [[unit.id for unit in level] for level in plan.topological_levels()],
        )
        self.assertTrue(parallel_record["integration"]["passed"])

    def test_chat_run_authors_artifacts_from_model_blocks(self) -> None:
        context = self.server.RequestHandlerClass.context
        registry = context.runs
        goal = "Produce a summary document and a small data table."
        state = ProjectState(
            project_id="desktop-artifact-authoring",
            goal=goal,
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(registry.database) as store:
            store.save_state(state)
        response = json.dumps(
            {
                "summary": (
                    "Done.\n"
                    "```artifact markdown title=Summary doc\n"
                    "# Summary\nproduced by the team\n"
                    "```\n"
                    "```artifact csv title=Numbers\nname,value\na,1\n"
                    "```"
                ),
                "events": [],
                "criterion_evidence": [],
                "focus_resolved": False,
            }
        )
        adapters = tuple(
            ScriptedAdapter([response] * 3, adapter_id=f"local-test-{index}",
                            model=f"local-test-{index}", is_local=True)
            for index in range(2)
        )
        started = registry.start_chat(
            goal=goal,
            project_id=state.project_id,
            adapters=adapters,
            mode=CollaborationMode.JOINT,
            privacy=PrivacyMode.OFFLINE,
        )
        current = self._await_terminal_run(registry, started)
        self.assertEqual(current["status"], "succeeded", current.get("error"))
        artifacts = context.artifact_store.list_for_project(state.project_id)
        types = sorted(item["artifact_type"] for item in artifacts)
        self.assertIn("markdown", types)
        self.assertIn("xlsx", types)
        creators = {item["created_by"] for item in artifacts}
        self.assertTrue(any(creator.startswith("model:local-test") for creator in creators))

    def test_workspace_open_save_conflict_and_traversal(self) -> None:
        def post(path: str, body: dict) -> dict:
            return self._json(path, method="POST", authorized=True, body=body)

        created = post(
            "/api/projects/selftest-ws/workspace/save",
            {"path": "src/main.py", "content": "print('v1')\n"},
        )
        self.assertTrue(created["file"]["created"])
        self.assertEqual(created["file"]["version"], 1)

        opened = self._json(
            "/api/projects/selftest-ws/workspace/open?path=src%2Fmain.py",
            authorized=True,
        )
        self.assertEqual(opened["file"]["content"], "print('v1')\n")
        self.assertEqual(opened["file"]["version"], 1)

        saved = post(
            "/api/projects/selftest-ws/workspace/save",
            {
                "path": "src/main.py",
                "content": "print('v2')\nprint('added')\n",
                "base_version": 1,
            },
        )
        self.assertEqual(saved["file"]["version"], 2)
        self.assertIn("+print('added')", saved["file"]["diff"])

        with self.assertRaises(HTTPError) as conflict:
            post(
                "/api/projects/selftest-ws/workspace/save",
                {"path": "src/main.py", "content": "clobber", "base_version": 1},
            )
        self.assertEqual(conflict.exception.code, 400)
        self.assertIn(
            "changed since version",
            json.loads(conflict.exception.read().decode("utf-8"))["error"],
        )

        listed = self._json("/api/projects/selftest-ws/workspace", authorized=True)
        self.assertEqual(listed["files"][0]["path"], "src/main.py")

        for bad in ("../escape.txt", "/abs.txt"):
            with self.assertRaises(HTTPError):
                post(
                    "/api/projects/selftest-ws/workspace/save",
                    {"path": bad, "content": "no"},
                )

    def test_workspace_revert_and_changes_endpoints(self) -> None:
        def post(path: str, body: dict) -> dict:
            return self._json(path, method="POST", authorized=True, body=body)

        post(
            "/api/projects/selftest-ws/workspace/save",
            {"path": "doc.md", "content": "v1 body"},
        )
        saved = post(
            "/api/projects/selftest-ws/workspace/save",
            {"path": "doc.md", "content": "v1 body\nv2 line", "base_version": 1},
        )
        self.assertEqual(saved["file"]["version"], 2)

        reverted = post(
            "/api/projects/selftest-ws/workspace/revert",
            {"path": "doc.md", "version": 1},
        )
        self.assertEqual(reverted["file"]["version"], 3)

        changes = self._json(
            "/api/projects/selftest-ws/workspace/changes?limit=10",
            authorized=True,
        )
        entries = changes["changes"]
        self.assertEqual(len(entries), 2)
        newest = entries[0]
        self.assertEqual(newest["path"], "doc.md")
        self.assertEqual(newest["version"], 3)
        self.assertIn("-v2 line", newest["diff"])

    def test_argv_command_propose_run_snapshot_flow(self) -> None:
        context = self.server.RequestHandlerClass.context
        ws_root = Path(self.temporary.name) / "ws"
        ws_root.mkdir(parents=True, exist_ok=True)
        from amca.domain import ComputePolicy, ProjectState as TestProjectState

        state = TestProjectState(
            project_id="local-tools-project",
            goal="Bounded command execution",
            privacy_mode=PrivacyMode.OFFLINE,
            compute_policy=ComputePolicy.ECONOMY,
        )
        with SQLiteStore(context.runs.database) as store:
            store.save_state(state)
        self._json(
            "/api/local-tools/profile",
            method="POST",
            authorized=True,
            body={
                "project_id": "local-tools-project",
                "profile": "extended",
                "workspace_root": str(ws_root.parent),
            },
        )
        grant = self._json(
            "/api/local-tools/grants",
            method="POST",
            authorized=True,
            body={
                "project_id": "local-tools-project",
                "root_path": str(ws_root),
                "capabilities": ["propose_command"],
                "reason": "Bounded command grant",
            },
        )
        grant_id = str(grant["grant"]["grant_id"])
        target_dir = Path(self.temporary.name) / "ws"
        target_dir.mkdir(parents=True, exist_ok=True)

        proposed = self._json(
            "/api/local-tools/argv-commands/propose",
            method="POST",
            authorized=True,
            body={
                "project_id": "local-tools-project",
                "grant_id": grant_id,
                "argv": ["git", "--version"],
                "cwd": ".",
                "reason": "Version probe",
            },
        )
        self.assertTrue(proposed["execution_supported"])
        approval_id = str(proposed["approval"]["id"])
        with PermissionStore(context.runs.database) as permissions:
            permissions.decide(approval_id, approve=True, decided_by="test_user")

        started = self._json(
            "/api/local-tools/argv-commands/run",
            method="POST",
            authorized=True,
            body={
                "project_id": "local-tools-project",
                "grant_id": grant_id,
                "approval_id": approval_id,
                "timeout_seconds": 30,
            },
        )
        run_id = str(started["run_id"])
        deadline = time.monotonic() + 20
        snapshot = started
        while time.monotonic() < deadline:
            snapshot = self._json(
                f"/api/local-tools/commands/runs/{run_id}", authorized=True
            )["run"]
            if snapshot["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["exit_code"], 0)
        self.assertIn("git version", snapshot["output"])
        time.sleep(0.3)

    def test_artifact_author_edit_restore_flow(self) -> None:
        base = "/api/projects/selftest-art/artifacts"

        created = self._json(
            base,
            method="POST",
            authorized=True,
            body={
                "artifact_type": "markdown",
                "title": "Quarterly report",
                "content": "# Q3\nbody",
                "created_by": "model:test via chat c9",
                "chat_id": "c9",
            },
        )
        aid = created["artifact"]["artifact_id"]
        self.assertEqual(created["artifact"]["current_version"], 1)

        edited = self._json(
            f"{base}/{aid}/edit",
            method="POST",
            authorized=True,
            body={"content": "# Q3\nbody\nmore", "base_version": 1},
        )
        self.assertEqual(edited["artifact"]["current_version"], 2)

        restored = self._json(
            f"{base}/{aid}/restore",
            method="POST",
            authorized=True,
            body={"version": 1},
        )
        self.assertEqual(restored["artifact"]["current_version"], 3)

        source = self._json(
            f"{base}/{aid}/source?version=2", authorized=True
        )
        self.assertIn("more", source["source"]["content"])

        listed = self._json(base, authorized=True)
        self.assertEqual(listed["artifacts"][0]["artifact_id"], aid)

    def test_ui_source_preview_requires_session_and_preserves_project_scope(self):
        project = self.server.amca_context.studio_project_store.save({"name": "Preview test"})
        pid = project["project_id"]
        record = self.server.amca_context.import_studio_asset(workspace_project_id=pid, filename="source.txt", data=b"cobalt evidence")
        from urllib.parse import quote
        endpoint = "/api/studio-asset-content?project_id=" + quote(pid) + "&record_id=" + quote(record.record_id)
        with self.assertRaises(HTTPError) as denied:
            self._json(endpoint)
        self.assertEqual(denied.exception.code, 401)
        result = self._json(endpoint, authorized=True)
        self.assertEqual(base64.b64decode(result["data_base64"]), b"cobalt evidence")
        with self.assertRaises(HTTPError):
            self._json(endpoint.replace(pid, "other-project"), authorized=True)
        self._json("/api/studio-assets/" + record.asset_id + "?project_id=" + quote(pid), method="DELETE", authorized=True)
        self.assertEqual(self.server.amca_context.studio_project(pid)["files"], [])
        with self.assertRaises(HTTPError): self._json(endpoint, authorized=True)

    def test_ui_artifact_download_returns_bytes_and_denies_other_project(self):
        context=self.server.amca_context
        meta=context.artifact_author("ui-project", {"artifact_type":"markdown", "title":"Report", "content":"# Evidence"})
        result=context.artifact_export("ui-project",meta["artifact_id"],{"format":"html"})
        self.assertIn(b"Evidence",base64.b64decode(result["data_base64"]))
        with self.assertRaises(PermissionError): context.artifact_export("other-project",meta["artifact_id"],{"format":"html"})

    def test_artifact_export_endpoint_html_and_unavailable(self) -> None:
        base = "/api/projects/selftest-art/artifacts"
        created = self._json(
            base,
            method="POST",
            authorized=True,
            body={
                "artifact_type": "markdown",
                "title": "Exportable",
                "content": "# Hello\nworld",
                "created_by": "model:test",
            },
        )
        aid = created["artifact"]["artifact_id"]

        exported = self._json(
            f"{base}/{aid}/export",
            method="POST",
            authorized=True,
            body={"format": "html"},
        )
        self.assertEqual(exported["export"]["status"], "exported")
        self.assertEqual(exported["export"]["target_format"], "html")

        unavailable = self._json(
            f"{base}/{aid}/export",
            method="POST",
            authorized=True,
            body={"format": "pdf"},
        )
        self.assertEqual(unavailable["export"]["status"], "converter_unavailable")
        self.assertIn("PyMuPDF", unavailable["export"]["reason"])

    def test_credential_status_never_returns_secret_values(self) -> None:
        status = self._json("/api/credentials/status", authorized=True)
        self.assertEqual(
            status["persistent_store"],
            "windows_credential_manager_with_dpapi_fallback",
        )
        self.assertTrue(status["write_available_in_ui"])
        self.assertEqual(len(status["configured"]), 4)
        self.assertNotIn("key", json.dumps(status).casefold())

    def test_credentials_are_write_only_and_model_specific(self) -> None:
        canary = "service-secret-canary-never-return"
        credential_id = "tabitoken.claude-opus-4-8"
        saved = self._json(
            "/api/credentials",
            method="POST",
            authorized=True,
            body={"credential_id": credential_id, "secret": canary},
        )
        self.assertTrue(saved["configured"][credential_id])
        self.assertNotIn(canary, json.dumps(saved))
        self.assertEqual(self.credential_store.get(credential_id), canary)
        database = self.server.RequestHandlerClass.context.database
        if database.exists():
            self.assertNotIn(canary.encode(), database.read_bytes())

        deleted = self._json(
            f"/api/credentials/{credential_id}",
            method="DELETE",
            authorized=True,
        )
        self.assertFalse(deleted["configured"][credential_id])
        with self.assertRaises(CredentialNotFound):
            self.credential_store.get(credential_id)

    def test_custom_model_profile_is_write_only_selectable_and_deletable(self) -> None:
        canary = "custom-provider-secret-canary-never-persist"
        created = self._json(
            "/api/model-profiles",
            method="POST",
            authorized=True,
            body={
                "provider_kind": "openai_compatible",
                "label": "Free compatible model",
                "model": "free-model-8b",
                "endpoint": "https://provider.example/v1/",
                "pricing_basis": "free",
                "secret": canary,
            },
        )
        self.assertFalse(created["network_started"])
        profile = created["profile"]
        profile_id = str(profile["profile_id"])
        self.assertTrue(profile["configured"])
        self.assertEqual(self.credential_store.get(profile_id), canary)
        self.assertNotIn(canary, json.dumps(created))
        database = self.server.RequestHandlerClass.context.database
        self.assertNotIn(canary.encode(), database.read_bytes())

        context = self.server.RequestHandlerClass.context
        with patch.object(
            type(context),
            "local_models",
            return_value={"provider": "ollama", "refreshed": True, "models": []},
        ):
            prepared = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Prepare one free compatible call.",
                    "mode": "solo",
                    "participant_ids": [profile_id],
                },
            )
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertFalse(prepared["network_started"])
        self.assertEqual(prepared["max_api_cost_usd"], 0)

        deleted = self._json(
            f"/api/model-profiles/{profile_id}",
            method="DELETE",
            authorized=True,
        )
        self.assertEqual(deleted["deleted"], profile_id)
        self.assertNotIn(profile_id, deleted["status"]["configured"])
        with self.assertRaises(CredentialNotFound):
            self.credential_store.get(profile_id)

    def test_provider_level_byok_catalog_is_selectable_without_fixed_profiles(self) -> None:
        created = self._json(
            "/api/model-hub/providers",
            method="POST",
            authorized=True,
            body={
                "provider_kind": "openai",
                "name": "Provider-level key",
                "endpoint": "https://api.openai.com/v1",
                "pricing_mode": "auto",
                "model_ids": ["gpt-catalog-a", "gpt-catalog-b"],
                "secret": "provider-level-secret-canary",
            },
        )
        provider = created["provider"]
        provider_id = str(provider["id"])
        self.assertEqual(provider["model_ids"], ["gpt-catalog-a", "gpt-catalog-b"])
        self.assertNotIn("provider-level-secret-canary", json.dumps(created))

        updated = self._json(
            f"/api/model-hub/providers/{provider_id}/update",
            method="POST",
            authorized=True,
            body={"name": "Edited provider", "model_ids": ["gpt-catalog-b"]},
        )
        self.assertEqual(updated["provider"]["name"], "Edited provider")
        self.assertEqual(updated["provider"]["model_ids"], ["gpt-catalog-b"])

        context = self.server.RequestHandlerClass.context
        with patch.object(
            type(context),
            "local_models",
            return_value={"provider": "ollama", "refreshed": True, "models": []},
        ):
            prepared = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Use one dynamically catalogued model.",
                    "mode": "solo",
                    "participant_ids": [f"{provider_id}:gpt-catalog-b"],
                },
            )
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertEqual(prepared["participant_ids"], [f"{provider_id}:gpt-catalog-b"])

    def test_provider_catalog_discovery_is_bounded_metadata_only(self) -> None:
        created = self._json(
            "/api/model-hub/providers",
            method="POST",
            authorized=True,
            body={
                "provider_kind": "openai",
                "name": "Discoverable provider",
                "endpoint": "https://api.openai.com/v1",
                "pricing_mode": "auto",
                "secret": "catalog-only-secret-canary",
            },
        )
        provider_id = str(created["provider"]["id"])

        class Response:
            def __enter__(self) -> "Response": return self
            def __exit__(self, *_: object) -> None: return None
            def read(self, _limit: int) -> bytes:
                return b'{"data":[{"id":"gpt-live-a"},{"id":"gpt-live-b"}]}'

        with patch("amca.service._urlopen_without_redirects", return_value=Response()) as opened:
            discovered = self._json(
                f"/api/model-hub/providers/{provider_id}/discover",
                method="POST",
                authorized=True,
                body={},
            )
        self.assertEqual(discovered["catalog_count"], 2)
        self.assertTrue(discovered["network_started"])
        self.assertFalse(discovered["inference_started"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/models")
        self.assertNotIn("catalog-only-secret-canary", request.full_url)

    def test_joint_cloud_budget_and_approvals_cover_the_review_round(self) -> None:
        participants = [
            "tabitoken.claude-opus-4-8",
            "tabitoken.claude-opus-4-8-thinking",
        ]
        for participant in participants:
            self.credential_store.set(participant, f"key-for-{participant}")
        context = self.server.RequestHandlerClass.context
        with patch.object(
            type(context),
            "local_models",
            return_value={"provider": "ollama", "refreshed": True, "models": []},
        ):
            prepared = self._json(
                "/api/chat-runs",
                method="POST",
                authorized=True,
                body={
                    "goal": "Compare the independent analyses, challenge errors, and verify the final result.",
                    "mode": "joint",
                    "participant_ids": participants,
                },
            )
        self.assertEqual(prepared["status"], "waiting_for_approval")
        self.assertEqual(prepared["max_api_cost_usd"], 2.0)
        self.assertEqual([item["max_uses"] for item in prepared["approvals"]], [2, 2])
        self.assertEqual(prepared["agent_plan"]["collaboration_protocol"]["rounds"], 2)

    def test_custom_model_rejects_non_https_before_writing_credential(self) -> None:
        with self.assertRaises(HTTPError) as rejected:
            self._json(
                "/api/model-profiles",
                method="POST",
                authorized=True,
                body={
                    "provider_kind": "openai",
                    "label": "Unsafe model",
                    "model": "gpt-test",
                    "endpoint": "http://provider.example/v1",
                    "pricing_basis": "token",
                    "input_cost_per_million": 1,
                    "output_cost_per_million": 1,
                    "secret": "must-not-be-stored",
                },
            )
        self.assertEqual(rejected.exception.code, 400)
        self.assertEqual(len(self.credential_store._values), 0)

    def test_unknown_credential_id_is_rejected_without_echoing_secret(self) -> None:
        canary = "rejected-secret-canary"
        with self.assertRaises(HTTPError) as rejected:
            self._json(
                "/api/credentials",
                method="POST",
                authorized=True,
                body={"credential_id": "unknown", "secret": canary},
            )
        self.assertEqual(rejected.exception.code, 400)
        self.assertNotIn(canary, rejected.exception.read().decode("utf-8"))

    def test_unknown_run_cannot_be_cancelled(self) -> None:
        with self.assertRaises(HTTPError) as missing:
            self._json(
                "/api/runs/does-not-exist/cancel",
                method="POST",
                body={},
                authorized=True,
            )
        self.assertEqual(missing.exception.code, 404)

    def test_local_run_cancellation_and_job_metadata_are_durable(self) -> None:
        async def wait_forever(_arguments: object) -> int:
            await __import__("asyncio").Event().wait()
            return 0

        registry = self.server.RequestHandlerClass.context.runs
        with patch("amca.service._run_live", side_effect=wait_forever):
            started = registry.start_local(
                goal="Cancel this bounded local run.", model="test-local:1b"
            )
            deadline = time.monotonic() + 3
            current = started
            while current["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(0.02)
                current = registry.get(str(started["run_id"])) or current
            self.assertEqual(current["status"], "running")
            registry.cancel(str(started["run_id"]))
            while current["status"] in {"running", "cancelling"} and time.monotonic() < deadline:
                time.sleep(0.02)
                current = registry.get(str(started["run_id"])) or current
        self.assertEqual(current["status"], "cancelled")
        reloaded = RunRegistry(
            root=registry.root,
            database=registry.database,
            run_dir=registry.run_dir,
        ).get(str(started["run_id"]))
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["model"], "test-local:1b")
        self.assertEqual(reloaded["privacy"], "offline")

    def test_collaboration_progress_surfaces_top_level_and_adaptive_transitions(self) -> None:
        state = ProjectState(
            project_id="test-adaptive-proj",
            goal="Research quantum computing scaling",
            current_collaboration_mode=CollaborationMode.JOINT,
            current_sync_mode=SyncMode.HARD,
            artifacts={
                "collaboration_decision": {
                    "architecture": {
                        "mode": "adaptive_hybrid",
                        "sync_mode": "soft",
                        "policy_rule": "task.ambiguous_decomposition_to_adaptive",
                    }
                },
                "adaptive-transition:1": {
                    "phase_index": 1,
                    "from_mode": "parallel",
                    "selected_mode": "joint",
                    "selected_sync_mode": "hard",
                    "policy_rule": "safety.conflict_to_joint",
                    "observed_triggers": ["parallel_conflict"],
                    "phase_stop_reason": "integration_blocked",
                    "state_version_before": 1,
                    "state_version_after": 2,
                    "safety_override": True,
                },
                "adaptive-transition:0": {
                    "phase_index": 0,
                    "from_mode": "",
                    "selected_mode": "parallel",
                    "selected_sync_mode": "soft",
                    "policy_rule": "adaptive.ambiguous_alignment_to_joint",
                    "observed_triggers": ["weakly_coupled"],
                    "phase_stop_reason": "round_limit",
                    "state_version_before": 0,
                    "state_version_after": 1,
                    "safety_override": False,
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        # Test GET /api/projects/{id}/collaboration-progress
        response = self._json(
            f"/api/projects/{state.project_id}/collaboration-progress",
            authorized=True,
        )
        progress = response["collaboration_progress"]
        self.assertEqual(progress["top_level_architecture"], "adaptive_hybrid")
        self.assertEqual(progress["current_base_mode"], "joint")
        self.assertEqual(progress["current_sync_mode"], "hard")
        self.assertEqual(len(progress["adaptive_transitions"]), 2)
        # Ensure sorted by phase_index
        self.assertEqual(progress["adaptive_transitions"][0]["phase_index"], 0)
        self.assertEqual(progress["adaptive_transitions"][0]["selected_mode"], "parallel")
        self.assertEqual(progress["adaptive_transitions"][1]["phase_index"], 1)
        self.assertEqual(progress["adaptive_transitions"][1]["selected_mode"], "joint")
        self.assertTrue(progress["adaptive_transitions"][1]["safety_override"])

        # Test GET /api/projects/{id} has embedded collaboration_progress
        project_doc = self._json(
            f"/api/projects/{state.project_id}",
            authorized=True,
        )
        self.assertIn("collaboration_progress", project_doc)
        self.assertEqual(
            project_doc["collaboration_progress"]["top_level_architecture"],
            "adaptive_hybrid",
        )

    def test_collaboration_progress_preserves_top_level_adaptive_during_child_mode(self) -> None:
        state = ProjectState(
            project_id="test-child-mode-proj",
            goal="Analyze multi-agent consensus",
            current_collaboration_mode=CollaborationMode.PARALLEL,
            current_sync_mode=SyncMode.SOFT,
            artifacts={
                "collaboration_decision": {
                    "architecture": {
                        "mode": "adaptive_hybrid",
                        "sync_mode": "soft",
                    }
                }
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(
            state.project_id
        )
        self.assertIsNotNone(progress)
        self.assertEqual(progress["top_level_architecture"], "adaptive_hybrid")
        self.assertEqual(progress["current_base_mode"], "parallel")

    def test_collaboration_progress_surfaces_parallel_work_units_and_integration(self) -> None:
        state = ProjectState(
            project_id="test-parallel-proj",
            goal="Decomposed code generation",
            current_collaboration_mode=CollaborationMode.PARALLEL,
            current_sync_mode=SyncMode.SOFT,
            artifacts={
                "collaboration_decision": {
                    "architecture": {
                        "mode": "parallel",
                    },
                    "decomposition": {
                        "work_unit_count": 3,
                    },
                },
                "parallel-integration:0": {
                    "phase_index": 0,
                    "work_units": [
                        {
                            "id": "u1",
                            "focus": "Parser module",
                            "status": "completed",
                            "assignee_ids": ["m1"],
                            "dependencies": [],
                        },
                        {
                            "id": "u2",
                            "focus": "AST optimizer",
                            "status": "active",
                            "assignee_ids": ["m2"],
                            "dependencies": ["u1"],
                        },
                        {
                            "id": "u3",
                            "focus": "Code emitter",
                            "status": "blocked",
                            "assignee_ids": ["m1"],
                            "dependencies": ["u2"],
                            "conflicts": ["Type signature mismatch on Node"],
                        },
                    ],
                    "integration": {
                        "passed": False,
                        "conflicts": ["Type signature mismatch on Node"],
                    },
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(
            state.project_id
        )
        self.assertIsNotNone(progress)
        par = progress["parallel_progress"]
        self.assertIsNotNone(par)
        self.assertEqual(par["total_units"], 3)
        self.assertEqual(par["completed_units"], 1)
        self.assertEqual(par["active_units"], 1)
        self.assertEqual(par["blocked_units"], 1)
        self.assertEqual(par["integration_status"], "blocked")
        self.assertEqual(len(par["units"]), 3)
        self.assertEqual(par["units"][0]["id"], "u1")
        self.assertEqual(par["units"][0]["status"], "completed")
        self.assertEqual(par["units"][2]["conflicts"], ["Type signature mismatch on Node"])

    def test_collaboration_progress_graceful_degradation(self) -> None:
        state = ProjectState(
            project_id="test-clean-proj",
            goal="Simple solo task",
            current_collaboration_mode=CollaborationMode.SOLO,
            current_sync_mode=SyncMode.ASYNC,
            artifacts={},
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(
            state.project_id
        )
        self.assertIsNotNone(progress)
        self.assertEqual(progress["top_level_architecture"], "solo")
        self.assertEqual(progress["current_base_mode"], "solo")
        self.assertEqual(progress["current_sync_mode"], "async")
        self.assertEqual(progress["adaptive_transitions"], [])
        self.assertIsNone(progress["parallel_progress"])

    def test_collaboration_decision_inspector_surfaces_architecture_and_model_set(self) -> None:
        state = ProjectState(
            project_id="test-inspector-proj",
            goal="Refactor core components with multi-model team",
            current_collaboration_mode=CollaborationMode.JOINT,
            current_sync_mode=SyncMode.HARD,
            artifacts={
                "collaboration_decision": {
                    "contract_version": 1,
                    "model_set": {
                        "policy": "diverse_specialists",
                        "model_refs": ["qwen_3_17b", "claude_sonnet"],
                        "rationale": "Complementary capabilities selected for multi-disciplinary refactor.",
                        "confidence": "high",
                        "expected_cost_usd": 0.042,
                    },
                    "architecture": {
                        "mode": "joint",
                        "sync_mode": "hard",
                        "policy_rule": "task.high_coupling_to_joint",
                        "triggers": ["tight_type_coupling", "shared_state"],
                        "reasons": ["High architectural interdependence detected.", "Multiple distinct specialists required."],
                        "requested_by": "auto",
                        "expected_rounds": 3,
                    },
                    "eligible_model_count": 2,
                    "task_shape": {
                        "complexity": "high",
                        "coupled": True,
                        "decomposable": False,
                        "verification_heavy": True,
                        "novelty_required": False,
                    },
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        response = self._json(
            f"/api/projects/{state.project_id}/collaboration-progress",
            authorized=True,
        )
        self.assertIn("collaboration_progress", response)
        progress = response["collaboration_progress"]
        self.assertIn("collaboration_decision", progress)
        decision = progress["collaboration_decision"]
        self.assertIsNotNone(decision)
        self.assertEqual(decision["model_set"]["policy"], "diverse_specialists")
        self.assertEqual(decision["model_set"]["model_refs"], ["qwen_3_17b", "claude_sonnet"])
        self.assertEqual(decision["model_set"]["expected_cost_usd"], 0.042)
        self.assertEqual(decision["architecture"]["mode"], "joint")
        self.assertEqual(decision["architecture"]["requested_by"], "auto")
        self.assertEqual(decision["architecture"]["policy_rule"], "task.high_coupling_to_joint")
        self.assertEqual(decision["task_shape"]["complexity"], "high")
        self.assertTrue(decision["task_shape"]["coupled"])
        self.assertFalse(decision["task_shape"]["decomposable"])

    def test_collaboration_decision_inspector_handles_user_lock_and_auto(self) -> None:
        state = ProjectState(
            project_id="test-lock-proj",
            goal="User locked pipeline",
            current_collaboration_mode=CollaborationMode.PARALLEL,
            current_sync_mode=SyncMode.SOFT,
            artifacts={
                "collaboration_decision": {
                    "contract_version": 1,
                    "model_set": {
                        "policy": "user_locked",
                        "model_refs": ["model_alpha", "model_beta"],
                        "rationale": "Explicit user team configured.",
                    },
                    "architecture": {
                        "mode": "parallel",
                        "sync_mode": "soft",
                        "policy_rule": "manual.user_lock",
                        "triggers": ["user_intent"],
                        "reasons": ["Explicit user configuration applied."],
                        "requested_by": "user_lock",
                        "expected_rounds": 2,
                    },
                    "eligible_model_count": 2,
                    "task_shape": {
                        "complexity": "medium",
                        "coupled": False,
                        "decomposable": True,
                        "verification_heavy": False,
                        "novelty_required": False,
                    },
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(state.project_id)
        self.assertIsNotNone(progress)
        decision = progress.get("collaboration_decision")
        self.assertIsNotNone(decision)
        self.assertEqual(decision["architecture"]["requested_by"], "user_lock")
        self.assertEqual(decision["architecture"]["policy_rule"], "manual.user_lock")

    def test_collaboration_decision_inspector_handles_feasibility_degradation(self) -> None:
        state = ProjectState(
            project_id="test-degrade-proj",
            goal="Multi-model task with only one model configured",
            current_collaboration_mode=CollaborationMode.SOLO,
            current_sync_mode=SyncMode.ASYNC,
            artifacts={
                "collaboration_decision": {
                    "contract_version": 1,
                    "model_set": {
                        "policy": "single_available",
                        "model_refs": ["qwen_3_17b"],
                        "rationale": "Single model available on local workstation.",
                    },
                    "architecture": {
                        "mode": "solo",
                        "sync_mode": "async",
                        "policy_rule": "feasibility.single_model_fallback",
                        "triggers": ["insufficient_models"],
                        "reasons": ["Degraded from Joint to Solo due to single available provider."],
                        "requested_by": "auto",
                        "expected_rounds": 1,
                        "candidate_mode": "joint",
                        "degradation_reason": "only one eligible model available",
                    },
                    "eligible_model_count": 1,
                    "task_shape": {
                        "complexity": "medium",
                        "coupled": True,
                        "decomposable": False,
                        "verification_heavy": False,
                        "novelty_required": False,
                    },
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(state.project_id)
        self.assertIsNotNone(progress)
        decision = progress.get("collaboration_decision")
        self.assertIsNotNone(decision)
        self.assertEqual(decision["architecture"]["mode"], "solo")
        self.assertEqual(decision["architecture"]["candidate_mode"], "joint")
        self.assertEqual(decision["architecture"]["degradation_reason"], "only one eligible model available")

    def test_collaboration_decision_inspector_surfaces_adaptive_explanation(self) -> None:
        state = ProjectState(
            project_id="test-adaptive-explain",
            goal="Evolving architectural challenge",
            current_collaboration_mode=CollaborationMode.PARALLEL,
            current_sync_mode=SyncMode.SOFT,
            artifacts={
                "collaboration_decision": {
                    "contract_version": 1,
                    "model_set": {
                        "policy": "diverse_specialists",
                        "model_refs": ["model_1", "model_2"],
                        "rationale": "Paired models for adaptive supervisor.",
                    },
                    "architecture": {
                        "mode": "adaptive_hybrid",
                        "sync_mode": "soft",
                        "policy_rule": "task.ambiguous_decomposition_to_adaptive",
                        "triggers": ["mixed_signals"],
                        "reasons": ["Ambiguous coupling signals require runtime supervisor."],
                        "requested_by": "auto",
                        "expected_rounds": 4,
                    },
                    "eligible_model_count": 2,
                    "task_shape": {
                        "complexity": "high",
                        "coupled": True,
                        "decomposable": True,
                        "verification_heavy": True,
                        "novelty_required": False,
                    },
                },
                "adaptive-transition:0": {
                    "phase_index": 0,
                    "from_mode": "",
                    "selected_mode": "joint",
                    "selected_sync_mode": "hard",
                    "policy_rule": "initial.joint_alignment",
                    "observed_triggers": ["initial_phase"],
                    "phase_stop_reason": "agreement_reached",
                    "safety_override": False,
                },
                "adaptive-transition:1": {
                    "phase_index": 1,
                    "from_mode": "joint",
                    "selected_mode": "parallel",
                    "selected_sync_mode": "soft",
                    "policy_rule": "transition.parallel_decomposition",
                    "observed_triggers": ["weak_coupling_confirmed"],
                    "phase_stop_reason": "work_units_dispatched",
                    "safety_override": False,
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        progress = self.server.RequestHandlerClass.context.collaboration_progress(state.project_id)
        self.assertIsNotNone(progress)
        self.assertEqual(progress["top_level_architecture"], "adaptive_hybrid")
        self.assertEqual(progress["current_base_mode"], "parallel")
        self.assertEqual(len(progress["adaptive_transitions"]), 2)
        self.assertEqual(progress["adaptive_transitions"][0]["selected_mode"], "joint")
        self.assertEqual(progress["adaptive_transitions"][1]["selected_mode"], "parallel")

    def test_collaboration_decision_inspector_never_recomputes_router_result(self) -> None:
        state = ProjectState(
            project_id="test-immutable-decision",
            goal="Goal with extreme complexity that would otherwise route elsewhere",
            current_collaboration_mode=CollaborationMode.SOLO,
            current_sync_mode=SyncMode.ASYNC,
            artifacts={
                "collaboration_decision": {
                    "contract_version": 1,
                    "model_set": {"policy": "fixed", "model_refs": ["solo_model"], "rationale": "Static"},
                    "architecture": {
                        "mode": "solo",
                        "sync_mode": "async",
                        "policy_rule": "rule.custom_recorded_prior",
                        "triggers": ["custom_trigger"],
                        "reasons": ["Pre-existing recorded rationale."],
                        "requested_by": "auto",
                        "expected_rounds": 1,
                    },
                    "eligible_model_count": 1,
                    "task_shape": {"complexity": "low"},
                },
            },
        )
        with SQLiteStore(self.server.RequestHandlerClass.context.database) as store:
            store.save_state(state)

        # Call endpoint multiple times to ensure state is read-only and never recomputed
        res1 = self.server.RequestHandlerClass.context.collaboration_progress(state.project_id)
        res2 = self.server.RequestHandlerClass.context.collaboration_progress(state.project_id)
        self.assertEqual(res1, res2)
        self.assertEqual(
            res1["collaboration_decision"]["architecture"]["policy_rule"],
            "rule.custom_recorded_prior",
        )


class AutomationShutdownLifecycleTests(unittest.TestCase):
    """B1.3A: graceful backend shutdown joins the automation scheduler."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        database = Path(self.temporary.name) / "state.sqlite3"
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=database,
            credential_store=MemoryCredentialStore(),
        )

    def tearDown(self) -> None:
        if getattr(self.server, "amca_context", None) is not None:
            self.server.server_close()
        self.temporary.cleanup()

    def test_server_close_stops_and_joins_scheduler_worker(self) -> None:
        scheduler = self.server.amca_context.scheduler
        worker = scheduler._thread
        self.assertIsNotNone(worker)
        self.assertTrue(worker.is_alive())
        # Graceful close: the worker is joined, not just signalled.
        self.server.server_close()
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.server.amca_context)
        # A second close is harmless (idempotent teardown).
        self.server.server_close()

    def test_shutdown_endpoint_unblocks_serve_forever(self) -> None:
        import http.client
        import json as json_module

        serve_thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        serve_thread.start()
        port = self.server.server_address[1]
        token = self.server.amca_context.session_token
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            "/api/shutdown",
            headers={"X-AMCA-Session": token, "Content-Length": "0"},
        )
        response = connection.getresponse()
        payload = json_module.loads(response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "shutting-down")
        # serve_forever returns promptly so main() reaches server_close().
        serve_thread.join(timeout=10)
        self.assertFalse(serve_thread.is_alive())

    def test_shutdown_endpoint_requires_session_token(self) -> None:
        import http.client

        serve_thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        serve_thread.start()
        try:
            port = self.server.server_address[1]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("POST", "/api/shutdown", headers={"Content-Length": "0"})
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 401)
            serve_thread.join(timeout=2)
            self.assertTrue(serve_thread.is_alive())  # unauthorized call must not stop service
        finally:
            self.server.shutdown()

    def test_pyinstaller_spec_collects_amca_submodules_and_configures_sys_path(self) -> None:
        software_root = Path(__file__).resolve().parents[1]
        spec_path = software_root / "packaging" / "amca-backend.spec"
        self.assertTrue(spec_path.exists(), "amca-backend.spec must exist")
        content = spec_path.read_text(encoding="utf-8")
        self.assertIn("collect_submodules(\"amca\")", content)
        self.assertIn("sys.path.insert(0, str(software_root / \"src\"))", content)
        self.assertIn("hiddenimports=", content)
        self.assertIn("hidden_amca", content)


class CreateServerDatabaseArgRegressionTests(unittest.TestCase):
    """ZC-RC2A regression: backend must start with database arg None or str."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except OSError:
            pass

    def test_database_none_uses_default_path_for_lifecycle_audit(self) -> None:
        server = create_server(host="127.0.0.1", port=0, root=self.root, database=None,
                               credential_store=MemoryCredentialStore())
        audit = server.lifecycle_audit
        self.assertTrue(audit.path.parent.exists())
        server.server_close()

    def test_database_str_is_resolved_for_lifecycle_audit(self) -> None:
        db = Path(self.temporary.name) / "rc.sqlite3"
        server = create_server(host="127.0.0.1", port=0, root=self.root, database=str(db),
                               credential_store=MemoryCredentialStore())
        audit = server.lifecycle_audit
        self.assertEqual(audit.path.parent, db.resolve().parent)
        server.server_close()
        self.assertTrue(db.parent.exists())


if __name__ == "__main__":

    unittest.main()




class ResearchCitationMapTests(DesktopServiceTests):
    def test_research_context_numbers_citations_in_payload_order(self) -> None:
        """The in-text [amcaN] keys must match the bibliography entry order."""
        import os
        from unittest.mock import patch

        from amca.service import ServiceContext

        context = self.server.RequestHandlerClass.context
        participant = "tabitoken.claude-opus-4-8"
        self.credential_store.set(participant, "citation-map-key")
        results = [
            {"title": "First work", "year": 2026, "doi": "10.1/one", "url": "https://doi.org/10.1/one"},
            {"title": "Second work", "year": 2025, "doi": "", "url": "https://example.org/two"},
        ]
        captured = {}

        def fake_search(project, value):
            captured["query"] = value["query"]
            captured["max_results"] = value["max_results"]
            return {"results": results, "providers": ["crossref"], "errors": [],
                    "evidence_scope": "bibliographic_metadata_and_abstracts_only"}

        with (
            patch.dict(os.environ, {"AMCA_DESKTOP_AUTOMATIC_CLOUD": "1"}),
            patch.object(type(context), "research_search_web", side_effect=fake_search),
            patch.object(type(context), "local_models", return_value={
                "provider": "ollama", "refreshed": True, "models": []}),
            patch.object(type(context.runs), "start_chat",
                         return_value={"run_id": "r", "status": "queued"}),
        ):
            context.start_chat_run(
                goal=("Write a literature review of the latest AI agent evaluation papers. "
                      "Retrieve 5 relevant papers and cite them."),
                participant_ids=[participant], mode="solo",
                selection_policy="manual_model_set",
            )
        # The topic, not the instruction, is searched for; the requested count
        # is honored.
        self.assertEqual(captured["query"], "AI agent evaluation")
        self.assertEqual(captured["max_results"], 5)
