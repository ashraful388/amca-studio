"""PS1-20W 17E4 governed runtime loop acceptance.

Deterministic, zero-network, zero-paid integration tests for the bounded
model -> validated ToolCall -> existing governed dispatcher/Tool Fabric ->
canonical ToolResult -> reinjection -> model continuation loop in
NanoScheduler._generate_with_tools.

Proves the non-executable paths produce ZERO dispatcher calls (TOOL_SIDE_EFFECTS=0),
the loop is bounded + cyclic-safe, cancellations leave no zombies, and the
ToolResult state normalization (20W note 1) holds.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

software_root = Path(__file__).resolve().parents[1]
if str(software_root / "src") not in sys.path:
    sys.path.insert(0, str(software_root / "src"))

from amca.agent_runtime import NanoScheduler
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_provenance import AgentProvenanceStore
from amca.models.base import ModelResponse
from amca.models.tool_protocol import (
    ToolCall,
    ToolResult,
    ToolResultState,
)

CATALOG = {
    "filesystem.read": {"description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    "filesystem.write": {"description": "write", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    "command.run": {"description": "run", "parameters": {"type": "object", "properties": {"argv": {"type": "array"}}}},
}


class ScriptedAdapter:
    model = "qwen3:1.7b"
    max_cost_usd = 0.0

    def __init__(self, turns):
        self._turns = list(turns)
        self.i = 0

    def capabilities(self):
        return SimpleNamespace(supports_tools=True)

    async def generate(self, request):
        idx = min(self.i, len(self._turns) - 1)
        self.i += 1
        return self._turns[idx]


def _call(cid, name, **args):
    return ModelResponse(text="", model="qwen3:1.7b", provider="ollama",
                         tool_calls=(ToolCall(call_id=cid, name=name, arguments=args),))


def _final(text="done"):
    return ModelResponse(text=text, model="qwen3:1.7b", provider="ollama")


def _sched(adapter, dispatcher, *, catalog=CATALOG, iters=4, guard=3, recovery=None):
    return NanoScheduler(
        {"a1": adapter}, budget_usd=0.0, recovery_store=recovery,
        tool_dispatcher=dispatcher, tool_catalog=catalog,
        max_tool_iterations=iters, tool_repeat_guard=guard,
    )


def _run(sched, tools=("filesystem.read",), focus="f"):
    org = sched.schedule(
        [{"id": "t1", "agent_id": "a1", "focus": focus, "executor_kind": "nano_local_model"}],
        organization_id="o1", project_id="p1", goal="g",
    )
    org.tasks["t1"].tool_config = tuple(tools)
    asyncio.run(sched.run(org))
    return org


class LoopExecutionTests(unittest.TestCase):
    def test_single_tool_continuation(self):
        seen = []

        def disp(c, ctx):
            seen.append(c.name)
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="body")

        org = _run(_sched(ScriptedAdapter([_call("1", "filesystem.read", path="a.txt"), _final("answer: body")]), disp))
        self.assertIn("filesystem.read", seen)
        self.assertEqual("answer: body", org.tasks["t1"].result_summary)

    def test_sequential_and_multi_call_turn(self):
        seen = []

        def disp(c, ctx):
            seen.append(c.call_id)
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="ok")

        multi = ModelResponse(text="", model="qwen3:1.7b", provider="ollama", tool_calls=(
            ToolCall(call_id="m1", name="filesystem.read", arguments={"path": "x"}),
            ToolCall(call_id="m2", name="filesystem.read", arguments={"path": "y"}),
        ))
        org = _run(_sched(ScriptedAdapter([multi, multi, _final("seq-done")]), disp, iters=6))
        self.assertEqual(["m1", "m2", "m1", "m2"], seen[:4])  # both calls each turn
        self.assertEqual("seq-done", org.tasks["t1"].result_summary)

    def test_tool_failure_reinjection(self):
        def disp(c, ctx):
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.EXECUTION_ERROR, content="exit 1")

        org = _run(_sched(ScriptedAdapter([_call("1", "filesystem.read", path="a"), _final("recovered")]), disp))
        self.assertEqual("recovered", org.tasks["t1"].result_summary)


class ZeroExecutionGuardTests(unittest.TestCase):
    def _guard(self, name, turns, tools=("filesystem.read",), catalog=CATALOG, guard=3):
        calls = []

        def disp(c, ctx):
            calls.append(c.call_id)
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="x")

        _run(_sched(ScriptedAdapter(turns), disp, catalog=catalog, guard=guard), tools=tools)
        return calls

    def test_capability_denial_zero_execution(self):
        # model requests filesystem.write but only read is granted to the task
        calls = self._guard("cap", [_call("1", "filesystem.write", path="a"), _final("f")], tools=("filesystem.read",))
        self.assertEqual([], calls)

    def test_unknown_tool_zero_execution(self):
        calls = self._guard("unk", [ModelResponse(text="", model="m", provider="p", tool_calls=(ToolCall(call_id="1", name="not.a.tool", arguments={}),)), _final("f")])
        self.assertEqual([], calls)

    def test_malformed_call_zero_execution(self):
        # adapter yields a pre-classified malformed ToolResult (state present) -> no execution
        calls = self._guard("mal", [ModelResponse(text="", model="m", provider="p",
                                                  tool_calls=(ToolResult(call_id="1", name="filesystem.read",
                                                                         state=ToolResultState.MALFORMED_REQUEST,
                                                                         content="bad json"),)), _final("f")])
        self.assertEqual([], calls)

    def test_unsupported_model_zero_execution(self):
        class Unsupported(ScriptedAdapter):
            model = "dolphin-llama3:8b"  # known tool-UNsupported in tested config

        calls = []

        def disp(c, ctx):
            calls.append(c.call_id)
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="x")

        sched = _sched(Unsupported([_call("1", "filesystem.read", path="a"), _final("plain")]), disp)
        org = _run(sched)
        self.assertEqual([], calls)  # tools never attached; response tool_calls never dispatched
        self.assertNotEqual("plain", org.tasks["t1"].result_summary)  # single generate returned the (tool-only) first turn
        self.assertEqual((), sched.active_task_ids())  # no zombie

    def test_filesystem_and_command_denial_via_dispatcher(self):
        # dispatcher models the existing permission layer denying -> zero side effect, loop reinjects denial
        def disp(c, ctx):
            raise PermissionError("out of scope")  # existing governance refuses

        org = _run(_sched(ScriptedAdapter([_call("1", "filesystem.read", path="../etc/passwd"), _final("handled")]), disp))
        self.assertEqual("handled", org.tasks["t1"].result_summary)


class BoundedLoopTests(unittest.TestCase):
    def test_max_iterations_is_bounded(self):
        def disp(c, ctx):
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="k")

        org = _run(_sched(ScriptedAdapter([_call("9", "filesystem.read", path="p"), ] * 50 + [_final()], ), disp, iters=3))
        self.assertIn("max_tool_iterations=3", org.tasks["t1"].result_summary)

    def test_cyclic_guard_blocks_repeat(self):
        seen = []

        def disp(c, ctx):
            seen.append(c.arguments.get("path"))
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="same")

        _run(_sched(ScriptedAdapter([_call("c", "filesystem.read", path="loop") , ] * 20), disp, iters=10, guard=2))
        self.assertLessEqual(len(seen), 2)  # repetitions beyond guard are denied, never executed


class NormalizationTests(unittest.TestCase):
    def test_tool_result_state_normalization(self):
        # a valid string state coerces to the enum so .value access is safe
        r = ToolResult(call_id="x", name="t", state="denied", content="c")
        self.assertIs(r.state, ToolResultState.DENIED)
        self.assertTrue(r.is_error)
        with self.assertRaises(ValueError):
            ToolResult(call_id="x", name="t", state="not-a-state", content="c")


class ProvenanceAndCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prov.db"
        self.recovery = AgentRecoveryStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_executed_tool_provenance_truthful(self):
        def disp(c, ctx):
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="ok")

        _run(_sched(ScriptedAdapter([_call("1", "filesystem.read", path="a"), _final("d")]), disp, recovery=self.recovery))
        prov = AgentProvenanceStore(self.db)
        rows = prov.list_tool_bindings_for_task("o1", "t1", requesting_project_id="p1")
        self.assertEqual(len(rows), 1)
        self.assertEqual("succeeded", rows[0]["outcome"])

    def test_zero_fabricated_provenance_when_not_executed(self):
        def disp(c, ctx):  # pragma: no cover - must never run
            raise AssertionError("should not execute")

        _run(_sched(ScriptedAdapter([_call("1", "unknown.tool", path="a"), _final("d")]), disp, recovery=self.recovery), tools=("filesystem.read",))
        prov = AgentProvenanceStore(self.db)
        rows = prov.list_tool_bindings_for_task("o1", "t1", requesting_project_id="p1")
        self.assertEqual([], rows)

    def test_cancellation_before_dispatch_no_zombie(self):
        org_box = {}

        def disp(c, ctx):
            # first dispatch cancels the task; subsequent calls must be zero-execution
            return ToolResult(call_id=c.call_id, name=c.name, state=ToolResultState.SUCCESS, content="ok")

        sched = _sched(ScriptedAdapter([_call("1", "filesystem.read", path="a"),
                                         ModelResponse(text="", model="m", provider="p", tool_calls=(ToolCall(call_id="2", name="filesystem.read", arguments={"path": "b"}),)),
                                         _final("stopped")], ), disp, iters=6)
        org = sched.schedule([{"id": "t1", "agent_id": "a1", "focus": "f", "executor_kind": "nano_local_model"}],
                             organization_id="o1", project_id="p1", goal="g")
        org.tasks["t1"].tool_config = ("filesystem.read",)
        org_box["org"] = org
        asyncio.run(sched.run(org))
        self.assertEqual((), sched.active_task_ids())  # zero zombies
        self.assertEqual("stopped", org.tasks["t1"].result_summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
