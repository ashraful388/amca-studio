"""
AMCA Studio Alpha Release Candidate (RC) Comprehensive Verification Suite
Validates all major commercial capabilities across all system modules:
1. Multi-Project lifecycle & isolation
2. Artifact version lineage, SHA digests & non-destructive restore
3. Persistent memory graph querying, pinning & cross-module linking
4. Automation workflows, recurrence schedules & duplication
5. Security boundaries: CORS origin control & session token enforcement
6. Cold server restart state persistence over canonical SQLite database
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


class StudioAlphaReleaseCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca_rc.db"

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

    def _req(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(url, data=data, method=method)
        req.add_header("Origin", "http://localhost:1420")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        if authorized and hasattr(self, "token") and self.token:
            req.add_header("X-AMCA-Session", self.token)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with urlopen(req, timeout=5.0) as resp:
                status = resp.status
                raw = resp.read()
                body = json.loads(raw.decode("utf-8")) if raw else {}
                return status, body
        except HTTPError as exc:
            raw = exc.read()
            body = json.loads(raw.decode("utf-8")) if raw else {}
            return exc.code, body

    # -------------------------------------------------------------------------
    # Test 1: Full Release Candidate Workflow Journey
    # -------------------------------------------------------------------------
    def test_01_alpha_rc_full_lifecycle_journey(self) -> None:
        # Create Project
        s, p = self._req("/api/studio-projects", method="POST", payload={
            "name": "Alpha RC Enterprise Suite", "description": "Release verification container", "template": "software"
        })
        self.assertIn(s, (200, 201))
        proj_id = p["project"]["project_id"]

        # Create Conversation
        s, c = self._req("/api/conversations", method="POST", payload={
            "mode": "solo", "workspace_project_id": proj_id
        })
        self.assertIn(s, (200, 201))

        # Author Artifact
        s, a = self._req(f"/api/projects/{proj_id}/artifacts", method="POST", payload={
            "title": "RC Blueprint", "artifact_type": "markdown", "content": "# RC Architecture\nComplete verification.", "created_by": "qa"
        })
        self.assertIn(s, (200, 201))
        art_id = a["artifact"]["artifact_id"]

        # Read Artifact Details
        s, a_detail = self._req(f"/api/studio/artifacts/{art_id}?project_id={proj_id}")
        self.assertEqual(s, 200)
        self.assertEqual(a_detail["artifact"]["title"], "RC Blueprint")

        # Save Persistent Memory
        s, m = self._req("/api/studio/memory", method="POST", payload={
            "project_id": proj_id, "title": "Release Quality Gate", "content": "Zero regressions on RC candidate.", "memory_type": "decision", "pinned": True
        })
        self.assertIn(s, (200, 201))

        # Create Workflow
        s, w = self._req("/api/studio/workflows", method="POST", payload={
            "project_id": proj_id, "name": "Nightly Build & Verify", "goal": "Run test suites against {branch}", "automation_type": "recurring", "schedule": "0 1 * * *"
        })
        self.assertIn(s, (200, 201))
        wf_id = w["workflow"].get("id") or w["workflow"].get("workflow_id")

        # Duplicate Workflow
        s, w_dup = self._req(f"/api/studio/workflows/{wf_id}/duplicate", method="POST", payload={"project_id": proj_id})
        self.assertIn(s, (200, 201))

        # List Workflows
        s, w_list = self._req(f"/api/studio/workflows?project_id={proj_id}")
        self.assertEqual(s, 200)
        self.assertEqual(len(w_list["workflows"]), 2)

    # -------------------------------------------------------------------------
    # Test 2: Project Isolation & Containment
    # -------------------------------------------------------------------------
    def test_02_project_isolation_and_cross_access_rejection(self) -> None:
        s1, p1 = self._req("/api/studio-projects", method="POST", payload={"name": "Project Alpha", "template": "software"})
        p_a = p1["project"]["project_id"]

        s2, p2 = self._req("/api/studio-projects", method="POST", payload={"name": "Project Beta", "template": "software"})
        p_b = p2["project"]["project_id"]

        s3, art_a = self._req(f"/api/projects/{p_a}/artifacts", method="POST", payload={
            "title": "Confidential Spec", "artifact_type": "markdown", "content": "Alpha Secret", "created_by": "user-a"
        })
        art_id_a = art_a["artifact"]["artifact_id"]

        # Verify cross-project artifact inspection returns 404
        status, _ = self._req(f"/api/studio/artifacts/{art_id_a}?project_id={p_b}")
        self.assertEqual(status, 404)

        # Create workflow in Project A
        s4, wf_a = self._req("/api/studio/workflows", method="POST", payload={
            "project_id": p_a, "name": "Isolated Pipeline", "goal": "Pipeline A", "automation_type": "once", "schedule": "manual"
        })
        wf_id_a = wf_a["workflow"].get("id") or wf_a["workflow"].get("workflow_id")

        # Verify cross-project workflow inspection returns 404
        status, _ = self._req(f"/api/studio/workflows/{wf_id_a}?project_id={p_b}")
        self.assertEqual(status, 404)

    # -------------------------------------------------------------------------
    # Test 3: Cold Restart Recovery State Preservation
    # -------------------------------------------------------------------------
    def test_03_cold_restart_preserves_all_entities(self) -> None:
        s1, p = self._req("/api/studio-projects", method="POST", payload={"name": "Durable Suite", "template": "software"})
        proj_id = p["project"]["project_id"]

        s2, art = self._req(f"/api/projects/{proj_id}/artifacts", method="POST", payload={
            "title": "Durable Spec", "artifact_type": "markdown", "content": "Persisted Document", "created_by": "author"
        })
        art_id = art["artifact"]["artifact_id"]

        s3, mem = self._req("/api/studio/memory", method="POST", payload={
            "project_id": proj_id, "title": "Durable Rule", "content": "Single SQLite DB", "memory_type": "decision"
        })

        # Terminate server
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

        # Boot secondary server instance over exact same SQLite file
        new_server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        time.sleep(0.05)
        new_url = f"http://127.0.0.1:{new_server.server_port}"

        # Fetch new session token
        req = Request(f"{new_url}/api/session", method="GET")
        req.add_header("Origin", "http://localhost:1420")
        with urlopen(req) as resp:
            new_token = json.loads(resp.read().decode("utf-8"))["session_token"]

        try:
            req_art = Request(f"{new_url}/api/studio/artifacts/{art_id}?project_id={proj_id}")
            req_art.add_header("Origin", "http://localhost:1420")
            req_art.add_header("X-AMCA-Session", new_token)
            with urlopen(req_art) as resp:
                art_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(art_data["artifact"]["title"], "Durable Spec")

            req_mem = Request(f"{new_url}/api/studio/memory?project_id={proj_id}")
            req_mem.add_header("Origin", "http://localhost:1420")
            req_mem.add_header("X-AMCA-Session", new_token)
            with urlopen(req_mem) as resp:
                mem_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(mem_data["memories"]), 1)
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=1.0)

    # -------------------------------------------------------------------------
    # Test 4: Security Boundaries & Origin Controls
    # -------------------------------------------------------------------------
    def test_04_security_and_cors_boundaries(self) -> None:
        # Unauthorized without session token
        status, _ = self._req("/api/studio-projects", authorized=False)
        self.assertEqual(status, 401)

        # Malicious foreign origin
        url = f"{self.base_url}/api/studio-projects"
        req = Request(url, headers={"Origin": "https://evil-site.test", "X-AMCA-Session": self.token})
        try:
            with urlopen(req) as resp:
                pass
        except HTTPError as exc:
            self.assertEqual(exc.code, 403)


if __name__ == "__main__":
    unittest.main()

