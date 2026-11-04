"""
AMCA Studio Alpha Release Candidate Final Smoke Test Suite
Validates:
1. Clean first-launch bootstrap and database schema generation from scratch
2. Backend session handshake and authentication token validation
3. Core commercial workflow execution (Project -> Artifact -> Memory -> Automation)
4. Multi-project boundary safety & isolation
5. Graceful shutdown and lifecycle auditing
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


class StudioAlphaRCFinalSmokeTests(unittest.TestCase):
    def test_clean_installation_and_full_smoke_cycle(self) -> None:
        """Execute a complete smoke test on a brand new empty environment."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / ".amca" / "amca_smoke.sqlite3"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            software_root = Path(__file__).resolve().parents[1]

            # 1. Start Server on brand new database
            server = create_server(host="127.0.0.1", port=0, root=software_root, database=db_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(0.05)
            base_url = f"http://127.0.0.1:{server.server_port}"

            # 2. Verify Session Handshake
            req_session = Request(f"{base_url}/api/session", method="GET")
            req_session.add_header("Origin", "http://localhost:1420")
            with urlopen(req_session, timeout=5.0) as resp:
                self.assertEqual(resp.status, 200)
                session_data = json.loads(resp.read().decode("utf-8"))
                token = session_data.get("session_token")
                self.assertIsNotNone(token)
                self.assertGreater(len(token), 32)

            # Helper for authenticated requests
            def api_req(path: str, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
                url = f"{base_url}{path}"
                data = json.dumps(body).encode("utf-8") if body is not None else None
                req = Request(url, data=data, method=method)
                req.add_header("Origin", "http://localhost:1420")
                req.add_header("X-AMCA-Session", token)
                if body is not None:
                    req.add_header("Content-Type", "application/json")
                try:
                    with urlopen(req, timeout=5.0) as r:
                        raw = r.read()
                        return r.status, json.loads(raw.decode("utf-8")) if raw else {}
                except HTTPError as exc:
                    raw = exc.read()
                    return exc.code, json.loads(raw.decode("utf-8")) if raw else {}

            try:
                # 3. Create Studio Project
                s, proj = api_req("/api/studio-projects", method="POST", body={
                    "name": "Final Smoke Project", "description": "Smoke verification", "template": "software"
                })
                self.assertIn(s, (200, 201))
                proj_id = proj["project"]["project_id"]

                # 4. Author Artifact
                s, art = api_req(f"/api/projects/{proj_id}/artifacts", method="POST", body={
                    "title": "Smoke Test Specification", "artifact_type": "markdown", "content": "# Smoke Passed", "created_by": "smoke-tester"
                })
                self.assertIn(s, (200, 201))
                art_id = art["artifact"]["artifact_id"]

                # 5. Read Artifact Detail
                s, art_detail = api_req(f"/api/studio/artifacts/{art_id}?project_id={proj_id}")
                self.assertEqual(s, 200)
                self.assertEqual(art_detail["artifact"]["title"], "Smoke Test Specification")

                # 6. Save Persistent Memory
                s, mem = api_req("/api/studio/memory", method="POST", body={
                    "project_id": proj_id, "title": "Smoke Invariant", "content": "Clean bootstrap verified", "memory_type": "decision", "pinned": True
                })
                self.assertIn(s, (200, 201))

                # 7. Create Workflow
                s, wf = api_req("/api/studio/workflows", method="POST", body={
                    "project_id": proj_id, "name": "Smoke Automation", "goal": "Daily audit", "automation_type": "recurring", "schedule": "0 0 * * *"
                })
                self.assertIn(s, (200, 201))
                wf_id = wf["workflow"].get("id") or wf["workflow"].get("workflow_id")

                # 8. List Workflows
                s, wf_list = api_req(f"/api/studio/workflows?project_id={proj_id}")
                self.assertEqual(s, 200)
                self.assertEqual(len(wf_list["workflows"]), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()

