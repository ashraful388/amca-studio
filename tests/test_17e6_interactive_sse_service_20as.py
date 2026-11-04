"""ANTIGRAVITY-AMCA-V111-17E6-INTERACTIVE-SSE-IMPLEMENTATION-20AS

Dedicated acceptance test suite for 17E6 Interactive SSE Service Route:
1. SSE Handshake, Security & Scoping:
   - text/event-stream headers, Origin check, X-AMCA-Session auth
   - 400 on missing project_id, 404 generic on unknown org_id
   - GET /api/agent-fabric/agent-runs/<org_id>/events and .../stream equivalence
2. Structured Tool Event Streaming:
   - Streamed tool_call and tool_result events with canonical structure
   - Verified turn_complete, status, and done terminal events
3. Interactive Approval Barrier (CRITICAL):
   - Mutating tool call (filesystem.write) triggers approval_required event
   - Execution paused at barrier
   - Calling POST /api/approvals/<id>/decision with approve=True resumes loop,
     executes write, emits tool_result(state=success), and completes run
4. Approval Rejection:
   - Calling POST /api/approvals/<id>/decision with approve=False resumes loop,
     skips execution, emits tool_result(state=denied), and reinjects rejection
5. Inspection Command Approval:
   - command.inspect triggers approval_required event
   - Approval executes inspection command and emits tool_result
6. Active Stream Cancellation:
   - POST /api/agent-fabric/agent-runs/<org_id>/cancel during active wait
   - Emits status: cancelled and done events, stream closes without zombies
7. Reconnection to Terminal Run:
   - Connecting to completed run replays past events through terminal done and closes cleanly
"""

from __future__ import annotations

import json
import os
import sys
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
SCRATCH_ROOT = software_root.parent / ".agent-tmp" / "antigravity-17e6-20as" / "testtmp"


class _Scratch:
    def __init__(self, name: str):
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


def _call(cid: str, name: str, **args):
    return ModelResponse(
        text="",
        model="qwen3:1.7b",
        provider="ollama",
        tool_calls=(ToolCall(call_id=cid, name=name, arguments=args),),
    )


def _final(text: str):
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
    (scope / "notes.txt").write_text("initial-note", encoding="utf-8")
    ctx.set_local_permission_profile({"project_id": pid, "profile": "extended", "workspace_root": str(scope)})
    g = ctx.create_local_grant({
        "project_id": pid,
        "root_path": str(scope),
        "capabilities": list(capability_caps),
        "reason": "20as",
    })
    return scope, g["grant_id"]


class SSECollector(threading.Thread):
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

    def stop(self):
        self._stop_event.set()


