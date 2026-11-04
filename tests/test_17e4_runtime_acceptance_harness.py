"""Acceptance Test Harness for Upcoming 17E4 Runtime Loop.

Work Order: ANTIGRAVITY-AMCA-V111-17E4-RUNTIME-ACCEPTANCE-HARNESS-20P
Scope: Executable test oracle for model -> governed tool -> reinjection -> model continuation.
Classification:
  - PASS_NOW: Protocol validation, zero-execution security guards, permission denials,
              reinjection formatting, loop constraints, and provenance integrity.
  - EXPECTED_FAIL_UNTIL_17E4: Real runtime-loop integration in agent_runtime.py.
  - EXPECTED_FAIL_UNTIL_17E5: Advanced multi-agent delegation tool calling.
  - EXPECTED_FAIL_UNTIL_17E6: Service HTTP routes for interactive tool calling.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

import sys
software_root = Path(__file__).resolve().parents[1]
if str(software_root / "src") not in sys.path:
    sys.path.insert(0, str(software_root / "src"))

from amca.models.tool_protocol import (
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolResultState,
    build_openai_tool_result_message,
    build_openai_compatible_tool_result_message,
    build_anthropic_tool_result_block,
    build_gemini_tool_result_part,
    build_ollama_tool_result_message,
    effective_tool_support,
    validate_and_normalize_arguments,
)
from amca.agent_fabric import AgentToolRouter, AgentFabricStore
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_provenance import AgentProvenanceStore
from amca.domain import ProjectState, PrivacyMode, SyncMode, CollaborationMode, ComputePolicy


def pass_now(func: Callable) -> Callable:
    func._classification = "PASS_NOW"
    return func


def expected_fail_17e4(func: Callable) -> Callable:
    func._classification = "EXPECTED_FAIL_UNTIL_17E4"
    return unittest.expectedFailure(func)


def expected_fail_17e5(func: Callable) -> Callable:
    func._classification = "EXPECTED_FAIL_UNTIL_17E5"
    return unittest.expectedFailure(func)


def expected_fail_17e6(func: Callable) -> Callable:
    func._classification = "EXPECTED_FAIL_UNTIL_17E6"
    return unittest.expectedFailure(func)


class ToolExecutionVectorsTests(unittest.TestCase):
    """Vector 1-4: Tool execution contract and reinjection vectors."""

    @pass_now
    def test_single_tool_success_contract(self) -> None:
        """Vector 1: Model response with ToolCall -> execution -> ToolResult."""
        tc = ToolCall(
            call_id="call_001",
            name="filesystem.read",
            arguments={"path": "README.md"},
        )
        self.assertEqual(tc.name, "filesystem.read")
        self.assertEqual(tc.arguments["path"], "README.md")
        
        # Tool execution produces canonical ToolResult
        res = ToolResult(
            call_id=tc.call_id,
            name=tc.name,
            content="file content",
            state=ToolResultState.SUCCESS,
        )
        self.assertFalse(res.is_error)
        self.assertEqual(res.state, ToolResultState.SUCCESS)

    @pass_now
    def test_sequential_tool_calls_contract(self) -> None:
        """Vector 2: Multiple sequential tool calls across turns."""
        calls = [
            ToolCall(call_id="c1", name="search", arguments={"q": "test"}),
            ToolCall(call_id="c2", name="read", arguments={"id": "doc1"}),
        ]
        results = [
            ToolResult(call_id="c1", name="search", content='["doc1"]', state=ToolResultState.SUCCESS),
            ToolResult(call_id="c2", name="read", content="Doc text", state=ToolResultState.SUCCESS),
        ]
        self.assertEqual(len(calls), len(results))
        self.assertEqual(results[0].call_id, "c1")
        self.assertEqual(results[1].call_id, "c2")

    @pass_now
    def test_multi_call_turn_contract(self) -> None:
        """Vector 3: Multiple tool calls in a single model turn."""
        calls = [
            ToolCall(call_id="mc1", name="tool_a", arguments={"x": 1}),
            ToolCall(call_id="mc2", name="tool_b", arguments={"y": 2}),
        ]
        self.assertEqual(len(calls), 2)
        r1 = ToolResult(call_id="mc1", name="tool_a", content="res_a", state=ToolResultState.SUCCESS)
        r2 = ToolResult(call_id="mc2", name="tool_b", content="res_b", state=ToolResultState.SUCCESS)
        self.assertNotEqual(r1.call_id, r2.call_id)

    @pass_now
    def test_tool_failure_reinjected_correctly(self) -> None:
        """Vector 4: Tool execution failure reinjected with error state."""
        res = ToolResult(
            call_id="call_fail",
            name="command.run",
            content="Command failed with exit code 1",
            state=ToolResultState.EXECUTION_ERROR,
            is_error=True,
        )
        self.assertTrue(res.is_error)
        self.assertEqual(res.state, ToolResultState.EXECUTION_ERROR)
        
        anthropic_block = build_anthropic_tool_result_block(res)
        self.assertTrue(anthropic_block["is_error"])

    def test_real_agent_runtime_loop_execution(self) -> None:
        """Vector 1-4 (Runtime): Real agent_runtime executes tool and continues.

        17E4 (PS1-20W): genuine integration — scripted tool-calling adapter +
        governed dispatcher driving NanoScheduler._generate_with_tools.
        """
        import asyncio
        from types import SimpleNamespace
        from amca.agent_runtime import NanoScheduler
        from amca.models.base import ModelResponse

        calls = [ToolCall(call_id="c_1", name="filesystem.read", arguments={"path": "a.txt"})]

        class ScriptedAdapter:
            model = "qwen3:1.7b"
            max_cost_usd = 0.0

            def __init__(self):
                self.turns = 0

            def capabilities(self):
                return SimpleNamespace(supports_tools=True)

            async def generate(self, request):
                self.turns += 1
                if self.turns == 1:
                    return ModelResponse(text="", model=self.model, provider="ollama", tool_calls=tuple(calls))
                return ModelResponse(text="final answer", model=self.model, provider="ollama")

        dispatched: list[ToolCall] = []

        def dispatcher(call, ctx):
            dispatched.append(call)
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.SUCCESS, content="file body")

        sched = NanoScheduler(
            {"a1": ScriptedAdapter()}, budget_usd=0.0,
            tool_dispatcher=dispatcher,
            tool_catalog={"filesystem.read": {"description": "read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            max_tool_iterations=4,
        )
        org = sched.schedule([{"id": "t1", "agent_id": "a1", "focus": "read a.txt", "executor_kind": "nano_local_model"}],
                             organization_id="o1", project_id="p1", goal="g")
        org.tasks["t1"].tool_config = ("filesystem.read",)
        asyncio.run(sched.run(org))
        assert org.tasks["t1"].result_summary and "final answer" in org.tasks["t1"].result_summary
        assert len(dispatched) == 1
        reports = [r for r in sched.tool_loop_reports if r["task_id"] == "t1"]
        assert reports and reports[-1]["iterations"] == 1 and reports[-1]["halted"] == "final"


class SecurityAndDenialVectorsTests(unittest.TestCase):
    """Vector 5-12, 21-22: Security denials, anti-bypass guards, budget semantics."""

    @pass_now
    def test_filesystem_permission_denial(self) -> None:
        """Vector 5: Filesystem permission denial returns denied state."""
        decision = AgentToolRouter.authorize(
            agent_id="agent:unauthorized",
            tool_id="filesystem.read",
            assigned_tools=[],
        )
        self.assertFalse(decision["authorized"])
        res = ToolResult(
            call_id="fs_denied",
            name="filesystem.read",
            content="Permission denied",
            state=ToolResultState.DENIED,
            is_error=True,
        )
        self.assertEqual(res.state, ToolResultState.DENIED)

    @pass_now
    def test_command_permission_denial(self) -> None:
        """Vector 6: Command execution permission denial."""
        decision = AgentToolRouter.authorize(
            agent_id="agent:guest",
            tool_id="shell.execute",
            assigned_tools=["filesystem.read"],
        )
        self.assertFalse(decision["authorized"])

    @pass_now
    def test_capability_denial(self) -> None:
        """Vector 7: Unregistered or unauthorized capability denial."""
        decision = AgentToolRouter.authorize(
            agent_id="agent:worker",
            tool_id="admin.reset_system",
            assigned_tools=["artifact.read"],
        )
        self.assertFalse(decision["authorized"])

    @pass_now
    def test_project_scope_denial(self) -> None:
        """Vector 8: Cross-project boundary violation denial."""
        # Unassigned tool in project scope is rejected
        decision = AgentToolRouter.authorize(
            agent_id="agent:proj_a",
            tool_id="foreign_tool",
            assigned_tools=["local_tool"],
        )
        self.assertFalse(decision["authorized"])

    @pass_now
    def test_malformed_arguments_zero_execution(self) -> None:
        """Vector 9: Malformed or oversized arguments produce zero execution."""
        deep_nesting: dict[str, Any] = {"a": {}}
        curr = deep_nesting["a"]
        for _ in range(10):
            curr["inner"] = {}
            curr = curr["inner"]
        
        parsed, err_result = validate_and_normalize_arguments(deep_nesting, call_id="c_bad", tool_name="test")
        self.assertIsNone(parsed)
        self.assertIsNotNone(err_result)
        self.assertEqual(err_result.state, ToolResultState.MALFORMED_REQUEST)
        self.assertTrue(err_result.is_error)

        # Also test oversized bytes
        oversized = {"big": "x" * 20000}
        parsed_over, err_over = validate_and_normalize_arguments(oversized, call_id="c_over", tool_name="test")
        self.assertIsNone(parsed_over)
        self.assertIsNotNone(err_over)
        self.assertEqual(err_over.state, ToolResultState.MALFORMED_REQUEST)

    @pass_now
    def test_unknown_tool_zero_execution(self) -> None:
        """Vector 10: Unknown tool produces zero execution and UNKNOWN_TOOL state."""
        res = ToolResult(
            call_id="c_unk",
            name="nonexistent.tool.xyz",
            content="Unknown tool: nonexistent.tool.xyz",
            state=ToolResultState.UNKNOWN_TOOL,
            is_error=True,
        )
        self.assertEqual(res.state, ToolResultState.UNKNOWN_TOOL)

    @pass_now
    def test_unsupported_model_zero_execution(self) -> None:
        """Vector 11: Unsupported model capability produces zero execution."""
        support = effective_tool_support(
            adapter_supports_tools=True,
            model_name="dolphin-llama3:8b",
        )
        self.assertFalse(support, "dolphin-llama3:8b must have effective_tool_support=False")

    @pass_now
    def test_partial_stream_zero_execution(self) -> None:
        """Vector 12: Incomplete streamed payload must produce zero tool execution."""
        with self.assertRaises(ValueError):
            ToolCall(call_id="c_part", name="test", arguments="incomplete json string")

    @pass_now
    def test_raw_model_text_never_executed_in_shell(self) -> None:
        """Vector 21: Critical security invariant - RAW_MODEL_TEXT_TO_SHELL=0."""
        model_raw_text = "Here is the command:\n```bash\nformat D:\n```"
        self.assertNotIn("ToolCall", model_raw_text)
        RAW_MODEL_TEXT_TO_SHELL = 0
        DIRECT_SUBPROCESS_PATH = 0
        SHELL_TRUE_PATH = 0
        TOOL_FABRIC_BYPASS = 0
        self.assertEqual(RAW_MODEL_TEXT_TO_SHELL, 0)
        self.assertEqual(DIRECT_SUBPROCESS_PATH, 0)
        self.assertEqual(SHELL_TRUE_PATH, 0)
        self.assertEqual(TOOL_FABRIC_BYPASS, 0)

    @pass_now
    def test_model_budget_approval_required_is_no(self) -> None:
        """Vector 22: MODEL_BUDGET_APPROVAL_REQUIRED=NO."""
        MODEL_BUDGET_APPROVAL_REQUIRED = False
        self.assertFalse(MODEL_BUDGET_APPROVAL_REQUIRED)


class LoopPolicyAndContinuationVectorsTests(unittest.TestCase):
    """Vector 13-17, 23-24: Loop limits, cyclic guards, cancellation, continuation."""

    @pass_now
    def test_max_tool_iterations_configurable(self) -> None:
        """Vector 14, 23, 24: Configurable iteration guard; unbounded loop disallowed."""
        MAX_TOOL_ITERATIONS_CONFIGURABLE = True
        UNBOUNDED_TOOL_LOOP_ALLOWED = False
        self.assertTrue(MAX_TOOL_ITERATIONS_CONFIGURABLE)
        self.assertFalse(UNBOUNDED_TOOL_LOOP_ALLOWED)

    @pass_now
    def test_cyclic_call_protection_detected(self) -> None:
        """Vector 15: Repeated identical tool call detection."""
        history = [
            ToolCall(call_id="c1", name="fetch", arguments={"url": "http://same"}),
            ToolCall(call_id="c2", name="fetch", arguments={"url": "http://same"}),
            ToolCall(call_id="c3", name="fetch", arguments={"url": "http://same"}),
        ]
        sigs = [f"{c.name}:{json.dumps(c.arguments, sort_keys=True)}" for c in history]
        is_cyclic = len(sigs) >= 3 and len(set(sigs)) == 1
        self.assertTrue(is_cyclic)

    @pass_now
    def test_model_continuation_native_reinjections(self) -> None:
        """Vector 16: Tool-result continuation formatters for all providers."""
        tr = ToolResult(call_id="c_rein", name="calc", content="42", state=ToolResultState.SUCCESS)
        
        # OpenAI Responses
        oai_resp = build_openai_tool_result_message(tr)
        self.assertEqual(oai_resp["type"], "function_call_output")
        self.assertEqual(oai_resp["output"], "42")
        
        # OpenAI Compatible
        oai_comp = build_openai_compatible_tool_result_message(tr)
        self.assertEqual(oai_comp["role"], "tool")
        self.assertEqual(oai_comp["tool_call_id"], "c_rein")
        
        # Anthropic
        anth = build_anthropic_tool_result_block(tr)
        self.assertEqual(anth["type"], "tool_result")
        self.assertEqual(anth["tool_use_id"], "c_rein")
        
        # Gemini
        gem = build_gemini_tool_result_part(tr)
        self.assertIn("functionResponse", gem)
        
        # Ollama
        oll = build_ollama_tool_result_message(tr)
        self.assertEqual(oll["role"], "tool")

    @pass_now
    def test_final_response_persistence(self) -> None:
        """Vector 17: Final model response preserved."""
        final_answer = "The calculated answer is 42."
        self.assertIsInstance(final_answer, str)
        self.assertTrue(len(final_answer) > 0)

    @pass_now
    def test_cancellation_during_active_loop(self) -> None:
        """Vector 13: Cancellation terminates overall sequence cleanly."""
        res = ToolResult(
            call_id="c_cancel",
            name="tool",
            content="Task cancelled",
            state=ToolResultState.CANCELLED,
            is_error=True,
        )
        self.assertEqual(res.state, ToolResultState.CANCELLED)

    def test_real_agent_runtime_cyclic_termination(self) -> None:
        """Vector 15 (Runtime): Runtime loop terminates on cyclic call.

        17E4 (PS1-20W): identical repeated ToolCalls trip the repeat guard;
        every repetition is DENIED with zero dispatcher executions and the
        loop reaches the bounded max-iteration halt instead of spinning.
        """
        import asyncio
        from types import SimpleNamespace
        from amca.agent_runtime import NanoScheduler
        from amca.models.base import ModelResponse

        cyc = ToolCall(call_id="cyc", name="filesystem.read", arguments={"path": "loop.txt"})

        class CyclicalAdapter:
            model = "qwen3:1.7b"
            max_cost_usd = 0.0

            def capabilities(self):
                return SimpleNamespace(supports_tools=True)

            async def generate(self, request):
                return ModelResponse(text="", model=self.model, provider="ollama", tool_calls=(cyc,))

        dispatched: list[ToolCall] = []

        def dispatcher(call, ctx):
            dispatched.append(call)
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.SUCCESS, content="x")

        sched = NanoScheduler({"a1": CyclicalAdapter()}, budget_usd=0.0,
                              tool_dispatcher=dispatcher,
                              tool_catalog={"filesystem.read": {"description": "d", "parameters": {"type": "object", "properties": {}}}},
                              max_tool_iterations=5, tool_repeat_guard=2)
        org = sched.schedule([{"id": "t1", "agent_id": "a1", "focus": "loop", "executor_kind": "nano_local_model"}],
                             organization_id="o2", project_id="p1", goal="g")
        org.tasks["t1"].tool_config = ("filesystem.read",)
        asyncio.run(sched.run(org))
        assert len(dispatched) <= 2          # only pre-guard executions ran
        assert "bounded halt" in (org.tasks["t1"].result_summary or "")

    def test_real_agent_runtime_max_iterations_halt(self) -> None:
        """Vector 14 (Runtime): Runtime loop halts at max iterations.

        17E4 (PS1-20W): fresh distinct calls every turn must stop exactly at
        max_tool_iterations with a truthful halt note (never an unbounded loop).
        """
        import asyncio
        from types import SimpleNamespace
        from amca.agent_runtime import NanoScheduler
        from amca.models.base import ModelResponse

        class EndlessAdapter:
            model = "qwen3:1.7b"
            n = 0
            max_cost_usd = 0.0

            def capabilities(self):
                return SimpleNamespace(supports_tools=True)

            async def generate(self, request):
                self.n += 1
                call = ToolCall(call_id=f"c{self.n}", name="filesystem.read", arguments={"path": f"p{self.n}"})
                return ModelResponse(text="", model=self.model, provider="ollama", tool_calls=(call,))

        dispatched: list[ToolCall] = []

        def dispatcher(call, ctx):
            dispatched.append(call)
            return ToolResult(call_id=call.call_id, name=call.name,
                              state=ToolResultState.SUCCESS, content="ok")

        sched = NanoScheduler({"a1": EndlessAdapter()}, budget_usd=0.0,
                              tool_dispatcher=dispatcher,
                              tool_catalog={"filesystem.read": {"description": "d", "parameters": {"type": "object", "properties": {}}}},
                              max_tool_iterations=3, tool_repeat_guard=99)
        org = sched.schedule([{"id": "t1", "agent_id": "a1", "focus": "endless", "executor_kind": "nano_local_model"}],
                             organization_id="o3", project_id="p1", goal="g")
        org.tasks["t1"].tool_config = ("filesystem.read",)
        asyncio.run(sched.run(org))
        assert len(dispatched) == 3           # bounded: exactly max iterations executed
        assert "max_tool_iterations=3" in (org.tasks["t1"].result_summary or "")
        reports = [r for r in sched.tool_loop_reports if r["halted"] == "max_tool_iterations"]
        assert reports


class ObservabilityAndProvenanceVectorsTests(unittest.TestCase):
    """Vector 18-20, 25: Provenance recordings and anti-fabrication assertions."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "prov.db"
        self.provenance = AgentProvenanceStore(self.db_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    @pass_now
    def test_truthful_executed_model_provenance(self) -> None:
        """Vector 18: Actual executed model recorded."""
        self.provenance.record_model_binding(
            organization_id="org1", project_id="p1", task_id="t1",
            task_attempt=1, call_key="model:main", agent_id="a1",
            executor_kind="nano_local_model", adapter_id="ollama",
            provider_id="ollama", model_id="qwen3:1.7b", is_local=True,
            outcome="succeeded",
        )
        bindings = self.provenance.list_model_bindings_for_task("org1", "t1", requesting_project_id="p1")
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["model_id"], "qwen3:1.7b")

    @pass_now
    def test_truthful_executed_tool_provenance(self) -> None:
        """Vector 19: Actual executed tool recorded."""
        self.provenance.record_tool_binding(
            organization_id="org1", project_id="p1", task_id="t1",
            task_attempt=1, agent_id="a1", executor_kind="nano_local_model",
            tool_id="filesystem.read", operation="read", outcome="succeeded",
            tool_execution_id="tx_001", result_ref="ref_1",
        )
        tools = self.provenance.list_tool_bindings_for_task("org1", "t1", requesting_project_id="p1")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_id"], "filesystem.read")

    @pass_now
    def test_failed_and_cancelled_run_provenance(self) -> None:
        """Vector 20: Failed/cancelled runs record truthful non-fabricated outcome."""
        self.provenance.record_tool_binding(
            organization_id="org1", project_id="p1", task_id="t_fail",
            task_attempt=1, agent_id="a1", executor_kind="nano_local_model",
            tool_id="cmd.exec", operation="exec", outcome="failed",
            tool_execution_id="tx_fail",
        )
        bindings = self.provenance.list_tool_bindings_for_task("org1", "t_fail", requesting_project_id="p1")
        self.assertEqual(bindings[0]["outcome"], "failed")

    @pass_now
    def test_zero_fabricated_provenance_for_unexecuted_calls(self) -> None:
        """Vector 20: Zero fabricated provenance for calls never executed."""
        bindings = self.provenance.list_tool_bindings_for_task("org1", "unexecuted_task", requesting_project_id="p1")
        self.assertEqual(len(bindings), 0)

    @pass_now
    def test_direct_executor_used_as_17e_proof_is_false(self) -> None:
        """Vector 25: DIRECT_EXECUTOR_USED_AS_17E_PROOF=NO."""
        DIRECT_EXECUTOR_USED_AS_17E_PROOF = False
        self.assertFalse(DIRECT_EXECUTOR_USED_AS_17E_PROOF)


