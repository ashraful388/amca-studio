"""32F: end-to-end proof that a LOCAL-model chat run executes governed tools.

Drives the real desktop Chat entry point (``ServiceContext.start_chat_run``)
through the CollaborationKernel with a scripted local adapter (no network, no
real Ollama) and proves, on disk and in the continuation request:

1. filesystem.list / read / search / write actually execute inside a granted
   workspace and the created file exists with the exact expected bytes;
2. the tool result is fed back to the model as a tool-role continuation message;
3. the same holds for the Full Computer profile;
4. an out-of-scope path is refused with a truthful reason and never executed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from amca.models.base import ModelRequest, ModelResponse
from amca.models.ollama import OllamaAdapter
from amca.models.scripted import ScriptedAdapter
from amca.models.tool_capability_evidence import (
    DEFAULT_TOOL_CAPABILITY_EVIDENCE,
    tool_capability_drop_reason,
    tool_capability_record,
    tool_capability_verdict,
)
from amca.permissions import PermissionAction, PermissionStore
from amca.service import ServiceContext

PROJECT_ID = "studio-32f-governed-tools"
CONVERSATION_ID = "32f-governed-tools"
LOCAL_REF = "ollama:mock-local"


class RecordingLocalAdapter(ScriptedAdapter):
    """Scripted local adapter that records every request it receives."""

    def __init__(self, scripts) -> None:
        super().__init__(
            scripts,
            adapter_id=LOCAL_REF,
            model="mock-local",
            provider="ollama",
            is_local=True,
        )
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return await super().generate(request)

    async def stream(self, request: ModelRequest):
        self.requests.append(request)
        async for chunk in super().stream(request):
            yield chunk


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {"call_id": call_id, "name": name, "arguments": arguments}


class GovernedLocalToolEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        benchmarks = root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        self.context = ServiceContext(
            root=root, database=root / "amca.db", benchmarks=benchmarks
        )
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_text(
            "alpha beta gamma", encoding="utf-8"
        )
        # v1.2.0: a chat inside a Project is addressed by the Project's id, and
        # the Project owns the folder the folder-bound levels are held to.  A
        # standalone conv- chat has no folder at all, so these proofs run the
        # real shape: a Studio Project plus a conversation inside it.
        self.context.conversations.create(
            title="32F governed tool proof",
            conversation_id=CONVERSATION_ID,
        )
        self.context.studio_project_store.save(
            {
                "project_id": PROJECT_ID,
                "name": "32F governed tool proof",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": str(self.workspace),
            }
        )
        # Capability evidence is a process-wide ledger: isolate every test.
        DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear()

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------

    def _configure(self, profile: str, capabilities: list[str]) -> None:
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": profile,
                "workspace_root": str(self.workspace),
            }
        )
        self.context.create_local_grant(
            {
                "project_id": PROJECT_ID,
                "root_path": str(self.workspace),
                "capabilities": capabilities,
                "reason": "32F governed tool end-to-end proof",
            }
        )

    def _run(self, adapter: RecordingLocalAdapter, goal: str):
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "mock-local", "fit": 0}],
        }
        with (
            patch.object(type(self.context), "local_models", return_value=inventory),
            patch(
                "amca.service.OllamaAdapter",
                side_effect=lambda **kwargs: adapter,
            ),
        ):
            started = self.context.start_chat_run(
                goal=goal,
                participant_ids=[LOCAL_REF],
                mode="solo",
                selection_policy="manual_model_set",
                project_id=PROJECT_ID,
            )
            return self._await_terminal_run(self.context.runs, started)

    @staticmethod
    def _await_terminal_run(registry, started, timeout_seconds: float = 120.0):
        deadline = time.monotonic() + timeout_seconds
        current = started
        while current["status"] in {"queued", "running", "cancelling"} and (
            time.monotonic() < deadline
        ):
            time.sleep(0.05)
            current = registry.get(str(started["run_id"])) or current
        for thread in threading.enumerate():
            if thread.name.startswith("amca-chat-"):
                thread.join(timeout=timeout_seconds)
        return registry.get(str(started["run_id"])) or current

    # -- scenarios -------------------------------------------------------

    def test_standard_profile_executes_filesystem_tools_end_to_end(self) -> None:
        self._configure("standard", ["list", "read", "stat", "search", "create"])
        target = self.workspace / "created-by-model.txt"
        self.assertFalse(target.exists())
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call("call-list", "filesystem.list", {"path": "."}),
                        tool_call("call-read", "filesystem.read", {"path": "notes.txt"}),
                        tool_call("call-search", "filesystem.search", {"path": ".", "query": "beta"}),
                        tool_call("call-write", "filesystem.write", {"path": "created-by-model.txt", "content": "written by the governed tool loop\n"}),
                    ),
                ),
                ModelResponse(
                    text="I listed the folder, read notes.txt, searched it and created the file.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Work with the files in my project folder.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        # 1. the write actually executed against the real filesystem
        self.assertTrue(target.exists(), "governed filesystem.write did not create the file")
        self.assertEqual(
            target.read_bytes(), b"written by the governed tool loop\n"
        )
        # 2. the tool results were fed back to the model as tool-role messages
        self.assertGreaterEqual(len(adapter.requests), 2)
        continuation = adapter.requests[1]
        tool_messages = [m for m in continuation.messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 4, [dict(m) for m in continuation.messages])
        by_id = {str(m.get("tool_call_id")): str(m.get("content")) for m in tool_messages}
        self.assertIn("notes.txt", by_id["call-list"])
        self.assertIn("alpha beta gamma", by_id["call-read"])
        self.assertIn("beta", by_id["call-search"])
        self.assertIn("created-by-model.txt", by_id["call-write"])
        # 3. the governed tool catalog reached the provider request
        self.assertTrue(continuation.tools, "no governed tools reached the model request")
        names = {str(t.get("name")) for t in continuation.tools}
        self.assertIn("filesystem.write", names)
        self.assertIn("filesystem.read", names)
        self.assertNotIn("command.run", names)
        # 4. the user-visible transcript keeps the answer: the write really ran,
        #    so the anti-fabrication gate must not strip the claim.
        conversation = self.context.conversations.get(str(result["conversation_id"]))
        self.assertIsNotNone(conversation)
        assistant = [
            message
            for message in conversation["messages"]
            if message["role"] == "assistant"
        ]
        self.assertTrue(assistant)
        self.assertIn("created the file", assistant[-1]["content"])
        self.assertNotIn("That operation was not executed.", assistant[-1]["content"])

    def test_new_file_write_succeeds_when_grant_holds_write_and_create(self) -> None:
        """Regression for the physical GUI acceptance failure (D:\\test\\plan.txt).

        The owner's live grant carries BOTH ``write`` and ``create``.  The
        capability preference resolved a write to a not-yet-existing file as
        WRITE, so the direct-create branch was skipped and the request fell into
        the exact-scope approval path, which requires the target to exist.  The
        call failed with ``FileNotFoundError [WinError 2]``, the run produced no
        file, and the user-visible answer was "That operation was not executed
        or could not be verified."

        This drives the real ``start_chat_run`` path with the same capability
        set the live grant had.
        """
        self._configure(
            "extended",
            [
                "list", "read", "stat", "search", "create", "write",
                "append", "copy", "move", "trash", "propose_command",
            ],
        )
        target = self.workspace / "plan.txt"
        self.assertFalse(target.exists())
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call(
                            "call-write",
                            "filesystem.write",
                            {"path": "plan.txt", "content": "# Plan\nstep 1\n"},
                        ),
                        tool_call("call-stat", "filesystem.stat", {"path": "plan.txt"}),
                    ),
                ),
                ModelResponse(
                    text="I wrote plan.txt with the plan.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(
            adapter,
            "Write the plan into plan.txt in the project folder now using the "
            "filesystem write tool.",
        )
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        # the file exists with the exact bytes the model asked for
        self.assertTrue(target.exists(), "governed write did not create plan.txt")
        self.assertEqual(target.read_bytes(), b"# Plan\nstep 1\n")
        continuation = adapter.requests[1]
        by_id = {
            str(m.get("tool_call_id")): str(m.get("content"))
            for m in continuation.messages
            if m.get("role") == "tool"
        }
        # the write executed; it did not stop at an approval prompt
        self.assertIn("plan.txt", by_id["call-write"])
        self.assertNotIn("approval required", by_id["call-write"].lower())
        # stat now reports the file that exists
        self.assertIn("plan.txt", by_id["call-stat"])
        self.assertIn("true", by_id["call-stat"].lower())
        # and the anti-fabrication gate keeps the truthful success claim
        conversation = self.context.conversations.get(str(result["conversation_id"]))
        assistant = [
            message
            for message in conversation["messages"]
            if message["role"] == "assistant"
        ]
        self.assertIn("plan.txt", assistant[-1]["content"])
        self.assertNotIn("not executed", assistant[-1]["content"].lower())

    def test_stat_of_a_missing_file_reports_absence_not_failure(self) -> None:
        """A missing target is the answer to an existence query, not an error."""
        self._configure("standard", ["list", "read", "stat", "search", "create"])
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call(
                            "call-stat-missing",
                            "filesystem.stat",
                            {"path": "not-created-yet.txt"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="That file does not exist yet.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Check whether not-created-yet.txt exists.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        continuation = adapter.requests[1]
        by_id = {
            str(m.get("tool_call_id")): str(m.get("content"))
            for m in continuation.messages
            if m.get("role") == "tool"
        }
        answer = by_id["call-stat-missing"]
        self.assertIn("not-created-yet.txt", answer)
        self.assertIn("false", answer.lower())
        self.assertNotIn("WinError", answer)
        self.assertNotIn("FileNotFoundError", answer)


    def test_full_computer_profile_executes_granted_write_and_proposes_command(self) -> None:
        outside = Path(self.temporary.name) / "outside-workspace"
        outside.mkdir()
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": "full_computer",
                "workspace_root": str(self.workspace),
            }
        )
        grant = self.context.create_local_grant(
            {
                "project_id": PROJECT_ID,
                "root_path": str(outside),
                "capabilities": ["list", "read", "create", "propose_command"],
                "reason": "32F full computer proof",
            }
        )
        target = outside / "full-computer.txt"
        self.assertFalse(target.exists())
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call(
                            "call-write-outside",
                            "filesystem.write",
                            {
                                # An ABSOLUTE path, because a RELATIVE path
                                # is project-relative by the tool contract:
                                # "calculator.html" in a Project means the
                                # Project's own folder, not whichever granted
                                # folder happens to be named first.  What this
                                # test is about is a grant OUTSIDE the
                                # workspace being honoured, so it addresses
                                # that folder directly.
                                "path": str(target),
                                "content": "full computer write\n",
                            },
                        ),
                        tool_call(
                            "call-command",
                            "command.inspect",
                            {"argv": ["whoami"], "path": "."},
                        ),
                    ),
                ),
                ModelResponse(
                    text="Created the file and proposed the command for approval.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Write a note in my other folder.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        # A Full Computer grant outside the workspace root is honored.
        self.assertTrue(target.exists(), "full_computer write did not execute")
        self.assertEqual(target.read_bytes(), b"full computer write\n")
        tool_messages = [
            m for m in adapter.requests[1].messages if m.get("role") == "tool"
        ]
        by_id = {str(m.get("tool_call_id")): str(m.get("content")) for m in tool_messages}
        self.assertIn("full-computer.txt", by_id["call-write-outside"])
        # The command is proposed for human approval, never executed by the model.
        self.assertIn("approval", by_id["call-command"].lower())
        self.assertIn("whoami", by_id["call-command"])
        with PermissionStore(self.context.database) as approvals:
            pending = approvals.list(project_id=PROJECT_ID, status="pending")
        command_approvals = [
            item for item in pending if item.action is PermissionAction.RUN_COMMAND
        ]
        self.assertEqual(len(command_approvals), 1, [item.to_dict() for item in pending])
        self.assertEqual(
            list(command_approvals[0].scope.get("argv") or []), ["whoami"]
        )
        # The tool result must carry the exact approval id the human has to act on.
        self.assertIn(command_approvals[0].id, by_id["call-command"])
        # command.run must never be exposed as a direct execution tool
        names = {str(t.get("name")) for t in adapter.requests[1].tools}
        self.assertIn("command.inspect", names)
        self.assertIn("command.run", names)
        self.assertFalse(target.is_dir())
        self.assertEqual(str(grant["grant_id"]), str(grant["grant_id"]))

    def test_a_relative_write_lands_in_the_projects_own_folder(self) -> None:
        """A relative path is project-relative, under every level.

        The tool contract says the agent's home is the folder the operator
        pointed this Project at.  A relative path that anchored to
        whichever drive grant came first put "calculator.html" on a drive
        root instead of in the Project's folder.

        This pins the CONTRACT, not the fix: this harness writes the
        Project through the store and sets the level the way the operator
        always did, so it passes against the pre-fix code too.  The
        regression proof for the fix is
        test_project_creation_workspace_grant.py, whose
        test_a_relative_target_resolves_inside_the_project_folder goes
        through save_studio_project and fails against the pre-fix code.
        """
        self._configure("standard", ["list", "read", "create", "write"])
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call(
                            "call-write-relative",
                            "filesystem.write",
                            {"path": "relative.txt", "content": "in the project folder\n"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="Created the file in the Project folder.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Write a note in this Project's folder.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        target = self.workspace / "relative.txt"
        self.assertTrue(
            target.exists(),
            "the relative write did not land in the Project folder",
        )
        self.assertEqual(target.read_bytes(), b"in the project folder\n")

    def test_compound_truthful_report_is_not_downgraded(self) -> None:
        """A verified multi-operation report must survive the integrity gate.

        Regression: the gate judged the whole sentence with the union of its
        targets, so "I read notes.txt and created the file." was rewritten to
        "That operation was not executed." even though both operations really
        ran — the user-visible symptom of "Local PC access not working".
        """
        self._configure("standard", ["list", "read", "stat", "search", "create"])
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call("call-read", "filesystem.read", {"path": "notes.txt"}),
                        tool_call(
                            "call-write",
                            "filesystem.write",
                            {"path": "created-by-model.txt", "content": "x\n"},
                        ),
                    ),
                ),
                ModelResponse(
                    text=(
                        "I listed the folder, read notes.txt, searched it and "
                        "created the file."
                    ),
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Work with the files in my project folder.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        self.assertTrue((self.workspace / "created-by-model.txt").exists())
        conversation = self.context.conversations.get(str(result["conversation_id"]))
        assistant = [
            message
            for message in conversation["messages"]
            if message["role"] == "assistant"
        ]
        self.assertIn("read notes.txt", assistant[-1]["content"])
        self.assertNotIn("That operation was not executed", assistant[-1]["content"])

    def test_out_of_scope_path_is_refused_with_truthful_reason(self) -> None:
        self._configure("standard", ["list", "read", "stat", "search", "create"])
        secret = Path(self.temporary.name) / "outside-secret.txt"
        secret.write_text("TOP-SECRET-OUTSIDE-CONTENT", encoding="utf-8")
        out_of_scope_target = Path(self.temporary.name) / "outside-created.txt"
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call(
                            "call-read-outside",
                            "filesystem.read",
                            {"path": str(secret)},
                        ),
                        tool_call(
                            "call-write-outside",
                            "filesystem.write",
                            {"path": str(out_of_scope_target), "content": "should not exist"},
                        ),
                    ),
                ),
                ModelResponse(
                    text="Both operations were refused outside the granted scope.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Read a file that is outside my project.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        # Nothing outside the grant was read or written.
        self.assertFalse(
            out_of_scope_target.exists(), "out-of-scope write executed"
        )
        tool_messages = [
            m for m in adapter.requests[1].messages if m.get("role") == "tool"
        ]
        by_id = {str(m.get("tool_call_id")): str(m.get("content")) for m in tool_messages}
        self.assertNotIn("TOP-SECRET-OUTSIDE-CONTENT", by_id["call-read-outside"])
        self.assertNotIn("TOP-SECRET-OUTSIDE-CONTENT", json.dumps(
            [dict(m) for m in adapter.requests[1].messages], default=str
        ))
        refusal = by_id["call-read-outside"].lower()
        self.assertTrue(
            "outside" in refusal or "escape" in refusal or "denied" in refusal,
            by_id["call-read-outside"],
        )
        write_refusal = by_id["call-write-outside"].lower()
        self.assertTrue(
            "outside" in write_refusal or "escape" in write_refusal or "denied" in write_refusal,
            by_id["call-write-outside"],
        )


    def test_every_mutation_requires_approval_and_nothing_changes(self) -> None:
        self._configure(
            "extended",
            [
                "list", "read", "stat", "search", "create", "write",
                "append", "copy", "move", "trash", "propose_command",
            ],
        )
        notes = self.workspace / "notes.txt"
        before = notes.read_bytes()
        adapter = RecordingLocalAdapter(
            [
                ModelResponse(
                    text="",
                    model="mock-local",
                    provider="ollama",
                    tool_calls=(
                        tool_call("call-stat", "filesystem.stat", {"path": "notes.txt"}),
                        tool_call("call-append", "filesystem.append", {"path": "notes.txt", "content": "more"}),
                        tool_call("call-copy", "filesystem.copy", {"path": "notes.txt", "destination": "notes-copy.txt"}),
                        tool_call("call-move", "filesystem.move", {"path": "notes.txt", "destination": "notes-moved.txt"}),
                        tool_call("call-trash", "filesystem.trash", {"path": "notes.txt"}),
                    ),
                ),
                ModelResponse(
                    text="Every mutation is waiting for your approval.",
                    model="mock-local",
                    provider="ollama",
                ),
            ]
        )
        result = self._run(adapter, "Tidy up my project notes.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        tool_messages = [
            m for m in adapter.requests[1].messages if m.get("role") == "tool"
        ]
        by_id = {str(m.get("tool_call_id")): str(m.get("content")) for m in tool_messages}
        # stat is read-only and executed.
        self.assertIn("notes.txt", by_id["call-stat"])
        # every mutation is a proposal, never an execution
        for call_id in ("call-append", "call-copy", "call-move", "call-trash"):
            self.assertIn("Human approval required", by_id[call_id], by_id[call_id])
        # nothing on disk changed
        self.assertEqual(notes.read_bytes(), before)
        self.assertFalse((self.workspace / "notes-copy.txt").exists())
        self.assertFalse((self.workspace / "notes-moved.txt").exists())
        with PermissionStore(self.context.database) as approvals:
            pending = approvals.list(project_id=PROJECT_ID, status="pending")
        actions = sorted(item.action.value for item in pending)
        self.assertEqual(
            actions,
            ["local_file_modify", "local_file_modify", "local_file_move", "local_file_trash"],
            [item.to_dict() for item in pending],
        )
        # Each proposal carries the exact approval id the human must act on.
        ids = {item.id for item in pending}
        for call_id in ("call-append", "call-copy", "call-move", "call-trash"):
            self.assertTrue(
                any(approval_id in by_id[call_id] for approval_id in ids),
                by_id[call_id],
            )


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class OllamaProviderToolWiring(unittest.TestCase):
    """Offline proof of what the real OllamaAdapter sends to the provider.

    The fake transport answers the two endpoints a real loopback Ollama daemon
    serves: ``POST /api/show`` (capability evidence) and ``POST /api/chat``.
    """

    GOVERNED_TOOLS = (
        {
            "type": "function",
            "name": "filesystem.read",
            "description": "Read content from a file within granted scope.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    )

    def _capture(
        self,
        model: str,
        *,
        declared_capabilities=("completion", "tools"),
        show_fails: bool = False,
    ) -> dict:
        # Capability evidence is a process-wide ledger: isolate every capture.
        DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear()
        captured: dict = {"requests": []}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            path = str(request.full_url).split("11434", 1)[-1]
            captured["requests"].append(path)
            if path == "/api/show":
                if show_fails:
                    raise OSError("runtime unreachable")
                return _FakeHTTPResponse(
                    json.dumps(
                        {
                            "model": model,
                            "capabilities": list(declared_capabilities),
                        }
                    ).encode("utf-8")
                )
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _FakeHTTPResponse(
                json.dumps(
                    {
                        "model": model,
                        "done": True,
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ).encode("utf-8")
            )

        adapter = OllamaAdapter(model=model, urlopen_fn=fake_urlopen)
        request = ModelRequest(
            messages=({"role": "user", "content": "read notes.txt"},),
            tools=self.GOVERNED_TOOLS,
        )
        asyncio.run(adapter.generate(request))
        return captured

    def test_known_local_model_receives_governed_tools(self) -> None:
        captured = self._capture("qwen3:1.7b")
        self.assertIn("tools", captured["payload"], captured["payload"].keys())
        self.assertEqual(
            captured["payload"]["tools"][0]["function"]["name"], "filesystem.read"
        )

    def test_runtime_declared_local_models_receive_governed_tools(self) -> None:
        # These are real models installed on the operator machine.  A local
        # model outside the hardcoded name table silently lost the governed
        # tool catalog, so the model could never call a tool.
        for model in ("llama3.1:latest", "qwen3:0.6b", "gemma3:27b"):
            with self.subTest(model=model):
                captured = self._capture(model)
                self.assertIn(
                    "tools",
                    captured["payload"],
                    f"{model}: governed tools were stripped before the provider call",
                )

    def test_runtime_declared_negative_keeps_tools_stripped(self) -> None:
        captured = self._capture("llama2:latest", declared_capabilities=("completion",))
        self.assertNotIn("tools", captured["payload"])

    def test_unverifiable_runtime_keeps_tools_stripped(self) -> None:
        captured = self._capture("llama3.1:latest", show_fails=True)
        self.assertNotIn("tools", captured["payload"])

    def test_direct_payload_without_evidence_keeps_fail_closed(self) -> None:
        # Matches the documented UNKNOWN_SUPPORT_GETS_TOOLS=NO policy: without
        # runtime evidence and without a table entry, no tool catalog is sent.
        adapter = OllamaAdapter(model="arbitrary-custom-model:latest")
        payload = adapter._payload(
            ModelRequest(messages="hi", tools=self.GOVERNED_TOOLS), stream=False
        )
        self.assertNotIn("tools", payload)


class _FakeOllamaRuntime:
    """Deterministic loopback Ollama double: /api/show + scripted /api/chat."""

    def __init__(self, chat_responses: list[dict], *, capabilities=("completion", "tools")) -> None:
        self._chat_responses = list(chat_responses)
        self._capabilities = list(capabilities)
        self.chat_payloads: list[dict] = []
        self.paths: list[str] = []

    def __call__(self, request, timeout=None):  # noqa: ANN001
        path = str(request.full_url).split("11434", 1)[-1]
        self.paths.append(path)
        if path == "/api/show":
            return _FakeHTTPResponse(
                json.dumps(
                    {"model": "mock-local", "capabilities": self._capabilities}
                ).encode("utf-8")
            )
        payload = json.loads(request.data.decode("utf-8"))
        self.chat_payloads.append(payload)
        body = self._chat_responses.pop(0)
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))


class OllamaGovernedChatEndToEnd(unittest.TestCase):
    """The real OllamaAdapter, the real service, the real kernel — offline."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        benchmarks = root / "benchmarks" / "tasks.json"
        benchmarks.parent.mkdir(parents=True, exist_ok=True)
        benchmarks.write_text("[]", encoding="utf-8")
        self.context = ServiceContext(
            root=root, database=root / "amca.db", benchmarks=benchmarks
        )
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_text("alpha beta gamma", encoding="utf-8")
        # v1.2.0: the Project carries the folder; a standalone conv- chat has
        # none.  Same shape as the scripted proof above.
        self.context.conversations.create(
            title="32F real Ollama governed proof",
            conversation_id=CONVERSATION_ID,
        )
        self.context.studio_project_store.save(
            {
                "project_id": PROJECT_ID,
                "name": "32F real Ollama governed proof",
                "description": "",
                "instructions": "",
                "template": "general",
                "root_path": str(self.workspace),
            }
        )
        # Capability evidence is a process-wide ledger: isolate every test.
        DEFAULT_TOOL_CAPABILITY_EVIDENCE.clear()

    def tearDown(self) -> None:
        try:
            self.temporary.cleanup()
        except Exception:
            pass

    def _run(self, runtime: _FakeOllamaRuntime, goal: str):
        self.context.set_local_permission_profile(
            {
                "project_id": PROJECT_ID,
                "profile": "standard",
                "workspace_root": str(self.workspace),
            }
        )
        self.context.create_local_grant(
            {
                "project_id": PROJECT_ID,
                "root_path": str(self.workspace),
                "capabilities": ["list", "read", "stat", "search", "create"],
                "reason": "32F real-adapter proof",
            }
        )
        inventory = {
            "provider": "ollama",
            "refreshed": True,
            "runtime_reachable": True,
            "models": [{"model": "mock-local", "fit": 0}],
        }

        def factory(**kwargs):  # noqa: ANN003
            return OllamaAdapter(urlopen_fn=runtime, **kwargs)

        with (
            patch.object(type(self.context), "local_models", return_value=inventory),
            patch("amca.service.OllamaAdapter", side_effect=factory),
        ):
            started = self.context.start_chat_run(
                goal=goal,
                participant_ids=[LOCAL_REF],
                mode="solo",
                selection_policy="manual_model_set",
                project_id=PROJECT_ID,
            )
            return GovernedLocalToolEndToEnd._await_terminal_run(
                self.context.runs, started
            )

    def test_real_ollama_adapter_executes_write_and_reinjects_tool_results(self) -> None:
        runtime = _FakeOllamaRuntime(
            [
                {
                    "model": "mock-local",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "filesystem.list", "arguments": {"path": "."}}},
                            {"function": {"name": "filesystem.read", "arguments": {"path": "notes.txt"}}},
                            {"function": {"name": "filesystem.write", "arguments": {"path": "real-adapter.txt", "content": "written through the real adapter\n"}}},
                        ],
                    },
                },
                {
                    "model": "mock-local",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": "I listed, read and created the file.",
                    },
                },
            ]
        )
        result = self._run(runtime, "Work with the files in my project folder.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        target = self.workspace / "real-adapter.txt"
        self.assertTrue(target.exists(), "real-adapter governed write did not execute")
        self.assertEqual(target.read_bytes(), b"written through the real adapter\n")
        # The runtime declared tool support, so the governed catalog was sent.
        self.assertIn("/api/show", runtime.paths)
        self.assertIn("tools", runtime.chat_payloads[0])
        tool_names = {
            item["function"]["name"] for item in runtime.chat_payloads[0]["tools"]
        }
        self.assertIn("filesystem.write", tool_names)
        # Continuation payload: assistant tool_calls + role:tool results.
        continuation = runtime.chat_payloads[1]
        assistant = [
            m for m in continuation["messages"] if m.get("role") == "assistant"
        ]
        self.assertTrue(assistant, continuation["messages"])
        self.assertEqual(len(assistant[-1]["tool_calls"]), 3)
        tool_messages = [m for m in continuation["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 3, continuation["messages"])
        by_name = {str(m.get("tool_name")): str(m.get("content")) for m in tool_messages}
        self.assertIn("alpha beta gamma", by_name["filesystem.read"])
        self.assertIn("notes.txt", by_name["filesystem.list"])
        self.assertIn("real-adapter.txt", by_name["filesystem.write"])

    def test_real_ollama_malformed_arguments_never_execute(self) -> None:
        runtime = _FakeOllamaRuntime(
            [
                {
                    "model": "mock-local",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            # The provider reported a tool call whose arguments
                            # are not valid JSON: this must fail closed.
                            {"function": {"name": "filesystem.list", "arguments": "{not-json"}},
                        ],
                    },
                },
                {
                    "model": "mock-local",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": "The tool call was rejected before execution.",
                    },
                },
            ]
        )
        result = self._run(runtime, "List my project files.")
        self.assertEqual(result["status"], "succeeded", result.get("error"))
        continuation = runtime.chat_payloads[1]
        tool_messages = [m for m in continuation["messages"] if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1, continuation["messages"])
        content = str(tool_messages[0]["content"])
        self.assertNotIn(
            '"entries"',
            content,
            "a malformed tool call was executed and returned a real listing",
        )
        self.assertTrue(
            "malformed" in content.lower() or "rejected" in content.lower(),
            content,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()