"""PS1-20AE (17E5) governed service exposure of the certified 17E4 loop.

Deterministic, zero-network. Positive E2E drives the REAL service context
path (start -> scheduler thread -> status) with an in-process scripted
adapter seam (unreachable via HTTP - JSON can never carry adapters).
Route-level auth/scoping/equivalence are proven over real HTTP.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

software_root = Path(__file__).resolve().parents[1]
if str(software_root / "src") not in sys.path:
    sys.path.insert(0, str(software_root / "src"))

from amca.service import ServiceContext, create_server
from amca.studio_projects import StudioProjectStore
from amca.models.base import ModelResponse
from amca.models.tool_protocol import ToolCall, ToolResult, ToolResultState

SW = software_root
SCRATCH_ROOT = software_root.parent / ".agent-tmp" / "opencode-ps1-17e5-20ae" / "testtmp"


class _Scratch:
    """D:-only disposable scratch with lenient teardown (sqlite handles can
    outlive a finished worker thread by milliseconds)."""

    def __init__(self, name):
        self.path = SCRATCH_ROOT / f"{name}-{int(time.time() * 1000) % 10**9}"
        self.path.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        import shutil

        shutil.rmtree(self.path, ignore_errors=True)


def _ctx(tmp: Path):
    return ServiceContext(root=SW, database=tmp / "t.db", benchmarks=SW / "benchmarks" / "tasks.json")


class Scripted:
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


def _call(cid, name, **args):
    return ModelResponse(text="", model="qwen3:1.7b", provider="ollama",
                         tool_calls=(ToolCall(call_id=cid, name=name, arguments=args),))


def _final(text):
    return ModelResponse(text=text, model="qwen3:1.7b", provider="ollama")


def _seed_project_scope(ctx, tmp: Path, pid: str, capability_caps=("read", "search")):
    # v1.2.0: the Project folder is chosen when the Project is created and is
    # fixed from then on, so the scope is created first and handed to the
    # Project rather than attached to it afterwards.
    scope = tmp / f"scope-{pid}"
    scope.mkdir(exist_ok=True)
    StudioProjectStore(Path(ctx.database)).save(
        {"project_id": pid, "name": pid, "template": "general",
         "root_path": str(scope)}
    )
    (scope / "notes.txt").write_text("hello-contents", encoding="utf-8")
    ctx.set_local_permission_profile({"project_id": pid, "profile": "extended", "workspace_root": str(scope)})
    g = ctx.create_local_grant({"project_id": pid, "root_path": str(scope), "capabilities": list(capability_caps), "reason": "20ae"})
    return scope, g["grant_id"]


class ServiceE2EPositivePath(unittest.TestCase):
    def setUp(self):
        self.tmpobj = _Scratch("e2e")
        self.tmp = self.tmpobj.path
        self.ctx = _ctx(self.tmp)
        self.scope, _ = _seed_project_scope(self.ctx, self.tmp, "p20ae")

    def tearDown(self):
        self.tmpobj.cleanup()

    def _wait(self, org_id, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            st = self.ctx.agent_tool_status(org_id)["status"]
            if st != "running":
                return self.ctx.agent_tool_status(org_id)
            time.sleep(0.2)
        return self.ctx.agent_tool_status(org_id)

    def test_single_tool_e2e_through_service_method(self):
        adapter = Scripted([
            _call("1", "filesystem.read", path="notes.txt"),
            _final("answer: hello-contents"),
        ])
        started = self.ctx.agent_tool_start({
            "project_id": "p20ae", "goal": "read notes",
            "participant": "ollama:qwen3:1.7b", "tools": ["filesystem.read"],
            "_test_adapter": adapter,
        })
        done = self._wait(started["organization_id"])
        self.assertEqual("completed", done["status"])
        self.assertEqual("answer: hello-contents", done["result"])
        self.assertEqual([{"tool_id": "filesystem.read", "outcome": "succeeded"}], done["tool_bindings"])
        self.assertTrue(done["model_bindings"], "model binding must be truthful")
        self.assertEqual("qwen3:1.7b", done["model_bindings"][0]["model_id"])

    def test_multi_call_turn_through_service(self):
        both = ModelResponse(text="", model="qwen3:1.7b", provider="ollama", tool_calls=(
            ToolCall(call_id="a", name="filesystem.read", arguments={"path": "notes.txt"}),
            ToolCall(call_id="b", name="filesystem.list", arguments={"path": "."}),
        ))
        adapter = Scripted([both, both, _final("twice")])
        self.scope2, _ = _seed_project_scope(self.ctx, self.tmp, "p20ae", ("read", "search", "list"))
        started = self.ctx.agent_tool_start({
            "project_id": "p20ae", "goal": "g", "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.read", "filesystem.list"], "_test_adapter": adapter,
        })
        done = self._wait(started["organization_id"])
        tools = {(t["tool_id"], t["outcome"]) for t in done["tool_bindings"]}
        self.assertIn(("filesystem.read", "succeeded"), tools)
        self.assertIn(("filesystem.list", "succeeded"), tools)

    def test_write_tool_never_autoexecutes_approval_required(self):
        ws = _seed_project_scope(self.ctx, self.tmp, "pwr", ("read", "write"))[0]
        (ws / "in.txt").write_text("x", encoding="utf-8")
        adapter = Scripted([
            _call("w1", "filesystem.write", path="out.txt"),
            _final("needs approval"),
        ])
        started = self.ctx.agent_tool_start({
            "project_id": "pwr", "goal": "g", "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.write"], "_test_adapter": adapter,
        })
        # write capability granted? grant caps ('read','write') -> expose;
        # dispatcher must ONLY create approval, never write the file
        for _ in range(150):
            st = self.ctx.agent_tool_status(started["organization_id"])
            if st["status"] != "running":
                break
            time.sleep(0.2)
        self.assertFalse((ws / "out.txt").exists(), "write must NOT execute without human approval")
        bindings = self.ctx.agent_tool_status(started["organization_id"])["tool_bindings"]
        self.assertTrue(all(b["outcome"] != "succeeded" for b in bindings))


class DispatcherSecurityVectors(unittest.TestCase):
    def setUp(self):
        self.tmpobj = _Scratch("disp")
        self.tmp = self.tmpobj.path
        self.ctx = _ctx(self.tmp)
        self.scope, _ = _seed_project_scope(self.ctx, self.tmp, "psec")
        self.d = self.ctx._make_agent_tool_dispatcher("psec")

    def tearDown(self):
        self.tmpobj.cleanup()

    def _run(self, call):
        out = self.d(call, {"project_id": "psec", "task_id": "t", "task_attempt": 1,
                            "agent_id": "a", "executor_kind": "nano_local_model"})
        return out

    def test_unknown_tool_zero_execution(self):
        r = self._run(ToolCall(call_id="u", name="not.real", arguments={}))
        self.assertIs(r.state, ToolResultState.UNKNOWN_TOOL)

    def test_capability_not_granted_zero_execution(self):
        # grants only read/search: write proposal for a project WITHOUT the
        # write grant capability still lands on approval path (never executed),
        # and a direct action outside grants denies with zero effect.
        r = self._run(ToolCall(call_id="x", name="filesystem.trash", arguments={"path": "notes.txt"}))
        # proposal refused (no capability) via any non-success truthful state;
        # the file must remain untouched in every accepted outcome.
        self.assertIn(r.state, (
            ToolResultState.APPROVAL_REQUIRED,
            ToolResultState.DENIED,
            ToolResultState.MALFORMED_REQUEST,
            ToolResultState.EXECUTION_ERROR,
        ))
        self.assertTrue((self.scope / "notes.txt").exists())

    def test_traversal_denied_zero_execution(self):
        r = self._run(ToolCall(call_id="t", name="filesystem.read", arguments={"path": "..\\..\\windows\\win.ini"}))
        self.assertTrue(r.is_error)
        self.assertNotIn("hello-contents", r.content)

    def test_malformed_inspection_no_execution(self):
        r = self._run(ToolCall(call_id="i", name="command.inspect", arguments={"argv": [], "path": "."}))
        self.assertTrue(r.is_error)


class StartGuardVectors(unittest.TestCase):
    def setUp(self):
        self.tmpobj = _Scratch("guard")
        self.tmp = self.tmpobj.path
        self.ctx = _ctx(self.tmp)
        _seed_project_scope(self.ctx, self.tmp, "pg")

    def tearDown(self):
        self.tmpobj.cleanup()

    def test_json_body_cannot_inject_adapter(self):
        with self.assertRaises(ValueError):
            self.ctx.agent_tool_start({
                "project_id": "pg", "goal": "g", "participant": "ollama:qwen3:1.7b",
                "tools": ["filesystem.read"], "_test_adapter": {"generate": "x"},
            })

    def test_no_effective_tools_rejected(self):
        with self.assertRaises(ValueError):
            self.ctx.agent_tool_start({
                "project_id": "pg", "goal": "g", "participant": "ollama:qwen3:1.7b",
                "tools": ["filesystem.delete"], "_test_adapter": Scripted([_final("x")]),
            })

    def test_unsupported_model_adapter_rejected(self):
        class NoTools(Scripted):
            def capabilities(self):
                return SimpleNamespace(supports_tools=False)

        with self.assertRaises(ValueError):
            self.ctx.agent_tool_start({
                "project_id": "pg", "goal": "g", "participant": "ollama:dolphin-llama3:8b",
                "tools": ["filesystem.read"], "_test_adapter": NoTools([_final("x")]),
            })


class HttpRouteVectors(unittest.TestCase):
    def setUp(self):
        self.tmpobj = _Scratch("http")
        tmp = self.tmpobj.path
        self.server = create_server(host="127.0.0.1", port=0, root=SW, database=tmp / "h.db")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = str(self.server.amca_context.session_token)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpobj.cleanup()

    def _req(self, path, method="GET", body=None, token=None, origin=True):
        data = json.dumps(body).encode() if body is not None else None
        rq = Request(self.base + path, data=data, method=method)
        if origin:
            rq.add_header("Origin", "http://localhost:1420")
        rq.add_header("X-AMCA-Session", token if token is not None else self.token)
        if data:
            rq.add_header("Content-Type", "application/json")
        try:
            with urlopen(rq, timeout=20) as r:
                raw = r.read()
                return r.status, json.loads(raw or b"{}")
        except HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw or b"{}")

    def test_requires_session(self):
        s, _ = self._req("/api/agent-fabric/agent-runs", method="POST", body={}, token="bogus")
        self.assertEqual(401, s)

    def test_status_unknown_org_generic_404(self):
        s, b = self._req("/api/agent-fabric/agent-runs/nope/status?project_id=px")
        self.assertEqual(404, s)
        self.assertEqual("unknown organization", b["error"])

    def test_status_requires_project(self):
        s, b = self._req("/api/agent-fabric/agent-runs/x/status")
        self.assertEqual(400, s)
        self.assertIn("project_id", b["error"])

    def test_start_validation_400(self):
        s, b = self._req("/api/agent-fabric/agent-runs", method="POST",
                         body={"project_id": "p", "goal": "g", "participant": "ollama:qwen3:1.7b",
                               "tools": ["filesystem.read"], "_test_adapter": {"evil": 1}})
        self.assertEqual(400, s)

    def test_cancel_unknown_generic_404(self):
        s, b = self._req("/api/agent-fabric/agent-runs/nope/cancel", method="POST", body={})
        self.assertEqual(404, s)
        self.assertEqual("unknown organization", b["error"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