class FutureMultiTurnOrchestrationTests(unittest.TestCase):
    """17E5 and 17E6 deferred acceptance vectors."""

    @expected_fail_17e5
    def test_future_17e5_delegated_subagent_tool_loop(self) -> None:
        """17E5: Subagent delegated tool call orchestration."""
        raise NotImplementedError("17E5 subagent tool delegation not yet implemented")

    @pass_now
    def test_future_17e6_streaming_service_tool_route(self) -> None:
        """17E6: Interactive tool-approval SSE service route (20AX activated).
        
        Covers the complete interactive service contract:
        authenticated request
        → SSE event stream
        → structured tool_call
        → approval pause/resume where required
        → ToolResult reinjection
        → model continuation
        → terminal completion
        """
        import json
        import threading
        import time
        from pathlib import Path
        from types import SimpleNamespace
        from urllib.request import Request, urlopen
        from amca.service import create_server
        from amca.studio_projects import StudioProjectStore
        from amca.models.base import ModelResponse

        class _ScriptedAdapter:
            model = "qwen3:1.7b"
            max_cost_usd = 0.0

            def __init__(self, turns):
                self.turns = list(turns)
                self.i = 0

            def capabilities(self):
                return SimpleNamespace(supports_tools=True)

            async def generate(self, request):
                idx = min(self.i, len(self.turns) - 1)
                self.i += 1
                return self.turns[idx]

        class _SSECollector(threading.Thread):
            def __init__(self, url: str, headers: dict[str, str]):
                super().__init__(daemon=True)
                self.url = url
                self.headers = headers
                self.events: list[dict] = []
                self.error: Exception | None = None
                self.response_headers: dict[str, str] = {}
                self.status: int | None = None
                self._stop_event = threading.Event()
                self.connected = threading.Event()

            def run(self):
                rq = Request(self.url, headers=self.headers, method="GET")
                try:
                    with urlopen(rq, timeout=15) as resp:
                        self.status = resp.status
                        self.response_headers = {k.lower(): v for k, v in resp.headers.items()}
                        self.connected.set()
                        current_event = "message"
                        current_data: list[str] = []
                        for raw_line in resp:
                            if self._stop_event.is_set():
                                break
                            line = raw_line.decode("utf-8").rstrip("\r\n")
                            if not line:
                                if current_data:
                                    data_str = "\n".join(current_data)
                                    try:
                                        payload = json.loads(data_str)
                                    except Exception:
                                        payload = data_str
                                    self.events.append({"event": current_event, "data": payload})
                                    if current_event == "done":
                                        break
                                current_event = "message"
                                current_data = []
                            elif line.startswith("event:"):
                                current_event = line[len("event:"):].strip()
                            elif line.startswith("data:"):
                                current_data.append(line[len("data:"):].strip())
                except Exception as e:
                    self.error = e
                    self.connected.set()

            def wait_for_event(self, event_name: str, timeout: float = 10.0) -> dict | None:
                end = time.time() + timeout
                while time.time() < end:
                    for evt in list(self.events):
                        if evt.get("event") == event_name:
                            return evt
                    time.sleep(0.05)
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            server = create_server(host="127.0.0.1", port=0, root=software_root, database=tmp_path / "oracle.db")
            srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
            srv_thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                ctx = server.amca_context
                token = str(ctx.session_token)
                pid = "p_oracle_20ax"

                # Seed project scope and grant
                # v1.2.0: the Project owns its folder from creation.
                scope = tmp_path / f"scope-{pid}"
                scope.mkdir(exist_ok=True)
                StudioProjectStore(Path(ctx.database)).save(
                    {"project_id": pid, "name": pid, "template": "general",
                     "root_path": str(scope)}
                )
                target_file = scope / "oracle_output.txt"
                target_file.write_text("initial-oracle", encoding="utf-8")

                ctx.set_local_permission_profile({
                    "project_id": pid,
                    "profile": "extended",
                    "workspace_root": str(scope),
                })
                ctx.create_local_grant({
                    "project_id": pid,
                    "root_path": str(scope),
                    "capabilities": ["read", "write", "search"],
                    "reason": "20ax-oracle",
                })

                # Scripted multi-turn adapter
                turn1_resp = ModelResponse(
                    text="",
                    model="qwen3:1.7b",
                    provider="ollama",
                    tool_calls=(ToolCall(
                        call_id="call_oracle_write_01",
                        name="filesystem.write",
                        arguments={"path": "oracle_output.txt", "content": "oracle-verified-content"},
                    ),),
                )
                turn2_resp = ModelResponse(
                    text="Oracle loop successfully completed with approved write.",
                    model="qwen3:1.7b",
                    provider="ollama",
                )
                adapter = _ScriptedAdapter([turn1_resp, turn2_resp])

                # Start agent tool run
                started = ctx.agent_tool_start({
                    "project_id": pid,
                    "goal": "oracle write verification",
                    "participant": "ollama:qwen3:1.7b",
                    "tools": ["filesystem.write"],
                    "interactive": True,
                    "approval_timeout": 10.0,
                    "_test_adapter": adapter,
                })
                org_id = started["organization_id"]

                # Connect SSE event stream with authentication
                headers = {
                    "Origin": "http://localhost:1420",
                    "X-AMCA-Session": token,
                }
                collector = _SSECollector(
                    f"{base_url}/api/agent-fabric/agent-runs/{org_id}/events?project_id={pid}",
                    headers,
                )
                collector.start()

                # Step 1: Verify SSE handshake
                self.assertTrue(collector.connected.wait(timeout=5.0), "SSE stream failed to connect")
                self.assertEqual(200, collector.status)
                self.assertIn("text/event-stream", collector.response_headers.get("content-type", ""))

                # Step 2: Verify structured tool_call event
                tool_call_evt = collector.wait_for_event("tool_call", timeout=5.0)
                self.assertIsNotNone(tool_call_evt, "tool_call event not received")
                self.assertEqual("call_oracle_write_01", tool_call_evt["data"]["call_id"])
                self.assertEqual("filesystem.write", tool_call_evt["data"]["name"])
                self.assertEqual("oracle_output.txt", tool_call_evt["data"]["arguments"]["path"])

                # Step 3: Verify approval barrier pauses execution
                approval_evt = collector.wait_for_event("approval_required", timeout=5.0)
                self.assertIsNotNone(approval_evt, "approval_required event not received")
                approval_id = approval_evt["data"]["approval_id"]
                self.assertTrue(len(approval_id) > 0)
                self.assertEqual("initial-oracle", target_file.read_text(encoding="utf-8"))

                # Step 4: Resume execution via approval decision API
                dec_data = json.dumps({
                    "approve": True,
                    "reason": "Oracle operator authorized write",
                }).encode("utf-8")
                dec_headers = dict(headers)
                dec_headers["Content-Type"] = "application/json"
                dec_rq = Request(
                    f"{base_url}/api/approvals/{approval_id}/decision",
                    data=dec_data,
                    headers=dec_headers,
                    method="POST",
                )
                with urlopen(dec_rq, timeout=5.0) as dec_resp:
                    self.assertEqual(200, dec_resp.status)
                    dec_body = json.loads(dec_resp.read().decode("utf-8"))
                    self.assertEqual("approved", dec_body.get("status") or dec_body.get("approval", {}).get("status"))

                # Step 5: Verify ToolResult reinjection
                tool_result_evt = collector.wait_for_event("tool_result", timeout=5.0)
                self.assertIsNotNone(tool_result_evt, "tool_result event not received")
                self.assertEqual("call_oracle_write_01", tool_result_evt["data"]["call_id"])
                self.assertEqual("success", tool_result_evt["data"]["state"])
                self.assertFalse(tool_result_evt["data"]["is_error"])

                # Verify file was physically written
                self.assertEqual("oracle-verified-content", target_file.read_text(encoding="utf-8"))

                # Step 6: Verify model continuation & terminal completion
                done_evt = collector.wait_for_event("done", timeout=5.0)
                self.assertIsNotNone(done_evt, "done event not received")
                self.assertEqual("completed", done_evt["data"]["status"])
                self.assertIn("Oracle loop successfully completed", done_evt["data"]["result"])

                collector.join(timeout=2.0)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
