"""Round 26 route-level regression tests for the panel workspaces.

The Round 24/25 lesson: new POST routes MUST also be added to the do_POST
allowlist guard, or every authenticated call 404s. These tests drive the
real HTTP handler so that gap can never silently return.
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from amca.service import create_server

from amca.credentials import MemoryCredentialStore


class PanelRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "state.sqlite3"
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=self.database,
            credential_store=MemoryCredentialStore(),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.server.amca_context.session_token

    def tearDown(self) -> None:
        self.server.server_close()
        self.temporary.cleanup()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = None if body is None else json.dumps(body).encode()
        headers = {"X-AMCA-Session": self.token}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return response.status, parsed

    def test_files_list_over_http_uses_project_root_path(self) -> None:
        context = self.server.amca_context
        project_id = "route-test-project"
        real = Path(self.temporary.name) / "real-folder"
        real.mkdir()
        (real / "hello.txt").write_text("route level", encoding="utf-8")
        context.save_studio_project(
            {
                "project_id": project_id,
                "name": "RouteTest",
                "root_path": str(real),
            }
        )
        status, body = self.request(
            "POST", "/api/panel/files/list", {"project_id": project_id, "path": ""}
        )
        record = self.server.amca_context.project(project_id)
        print("RECORD:", record)
        self.assertEqual(status, 200)
        self.assertTrue(Path(body["root"]).samefile(real))
        self.assertIn("hello.txt", [entry["name"] for entry in body["entries"]])

    def test_files_list_accepts_root_override(self) -> None:
        override = Path(self.temporary.name) / "override-folder"
        override.mkdir()
        (override / "other.txt").write_text("override", encoding="utf-8")
        status, body = self.request(
            "POST",
            "/api/panel/files/list",
            {"project_id": "route-test-project", "path": "", "root": str(override)},
        )
        self.assertEqual(status, 200)
        self.assertTrue(Path(body["root"]).samefile(override))

    def test_browser_fetch_renders_page_and_refuses_non_http(self) -> None:
        # Serve a tiny local page so the test stays loopback-only.
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class TinyPage(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><head><title>t</title></head><body>"
                    b"<script>alert(1)</script><h1 id='ok'>route-ok</h1></body></html>"
                )

            def log_message(self, *args) -> None:
                pass

        tiny = HTTPServer(("127.0.0.1", 0), TinyPage)
        tiny_thread = threading.Thread(target=tiny.serve_forever, daemon=True)
        tiny_thread.start()
        try:
            url = f"http://127.0.0.1:{tiny.server_address[1]}/page"
            status, body = self.request(
                "POST", "/api/panel/browser/fetch", {"url": url}
            )
            self.assertEqual(status, 200)
            self.assertIn("<base href=", body["html"])
            self.assertNotIn("<script", body["html"].lower())
            self.assertIn("route-ok", body["html"])
            status, body = self.request(
                "POST", "/api/panel/browser/fetch", {"url": "file:///etc/passwd"}
            )
            self.assertEqual(status, 400)
            status, body = self.request(
                "POST", "/api/panel/browser/fetch", {"url": "ftp://example.invalid"}
            )
            self.assertEqual(status, 400)
        finally:
            tiny.shutdown()

    def test_unknown_panel_post_route_still_404s(self) -> None:
        # The allowlist guard must keep refusing unregistered POST paths.
        status, _ = self.request(
            "POST", "/api/panel/unknown-route", {"project_id": "x"}
        )
        self.assertEqual(status, 404)

    def test_panel_base_root_resolution_modes(self) -> None:
        context = self.server.amca_context
        project_id = "root-modes"
        real = Path(self.temporary.name) / "mode-folder"
        real.mkdir()
        context.save_studio_project(
            {
                "project_id": project_id,
                "name": "RootModes",
                "root_path": str(real),
            }
        )
        # Default: the project's real folder.
        resolved = context._panel_base_root(project_id, None)
        self.assertTrue(resolved.samefile(real))
        # Operator override wins.
        other = Path(self.temporary.name) / "other-folder"
        other.mkdir()
        self.assertTrue(context._panel_base_root(project_id, str(other)).samefile(other))
        # Non-absolute and missing overrides are refused.
        with self.assertRaisesRegex(ValueError, "absolute"):
            context._panel_base_root(project_id, "relative/path")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            context._panel_base_root(project_id, str(real / "missing"))
        # A root_path that no longer exists on disk falls through to the
        # workspace (the store refuses file-path roots at save time, so a
        # vanished folder exercises the same non-directory branch).
        gone_project = "root-vanished"
        gone = Path(self.temporary.name) / "vanishes-later"
        gone.mkdir()
        context.save_studio_project(
            {
                "project_id": gone_project,
                "name": "RootVanished",
                "root_path": str(gone),
            }
        )
        gone.rmdir()
        workspace = context.workspace_store.project_root(gone_project)
        self.assertEqual(context._panel_base_root(gone_project, None), workspace)


class MetaProviderTests(unittest.TestCase):
    """Round 29: the Meta 401 report.

    A provider with zero stored credentials must be reported "missing API
    key" WITHOUT a network probe (a bare 401 from a login wall is not
    evidence of a wrong key), and the Meta endpoint/api-format must resolve
    from the registry by default.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=Path(self.temporary.name) / "state.sqlite3",
            credential_store=MemoryCredentialStore(),
        )
        self.context = self.server.amca_context

    def tearDown(self) -> None:
        self.server.server_close()
        self.temporary.cleanup()

    def test_meta_endpoint_defaults_from_registry(self) -> None:
        outcome = self.context.create_hub_provider(
            {
                "provider_kind": "meta",
                "name": "Meta",
                "model_ids": ["muse-wonder-1.0", "muse-spark-1.3"],
            }
        )
        provider = outcome["provider"]
        self.assertFalse(provider["configured"])
        self.assertEqual(provider["endpoint"], "https://api.meta.ai/v1")
        self.assertEqual(provider["name"], "Meta")

    def test_verify_without_credential_is_unverified_not_401(self) -> None:
        outcome = self.context.create_hub_provider(
            {
                "provider_kind": "meta",
                "name": "Meta",
                "model_ids": ["muse-wonder-1.0"],
            }
        )
        provider_id = outcome["provider"]["id"]
        record = self.context.verify_model_configuration(
            kind="hub",
            ref=f"{provider_id}:muse-wonder-1.0",
            endpoint="https://api.meta.ai/v1",
            model="muse-wonder-1.0",
        )
        self.assertEqual(record["state"].upper(), "UNVERIFIED")
        self.assertIn("missing API key", record["detail"])
        # It is classified from the missing credential alone, not from a
        # probe that bounced off a login wall.
        self.assertNotIn("401", record["detail"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
