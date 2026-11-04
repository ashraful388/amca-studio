"""HTTP route tests for the facilities-panel stores (Round 22).

Mirrors the _req fixture pattern from test_studio_alpha_release_candidate.py:
a live stdlib-HTTP server on a temp SQLite DB, exercised over loopback.
Covers user_memories, studio_commands, mcp_servers, run_hooks, studio_skills:
create -> get -> list -> enable-toggle -> (probe for MCP) -> delete.
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server


class FacilitiesStoresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca_facilities.db"

        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.root,
            database=self.database,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        session_res = self._req("/api/session", authorized=False)[1]
        self.token = str(session_res.get("session_token", ""))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)
        self.temporary.cleanup()

    def _req(self, path, method="GET", payload=None, authorized=True):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(url, data=data, method=method)
        req.add_header("Origin", "http://localhost:1420")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        if authorized and hasattr(self, "token") and self.token:
            req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req, timeout=10.0) as resp:
                status = resp.status
                raw = resp.read()
                body = json.loads(raw.decode("utf-8")) if raw else {}
                return status, body
        except HTTPError as exc:
            raw = exc.read()
            body = json.loads(raw.decode("utf-8")) if raw else {}
            return exc.code, body

    # -- user memories ----------------------------------------------------
    def test_user_memory_crud_pin_context(self) -> None:
        s, m = self._req("/api/user/memory", method="POST", payload={
            "title": "Operator rule", "content": "Prefer local models for drafts.",
            "memory_type": "operator", "pinned": False,
        })
        self.assertIn(s, (200, 201))
        mem_id = m["memory"]["memory_id"]

        s, got = self._req(f"/api/user/memory/{mem_id}")
        self.assertEqual(s, 200)
        self.assertEqual(got["memory"]["title"], "Operator rule")

        s, listed = self._req("/api/user/memory")
        self.assertEqual(s, 200)
        self.assertTrue(any(x["memory_id"] == mem_id for x in listed["memories"]))

        s, pinned = self._req(f"/api/user/memory/{mem_id}/pin", method="POST", payload={"pinned": True})
        self.assertEqual(s, 200)
        self.assertTrue(pinned["memory"]["pinned"])

        s, ctx = self._req("/api/user/memory/context?query=local+models")
        self.assertEqual(s, 200)
        bodies = " ".join(
            (x.get("title", "") + " " + x.get("content", ""))
            for x in ctx.get("memories", [])
        ).lower()
        self.assertIn("local models", bodies)

        s, gone = self._req(f"/api/user/memory/{mem_id}", method="DELETE")
        self.assertEqual(s, 200)
        self.assertTrue(gone.get("success", True) is not False)

        s, _ = self._req(f"/api/user/memory/{mem_id}")
        self.assertEqual(s, 404)

    def test_user_memory_rejects_credentials(self) -> None:
        s, _ = self._req("/api/user/memory", method="POST", payload={
            "title": "bad", "content": "token sk-proj-abcdef", "memory_type": "general",
        })
        self.assertEqual(s, 400)

    # -- studio commands --------------------------------------------------
    def test_studio_command_crud_enable(self) -> None:
        s, c = self._req("/api/studio/commands", method="POST", payload={
            "name": "summarize", "description": "Summarize text",
            "instructions": "Summarize the selected text in three bullets.",
            "scope": "global",
        })
        self.assertIn(s, (200, 201))
        cmd_id = c["command"]["command_id"]

        s, got = self._req(f"/api/studio/commands/{cmd_id}")
        self.assertEqual(s, 200)
        self.assertEqual(got["command"]["name"], "summarize")

        s, disabled = self._req(f"/api/studio/commands/{cmd_id}/enable", method="POST", payload={"enabled": False})
        self.assertEqual(s, 200)
        self.assertFalse(disabled["command"]["enabled"])

        s, listed = self._req("/api/studio/commands")
        self.assertEqual(s, 200)
        self.assertTrue(any(x["command_id"] == cmd_id for x in listed["commands"]))

        s, _ = self._req(f"/api/studio/commands/{cmd_id}", method="DELETE")
        self.assertEqual(s, 200)
        s, _ = self._req(f"/api/studio/commands/{cmd_id}")
        self.assertEqual(s, 404)

    def test_studio_command_rejects_bad_name(self) -> None:
        s, _ = self._req("/api/studio/commands", method="POST", payload={
            "name": "has space!", "instructions": "x", "scope": "global",
        })
        self.assertEqual(s, 400)

    def test_studio_command_rejects_empty_name_and_secrets(self) -> None:
        s, _ = self._req("/api/studio/commands", method="POST", payload={
            "name": "///", "instructions": "x", "scope": "global",
        })
        self.assertEqual(s, 400)
        s, _ = self._req("/api/studio/commands", method="POST", payload={
            "name": "leaky", "instructions": "use token ghp_deadbeef", "scope": "global",
        })
        self.assertEqual(s, 400)

    # -- mcp servers ------------------------------------------------------
    def test_mcp_server_crud_refuses_remote(self) -> None:
        s, srv = self._req("/api/mcp/servers", method="POST", payload={
            "name": "local-echo", "command": "python", "args": ["-c", "pass"],
            "transport": "stdio",
        })
        self.assertIn(s, (200, 201))
        srv_id = srv["server"]["server_id"]

        s, got = self._req(f"/api/mcp/servers/{srv_id}")
        self.assertEqual(s, 200)
        self.assertEqual(got["server"]["transport"], "stdio")

        s, _ = self._req("/api/mcp/servers", method="POST", payload={
            "name": "remote", "command": "x", "transport": "sse",
        })
        self.assertEqual(s, 400)

        s, _ = self._req(f"/api/mcp/servers/{srv_id}", method="DELETE")
        self.assertEqual(s, 200)

    def test_mcp_probe_records_failure_honestly(self) -> None:
        s, srv = self._req("/api/mcp/servers", method="POST", payload={
            "name": "nope", "command": "amca-definitely-not-a-binary",
            "transport": "stdio",
        })
        self.assertIn(s, (200, 201))
        srv_id = srv["server"]["server_id"]

        s, probed = self._req(f"/api/mcp/servers/{srv_id}/probe", method="POST", payload={})
        self.assertEqual(s, 200)
        self.assertIsNotNone(probed["server"]["last_error"])
        # The failure must name the missing binary, not an UnboundLocalError
        # from the probe's own cleanup path.
        self.assertNotIn("UnboundLocal", probed["server"]["last_error"])

    def test_mcp_probe_happy_path_lists_tools(self) -> None:
        import sys
        import textwrap
        stub_path = Path(self.temporary.name) / "mcp_stub.py"
        stub_path.write_text(
            textwrap.dedent(
                """\
                import json
                import sys


                def emit(payload):
                    body = json.dumps(payload).encode("utf-8")
                    sys.stdout.buffer.write(
                        b"Content-Length: %d\\r\\n\\r\\n" % len(body) + body
                    )
                    sys.stdout.buffer.flush()


                # Answer initialize, then tools/list, then exit. Reads stdin
                # only to stay alive until the parent closes the pipe.
                emit({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}})
                emit({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "probe-tool", "description": "probe stub"}]}})
                try:
                    sys.stdin.read()
                except Exception:
                    pass
                """
            ),
            encoding="utf-8",
        )
        s, srv = self._req("/api/mcp/servers", method="POST", payload={
            "name": "stub", "command": sys.executable, "args": [str(stub_path)],
            "transport": "stdio",
        })
        self.assertIn(s, (200, 201))
        srv_id = srv["server"]["server_id"]

        s, probed = self._req(f"/api/mcp/servers/{srv_id}/probe", method="POST", payload={})
        self.assertEqual(s, 200)
        tools = probed["server"]["last_tools"] or []
        self.assertTrue(any(t["name"] == "probe-tool" for t in tools))
        self.assertIsNone(probed["server"]["last_error"])

    # -- run hooks --------------------------------------------------------
    def test_run_hook_crud_firings_dispatch(self) -> None:
        s, h = self._req("/api/run-hooks", method="POST", payload={
            "name": "notify done", "event": "run_completed",
            "action": "notify",
        })
        self.assertIn(s, (200, 201))
        hook_id = h["hook"]["hook_id"]

        s, listed = self._req("/api/run-hooks")
        self.assertEqual(s, 200)
        self.assertTrue(any(x["hook_id"] == hook_id for x in listed["hooks"]))

        # Dispatch directly against a store on the same DB file (there is no
        # public fire route by design — firing is runtime-driven, not UI-driven).
        from amca.run_hooks import RunHookStore
        fired = RunHookStore(self.database).dispatch(
            "run_completed", {"kind": "demo"}
        )
        self.assertEqual(fired, 1)

        s, firings = self._req("/api/run-hooks/firings")
        self.assertEqual(s, 200)
        self.assertTrue(any(f["hook_id"] == hook_id for f in firings["firings"]))

        s, _ = self._req(f"/api/run-hooks/{hook_id}", method="DELETE")
        self.assertEqual(s, 200)

    def test_run_started_dispatch_fires_hooks_with_sinks(self) -> None:
        s, h = self._req("/api/run-hooks", method="POST", payload={
            "name": "append on start", "event": "run_started",
            "action": "append-memory", "action_arg": "a run began",
        })
        self.assertIn(s, (200, 201))
        hook_id = h["hook"]["hook_id"]

        s, p = self._req("/api/studio-projects", method="POST", payload={
            "name": "Hook sink project", "template": "software",
        })
        self.assertIn(s, (200, 201))
        proj_id = p["project"]["project_id"]

        # Exercise the real service helper (same code path as
        # run_studio_workflow): dispatch + append-memory sink.
        context = self.server.amca_context
        fired = context._dispatch_run_hook("run_started", {
            "kind": "workflow", "workflow_id": "wf-test",
            "project_id": proj_id, "run_id": "run-test",
        })
        self.assertEqual(fired, 1)

        s, firings = self._req("/api/run-hooks/firings")
        self.assertEqual(s, 200)
        self.assertTrue(any(f["hook_id"] == hook_id for f in firings["firings"]))

        s, mems = self._req(f"/api/studio/memory?project_id={proj_id}")
        self.assertEqual(s, 200)
        self.assertTrue(any(m["content"] == "a run began" for m in mems["memories"]))

    def test_run_hook_rejects_bad_event(self) -> None:
        s, _ = self._req("/api/run-hooks", method="POST", payload={
            "name": "bad", "event": "on_everything", "action": "notify",
        })
        self.assertEqual(s, 400)

    def test_run_hook_rejects_non_object_match(self) -> None:
        s, _ = self._req("/api/run-hooks", method="POST", payload={
            "name": "bad", "event": "run_completed", "action": "notify",
            "match": ["not", "an", "object"],
        })
        self.assertEqual(s, 400)

    def test_run_hook_rejects_bad_body_types(self) -> None:
        s, _ = self._req("/api/mcp/servers", method="POST", payload={
            "name": "bad", "command": "x", "args": "not-a-list",
            "transport": "stdio",
        })
        self.assertEqual(s, 400)

    def test_get_clamps_bad_limits(self) -> None:
        s, listed = self._req("/api/user/memory?limit=abc")
        self.assertEqual(s, 200)
        s, listed = self._req("/api/user/memory?limit=-1")
        self.assertEqual(s, 200)
        self.assertLessEqual(len(listed["memories"]), 200)

    def test_delete_firings_path_is_not_a_hook(self) -> None:
        s, _ = self._req("/api/run-hooks/firings", method="DELETE")
        self.assertEqual(s, 404)

    # -- studio skills ----------------------------------------------------
    def test_studio_skill_crud_enable(self) -> None:
        s, sk = self._req("/api/studio/skills", method="POST", payload={
            "name": "Review checklist", "instructions": "Check inputs, outputs, edge cases.",
        })
        self.assertIn(s, (200, 201))
        skill_id = sk["skill"]["skill_id"]

        s, listed = self._req("/api/studio/skills")
        self.assertEqual(s, 200)
        self.assertTrue(any(x["skill_id"] == skill_id for x in listed["skills"]))

        s, disabled = self._req(f"/api/studio/skills/{skill_id}/enable", method="POST", payload={"enabled": False})
        self.assertEqual(s, 200)
        self.assertFalse(disabled["skill"]["enabled"])

        s, _ = self._req(f"/api/studio/skills/{skill_id}", method="DELETE")
        self.assertEqual(s, 200)
        s, _ = self._req(f"/api/studio/skills/{skill_id}")
        self.assertEqual(s, 404)


if __name__ == "__main__":
    unittest.main()
