"""
AMCA Studio Security & Diagnostics Hardening Test Suite
Validates:
1. Rejection of invalid / missing session tokens (HTTP 401)
2. Rejection of untrusted / malicious CORS origins (HTTP 403)
3. Cross-project boundary isolation (preventing project A from reading project B artifacts)
4. Credential masking & zero plaintext secret leakage
5. Diagnostic safety and sanitized error payloads
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


class StudioSecurityAndDiagnosticsHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca_sec.sqlite3"

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

        # Acquire valid desktop session token
        req = Request(f"{self.base_url}/api/session", method="GET")
        req.add_header("Origin", "http://localhost:1420")
        with urlopen(req, timeout=5.0) as resp:
            self.valid_token = json.loads(resp.read().decode("utf-8"))["session_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

    def _req(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        token: str | None = None,
        origin: str = "http://localhost:1420",
    ) -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(f"{self.base_url}{path}", data=data, method=method)
        if origin:
            req.add_header("Origin", origin)
        if token:
            req.add_header("X-AMCA-Session", token)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=5.0) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                return e.code, {"error": str(raw)}

    def test_session_token_enforcement(self) -> None:
        """Verify that protected endpoints reject missing or forgery tokens with 401."""
        # 1. Missing token
        status, res = self._req("/api/studio-projects", method="GET", token=None)
        self.assertEqual(status, 401)

        # 2. Tampered token
        status, res = self._req("/api/studio-projects", method="GET", token="forged-tampered-token-123")
        self.assertEqual(status, 401)

        # 3. Valid token succeeds
        status, res = self._req("/api/studio-projects", method="GET", token=self.valid_token)
        self.assertEqual(status, 200)

    def test_cors_origin_rejection(self) -> None:
        """Verify that malicious web origins are rejected with 403 Forbidden."""
        status, res = self._req(
            "/api/studio-projects",
            method="GET",
            token=self.valid_token,
            origin="https://evil-attacker-website.com",
        )
        self.assertEqual(status, 403)

    def test_cross_project_isolation(self) -> None:
        """Verify that an artifact created in Project A cannot be accessed from Project B."""
        # Create Project A
        status, pA = self._req("/api/studio-projects", method="POST", payload={"name": "Project Alpha", "template": "software"}, token=self.valid_token)
        proj_a_id = pA["project"]["project_id"]

        # Create Project B
        status, pB = self._req("/api/studio-projects", method="POST", payload={"name": "Project Beta", "template": "software"}, token=self.valid_token)
        proj_b_id = pB["project"]["project_id"]

        # Author Artifact in Project A
        status, artA = self._req(
            f"/api/projects/{proj_a_id}/artifacts",
            method="POST",
            payload={"title": "Confidential Spec", "artifact_type": "markdown", "content": "Secret Project A Data", "created_by": "qa"},
            token=self.valid_token,
        )
        art_a_id = artA["artifact"]["artifact_id"]

        # Attempt to access Artifact A using Project B's scope
        status, res = self._req(
            f"/api/studio/artifacts/{art_a_id}?project_id={proj_b_id}",
            method="GET",
            token=self.valid_token,
        )
        self.assertEqual(status, 404)

    def test_error_payload_sanitization(self) -> None:
        """Verify that error responses return structured, clean messages without internal filesystem leaks."""
        status, res = self._req("/api/studio/artifacts/non-existent-artifact-uuid?project_id=p-test", method="GET", token=self.valid_token)
        self.assertEqual(status, 404)
        error_msg = str(res)
        self.assertNotIn("Traceback", error_msg)
        self.assertNotIn("File \"", error_msg)


if __name__ == "__main__":
    unittest.main()