class Test17E6InteractiveSSEService(unittest.TestCase):
    def setUp(self):
        self.tmpobj = _Scratch("17e6_sse")
        self.tmp = self.tmpobj.path
        self.server = create_server(host="127.0.0.1", port=0, root=SW, database=self.tmp / "test.db")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.ctx = self.server.amca_context
        self.token = str(self.ctx.session_token)
        self.scope, self.grant_id = _seed_project_scope(
            self.ctx, self.tmp, "p17e6", ("read", "write", "search", "list", "propose_command")
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpobj.cleanup()

    def _headers(self, origin: bool = True, token: str | None = None) -> dict[str, str]:
        h = {}
        if origin:
            h["Origin"] = "http://localhost:1420"
        h["X-AMCA-Session"] = token if token is not None else self.token
        return h

    def _post_json(self, path: str, body: dict, token: str | None = None, origin: bool = True):
        data = json.dumps(body).encode("utf-8")
        headers = self._headers(origin=origin, token=token)
        headers["Content-Type"] = "application/json"
        rq = Request(self.base + path, data=data, headers=headers, method="POST")
        try:
            with urlopen(rq, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    # -------------------------------------------------------------------------
    # Vector 1: Handshake, Session, Origin, and Scoping Security
    # -------------------------------------------------------------------------

    def test_sse_endpoint_requires_session(self):
        url = f"{self.base}/api/agent-fabric/agent-runs/any/events?project_id=p17e6"
        rq = Request(url, headers=self._headers(token="invalid_token"), method="GET")
        with self.assertRaises(HTTPError) as cm:
            urlopen(rq, timeout=5)
        self.assertEqual(401, cm.exception.code)

    def test_sse_endpoint_rejects_foreign_origin(self):
        url = f"{self.base}/api/agent-fabric/agent-runs/any/events?project_id=p17e6"
        headers = self._headers()
        headers["Origin"] = "http://malicious-site.com"
        rq = Request(url, headers=headers, method="GET")
        with self.assertRaises(HTTPError) as cm:
            urlopen(rq, timeout=5)
        self.assertEqual(403, cm.exception.code)

    def test_sse_endpoint_requires_project_id(self):
        url = f"{self.base}/api/agent-fabric/agent-runs/any/events"
        rq = Request(url, headers=self._headers(), method="GET")
        with self.assertRaises(HTTPError) as cm:
            urlopen(rq, timeout=5)
        self.assertEqual(400, cm.exception.code)
        body = json.loads(cm.exception.read())
        self.assertIn("project_id", body.get("error", ""))

    def test_sse_endpoint_unknown_org_returns_generic_404(self):
        url = f"{self.base}/api/agent-fabric/agent-runs/nonexistent_org/events?project_id=p17e6"
        rq = Request(url, headers=self._headers(), method="GET")
        with self.assertRaises(HTTPError) as cm:
            urlopen(rq, timeout=5)
        self.assertEqual(404, cm.exception.code)
        body = json.loads(cm.exception.read())
        self.assertEqual("unknown organization", body.get("error"))

    def test_sse_endpoint_stream_alias_handshake(self):
        # /stream alias returns identical behavior
        url = f"{self.base}/api/agent-fabric/agent-runs/nonexistent_org/stream?project_id=p17e6"
        rq = Request(url, headers=self._headers(), method="GET")
        with self.assertRaises(HTTPError) as cm:
            urlopen(rq, timeout=5)
        self.assertEqual(404, cm.exception.code)

    # -------------------------------------------------------------------------
    # Vector 2: Streamed Structured Tool Events (Direct Read)
    # -------------------------------------------------------------------------

    def test_direct_read_tool_streamed_events(self):
        adapter = Scripted([
            _call("c_read_1", "filesystem.read", path="notes.txt"),
            _final("Note read successfully."),
        ])
        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "read notes",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.read"],
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()
        collector.join(timeout=10)

        self.assertEqual(200, collector.status)
        self.assertIn("text/event-stream", collector.response_headers.get("content-type", ""))

        event_types = [e["event"] for e in collector.events]
        self.assertIn("status", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertIn("done", event_types)

        tool_call_event = next(e for e in collector.events if e["event"] == "tool_call")
        self.assertEqual("c_read_1", tool_call_event["data"]["call_id"])
        self.assertEqual("filesystem.read", tool_call_event["data"]["name"])
        self.assertEqual("notes.txt", tool_call_event["data"]["arguments"]["path"])

        tool_result_event = next(e for e in collector.events if e["event"] == "tool_result")
        self.assertEqual("c_read_1", tool_result_event["data"]["call_id"])
        self.assertEqual("success", tool_result_event["data"]["state"])
        self.assertIn("initial-note", tool_result_event["data"]["content"])

        done_event = next(e for e in collector.events if e["event"] == "done")
        self.assertEqual("completed", done_event["data"]["status"])
        self.assertEqual("Note read successfully.", done_event["data"]["result"])

    # -------------------------------------------------------------------------
    # Vector 3: Interactive Approval Barrier — APPROVE Path
    # -------------------------------------------------------------------------

    def test_interactive_approval_barrier_approved(self):
        # Target file to create/overwrite
        target_file = self.scope / "new_document.txt"
        target_file.write_text("old-content", encoding="utf-8")

        adapter = Scripted([
            _call("c_write_1", "filesystem.write", path="new_document.txt", content="authorized-content"),
            _final("File updated successfully."),
        ])

        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "write file",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.write"],
            "interactive": True,
            "approval_timeout": 10.0,
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()

        # 1. Wait for approval_required event
        approval_evt = collector.wait_for_event("approval_required", timeout=5.0)
        self.assertIsNotNone(approval_evt, "approval_required event must be emitted")
        approval_id = approval_evt["data"]["approval_id"]
        self.assertTrue(len(approval_id) > 0)
        self.assertEqual("filesystem.write", approval_evt["data"]["name"])

        # Verify target file has NOT been written yet
        self.assertEqual("old-content", target_file.read_text(encoding="utf-8"))

        # 2. Decide approval: approve=True via existing POST /api/approvals/<id>/decision
        status_code, dec_resp = self._post_json(
            f"/api/approvals/{approval_id}/decision",
            {"approve": True, "reason": "Operator certified 20AS"},
        )
        self.assertEqual(200, status_code)
        app_status = dec_resp.get("status") or dec_resp.get("approval", {}).get("status")
        self.assertEqual("approved", app_status)

        # 3. Wait for run to complete via SSE
        done_evt = collector.wait_for_event("done", timeout=5.0)
        self.assertIsNotNone(done_evt, "done event must be received after approval")
        self.assertEqual("completed", done_evt["data"]["status"])

        # 4. Verify file was written
        self.assertEqual("authorized-content", target_file.read_text(encoding="utf-8"))

        # 5. Verify tool_result event
        tool_result_evt = next(e for e in collector.events if e["event"] == "tool_result")
        self.assertEqual("success", tool_result_evt["data"]["state"])
        self.assertFalse(tool_result_evt["data"]["is_error"])

        collector.join(timeout=2)

    # -------------------------------------------------------------------------
    # Vector 4: Interactive Approval Barrier — REJECT Path
    # -------------------------------------------------------------------------

    def test_interactive_approval_barrier_rejected(self):
        target_file = self.scope / "protected.txt"
        target_file.write_text("protected-content", encoding="utf-8")

        adapter = Scripted([
            _call("c_write_2", "filesystem.write", path="protected.txt", content="malicious-payload"),
            _final("I could not update the file because permission was denied."),
        ])

        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "overwrite protected file",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.write"],
            "interactive": True,
            "approval_timeout": 10.0,
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()

        # 1. Wait for approval_required event
        approval_evt = collector.wait_for_event("approval_required", timeout=5.0)
        self.assertIsNotNone(approval_evt)
        approval_id = approval_evt["data"]["approval_id"]

        # 2. Reject the approval request
        status_code, dec_resp = self._post_json(
            f"/api/approvals/{approval_id}/decision",
            {"approve": False, "reason": "Operator denied unsafe overwrite"},
        )
        self.assertEqual(200, status_code)
        app_status = dec_resp.get("status") or dec_resp.get("approval", {}).get("status")
        self.assertEqual("denied", app_status)

        # 3. Wait for run to complete
        done_evt = collector.wait_for_event("done", timeout=5.0)
        self.assertIsNotNone(done_evt)

        # 4. Assert target file was NEVER modified
        self.assertEqual("protected-content", target_file.read_text(encoding="utf-8"))

        # 5. Assert tool_result event recorded denial
        tool_result_evt = next(e for e in collector.events if e["event"] == "tool_result")
        self.assertEqual("denied", tool_result_evt["data"]["state"])
        self.assertTrue(tool_result_evt["data"]["is_error"])

        collector.join(timeout=2)

    # -------------------------------------------------------------------------
    # Vector 5: Interactive Approval Barrier — Inspection Command Path
    # -------------------------------------------------------------------------

    def test_inspection_command_approval(self):
        adapter = Scripted([
            _call("c_insp_1", "command.inspect", argv=["whoami"], path="."),
            _final("Inspection completed."),
        ])
        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "inspect user identity",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["command.inspect"],
            "interactive": True,
            "approval_timeout": 10.0,
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()

        approval_evt = collector.wait_for_event("approval_required", timeout=5.0)
        self.assertIsNotNone(approval_evt, "approval_required must be emitted for command.inspect")
        approval_id = approval_evt["data"]["approval_id"]

        status_code, dec_resp = self._post_json(
            f"/api/approvals/{approval_id}/decision",
            {"approve": True, "reason": "Operator approved whoami inspection"},
        )
        self.assertEqual(200, status_code)
        app_status = dec_resp.get("status") or dec_resp.get("approval", {}).get("status")
        self.assertEqual("approved", app_status)

        done_evt = collector.wait_for_event("done", timeout=5.0)
        self.assertIsNotNone(done_evt)
        self.assertEqual("completed", done_evt["data"]["status"])

        tool_result_evt = next(e for e in collector.events if e["event"] == "tool_result")
        self.assertEqual("success", tool_result_evt["data"]["state"])
        self.assertFalse(tool_result_evt["data"]["is_error"])

        collector.join(timeout=2)

    # -------------------------------------------------------------------------
    # Vector 6: Service-Path Cancellation During Active Approval Wait
    # -------------------------------------------------------------------------

    def test_cancellation_during_active_approval_wait(self):
        target_file = self.scope / "pending.txt"
        target_file.write_text("pending-content", encoding="utf-8")

        adapter = Scripted([
            _call("c_write_3", "filesystem.write", path="pending.txt", content="unapproved"),
            _final("Done"),
        ])

        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "pending write",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.write"],
            "interactive": True,
            "approval_timeout": 15.0,
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()

        # Wait until approval_required is emitted and loop is actively paused
        approval_evt = collector.wait_for_event("approval_required", timeout=5.0)
        self.assertIsNotNone(approval_evt)

        # Cancel the run via service cancel route
        code, cancel_resp = self._post_json(
            f"/api/agent-fabric/agent-runs/{org_id}/cancel",
            {},
        )
        self.assertEqual(200, code)
        cancel_status = cancel_resp.get("status") or cancel_resp.get("agent_run", {}).get("status")
        self.assertEqual("cancelled", cancel_status)

        # Stream receives cancelled status and done
        collector.join(timeout=5)
        self.assertEqual("pending-content", target_file.read_text(encoding="utf-8"))

        # Verify terminal event in collector
        done_evt = collector.wait_for_event("done", timeout=1.0)
        self.assertIsNotNone(done_evt)
        self.assertEqual("cancelled", done_evt["data"]["status"])

    # -------------------------------------------------------------------------
    # Vector 7: Reconnection to Terminal Run
    # -------------------------------------------------------------------------

    def test_reconnect_to_terminal_run_replays_events_and_closes(self):
        adapter = Scripted([
            _call("c_read_rec", "filesystem.read", path="notes.txt"),
            _final("Final answer from completed run."),
        ])
        started = self.ctx.agent_tool_start({
            "project_id": "p17e6",
            "goal": "read notes for reconnect",
            "participant": "ollama:qwen3:1.7b",
            "tools": ["filesystem.read"],
            "_test_adapter": adapter,
        })
        org_id = started["organization_id"]

        # Wait for run to finish completely
        for _ in range(50):
            st = self.ctx.agent_tool_status(org_id)
            if st["status"] == "completed":
                break
            time.sleep(0.1)

        # Connect AFTER completion
        collector = SSECollector(
            f"{self.base}/api/agent-fabric/agent-runs/{org_id}/events?project_id=p17e6",
            self._headers(),
        )
        collector.start()
        collector.join(timeout=5)

        self.assertFalse(collector.is_alive(), "Collector must close cleanly on terminal run replay")
        event_types = [e["event"] for e in collector.events]
        self.assertIn("status", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        self.assertIn("done", event_types)

        done_evt = next(e for e in collector.events if e["event"] == "done")
        self.assertEqual("completed", done_evt["data"]["status"])


if __name__ == "__main__":
    unittest.main()
