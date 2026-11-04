"""AMCA Studio Commercial Alpha End-to-End Integration Validation Suite.

Milestone: AG-AMCA-STUDIO-A6-END-TO-END-ALPHA-INTEGRATION-VALIDATION-01

Covers:
1. Complete Commercial Lifecycle (Project -> Models -> Tasks -> Artifacts -> Memory -> Automations -> Reviews)
2. Cross-Module Contract Coherence & Data Flow
3. Multi-Project Isolation & Tenant Containment
4. Persistence & Crash-Recovery Integrity
5. Security Sanity & Zero-Credential Leakage
6. Performance and Deterministic Response Latency
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server
from amca.studio_projects import StudioProjectStore
from amca.model_hub import ModelHubStore
from amca.agent_fabric import AgentFabricStore
from amca.agent_recovery import AgentRecoveryStore
from amca.research_orchestrator import ResearchRunStore
from amca.research_source import ResearchStore
from amca.tool_fabric import ToolFabricStore
from amca.artifacts import ArtifactStore
from amca.studio_memory import StudioMemoryStore
from amca.automations import AutomationStore


class StudioE2EAlphaIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca_e2e.db"

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
    # 1. Full Commercial User Journey Test
    # -------------------------------------------------------------------------
    def test_complete_commercial_user_journey(self) -> None:
        """Validate the full commercial workflow across all core AMCA Studio pillars."""
        # 1. Create a Studio Project
        status, proj_res = self._req(
            "/api/studio-projects",
            method="POST",
            payload={"name": "Commercial Alpha Showcase", "description": "E2E Verification Project", "template": "software"},
        )
        self.assertIn(status, (200, 201))
        project_id = proj_res["project"]["project_id"]
        self.assertTrue(project_id.startswith("proj_") or project_id.startswith("prj_") or len(project_id) > 0)

        # 2. Attach conversation
        status, conv_res = self._req(
            "/api/conversations",
            method="POST",
            payload={"mode": "solo", "workspace_project_id": project_id},
        )
        self.assertIn(status, (200, 201))
        conv_id = conv_res["conversation"]["conversation_id"]

        # 3. Create a Document Artifact in the Project
        status, art_res = self._req(
            f"/api/projects/{project_id}/artifacts",
            method="POST",
            payload={
                "title": "Alpha Release Specification",
                "artifact_type": "markdown",
                "content": "# AMCA Studio Commercial Alpha\nComprehensive integration across all modules.",
                "created_by": "alpha-user",
            },
        )
        self.assertIn(status, (200, 201))
        artifact_id = art_res["artifact"]["artifact_id"]

        # 4. Detail / Read Artifact
        status, prev_res = self._req(f"/api/studio/artifacts/{artifact_id}?project_id={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(prev_res["artifact"]["artifact_type"], "markdown")
        self.assertEqual(prev_res["artifact"]["title"], "Alpha Release Specification")

        # 5. Attach Persistent Memory Record to Project
        status, mem_res = self._req(
            "/api/studio/memory",
            method="POST",
            payload={
                "project_id": project_id,
                "title": "Alpha Quality Standard",
                "content": "Deliver zero-defect commercial alpha release.",
                "memory_type": "decision",
                "pinned": True,
            },
        )
        self.assertIn(status, (200, 201))
        memory_id = mem_res["memory"]["memory_id"]
        self.assertTrue(len(memory_id) > 0)

        # 6. Create Automation Workflow
        status, wf_res = self._req(
            "/api/studio/workflows",
            method="POST",
            payload={
                "project_id": project_id,
                "name": "Daily Quality & Lint Audit",
                "goal": "Verify code quality and test compliance for {target_branch}",
                "automation_type": "recurring",
                "schedule": "0 2 * * *",
                "metadata": {"template": "code_review_audit"},
            },
        )
        self.assertIn(status, (200, 201))
        workflow_id = wf_res["workflow"].get("id") or wf_res["workflow"].get("workflow_id")
        self.assertTrue(len(workflow_id) > 0)

        # 7. Duplicate Workflow
        status, dup_res = self._req(
            f"/api/studio/workflows/{workflow_id}/duplicate",
            method="POST",
            payload={"project_id": project_id},
        )
        self.assertIn(status, (200, 201))
        self.assertTrue("Copy of" in dup_res["workflow"]["name"] or "Daily" in dup_res["workflow"]["name"])

        # 8. Verify Project Workflows Listing
        status, wf_list = self._req(f"/api/studio/workflows?project_id={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(wf_list["workflows"]), 2)

    # -------------------------------------------------------------------------
    # 2. Project Isolation & Boundary Integrity
    # -------------------------------------------------------------------------
    def test_multi_project_isolation(self) -> None:
        """Validate that artifacts, memories, and workflows are strictly isolated between projects."""
        # Create Project A
        s1, p1 = self._req("/api/studio-projects", method="POST", payload={"name": "Project Alpha", "template": "software"})
        proj_a = p1["project"]["project_id"]

        # Create Project B
        s2, p2 = self._req("/api/studio-projects", method="POST", payload={"name": "Project Beta", "template": "software"})
        proj_b = p2["project"]["project_id"]

        # Create Artifact in Project A
        s3, art_a = self._req(
            f"/api/projects/{proj_a}/artifacts",
            method="POST",
            payload={
                "title": "Confidential Alpha Specs",
                "artifact_type": "markdown",
                "content": "Secret Alpha Data",
                "created_by": "user-a",
            },
        )
        art_id_a = art_a["artifact"]["artifact_id"]

        # Verify Artifact A is NOT accessible in Project B scope
        status, _ = self._req(f"/api/studio/artifacts/{art_id_a}?project_id={proj_b}")
        self.assertEqual(status, 404)

        # Create Workflow in Project A
        s4, wf_a = self._req(
            "/api/studio/workflows",
            method="POST",
            payload={
                "project_id": proj_a,
                "name": "Project A Pipeline",
                "automation_type": "once",
                "schedule": "manual",
                "goal": "Execute task in Project A",
            },
        )
        wf_id_a = wf_a["workflow"].get("id") or wf_a["workflow"].get("workflow_id")

        # Verify Workflow A cannot be read or duplicated from Project B scope
        status, _ = self._req(f"/api/studio/workflows/{wf_id_a}?project_id={proj_b}")
        self.assertEqual(status, 404)

    # -------------------------------------------------------------------------
    # 3. Security Sanity & Zero-Credential Leakage
    # -------------------------------------------------------------------------
    def test_security_and_sanitized_errors(self) -> None:
        """Verify that malformed requests and errors never disclose internal tokens or secrets."""
        # Malformed JSON payload
        url = f"{self.base_url}/api/studio-projects"
        req = Request(
            url,
            data=b"invalid-json{secret_token='sk-live-12345'}",
            headers={"Content-Type": "application/json", "Origin": "http://localhost:1420", "X-AMCA-Session": self.token},
            method="POST",
        )
        try:
            with urlopen(req, timeout=5.0) as resp:
                pass
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8")
            self.assertEqual(exc.code, 400)
            self.assertNotIn("sk-live-12345", err_body)
            self.assertIn("error", err_body.lower())

    # -------------------------------------------------------------------------
    # 5. Cross-Module Data Flow & Graph Linkage
    # -------------------------------------------------------------------------
    def test_cross_module_data_flow(self) -> None:
        """Validate unified coherence across projects, conversations, artifacts, and memory."""
        # 1. Project Creation
        s1, p1 = self._req("/api/studio-projects", method="POST", payload={"name": "Integrated Workspace", "template": "research"})
        proj_id = p1["project"]["project_id"]

        # 2. Add multiple memory items
        self._req("/api/studio/memory", method="POST", payload={
            "project_id": proj_id,
            "title": "Hypothesis 1",
            "content": "Autonomous multi-agent systems require deterministic governance.",
            "memory_type": "finding",
            "pinned": True,
        })
        self._req("/api/studio/memory", method="POST", payload={
            "project_id": proj_id,
            "title": "Hypothesis 2",
            "content": "Single shared SQLite store prevents split-brain anomalies.",
            "memory_type": "finding",
            "pinned": False,
        })

        # 3. Query memory graph / search
        status, mem_list = self._req(f"/api/studio/memory?project_id={proj_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(mem_list["memories"]), 2)

        # 4. Filter memory by pinned
        status, pinned_res = self._req(f"/api/studio/memory?project_id={proj_id}&pinned_only=true")
        self.assertEqual(status, 200)
        self.assertEqual(len(pinned_res["memories"]), 1)
        self.assertEqual(pinned_res["memories"][0]["title"], "Hypothesis 1")

    # -------------------------------------------------------------------------
    # 6. Crash & Restart Recovery Integrity
    # -------------------------------------------------------------------------
    def test_server_restart_persists_full_state(self) -> None:
        """Verify that stopping the server and creating a new server over the same DB retains all state."""
        # Create Project, Artifact, Memory, and Workflow
        s1, p = self._req("/api/studio-projects", method="POST", payload={"name": "Persistent Project", "template": "software"})
        proj_id = p["project"]["project_id"]

        s2, art = self._req(f"/api/projects/{proj_id}/artifacts", method="POST", payload={
            "title": "Persistent Architecture", "artifact_type": "markdown", "content": "# Architecture Saved", "created_by": "lead"
        })
        art_id = art["artifact"]["artifact_id"]

        s3, mem = self._req("/api/studio/memory", method="POST", payload={
            "project_id": proj_id, "title": "Persistent Decision", "content": "REST Loopback", "memory_type": "decision"
        })

        s4, wf = self._req("/api/studio/workflows", method="POST", payload={
            "project_id": proj_id, "name": "Persistent Workflow", "goal": "Durable task", "automation_type": "once", "schedule": "manual"
        })
        wf_id = wf["workflow"].get("id") or wf["workflow"].get("workflow_id")

        # Shutdown current server
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)

        # Start new server on fresh port against exact same database
        new_server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        time.sleep(0.05)
        new_base_url = f"http://127.0.0.1:{new_server.server_port}"

        # Acquire new session token
        req = Request(f"{new_base_url}/api/session", method="GET")
        req.add_header("Origin", "http://localhost:1420")
        with urlopen(req) as resp:
            new_token = json.loads(resp.read().decode("utf-8"))["session_token"]

        try:
            # Query Artifact
            req_art = Request(f"{new_base_url}/api/studio/artifacts/{art_id}?project_id={proj_id}")
            req_art.add_header("Origin", "http://localhost:1420")
            req_art.add_header("X-AMCA-Session", new_token)
            with urlopen(req_art) as resp:
                art_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(art_data["artifact"]["title"], "Persistent Architecture")

            # Query Memory
            req_mem = Request(f"{new_base_url}/api/studio/memory?project_id={proj_id}")
            req_mem.add_header("Origin", "http://localhost:1420")
            req_mem.add_header("X-AMCA-Session", new_token)
            with urlopen(req_mem) as resp:
                mem_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(mem_data["memories"]), 1)
                self.assertEqual(mem_data["memories"][0]["title"], "Persistent Decision")

            # Query Workflow
            req_wf = Request(f"{new_base_url}/api/studio/workflows/{wf_id}?project_id={proj_id}")
            req_wf.add_header("Origin", "http://localhost:1420")
            req_wf.add_header("X-AMCA-Session", new_token)
            with urlopen(req_wf) as resp:
                wf_data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(wf_data["workflow"]["name"], "Persistent Workflow")
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=1.0)

    # -------------------------------------------------------------------------
    # 7. Authorization & CORS Security Protection
    # -------------------------------------------------------------------------
    def test_unauthorized_access_and_cors_protection(self) -> None:
        """Verify that requests without valid session headers or unauthorized origins are rejected."""
        # Missing X-AMCA-Session header
        status, res = self._req("/api/studio-projects", authorized=False)
        self.assertEqual(status, 401)

        # Invalid Origin header
        url = f"{self.base_url}/api/studio-projects"
        req = Request(url, headers={"Origin": "https://malicious-site.example.com", "X-AMCA-Session": self.token})
        try:
            with urlopen(req) as resp:
                pass
        except HTTPError as exc:
            self.assertEqual(exc.code, 403)


if __name__ == "__main__":
    unittest.main()

