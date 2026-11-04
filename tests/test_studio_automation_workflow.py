"""Validation suite for AMCA Studio Automation & Workflow Foundation.

Task: AG-AMCA-STUDIO-A4-AUTOMATION-WORKFLOW-FOUNDATION-01

Domains Validated:
1. Workflow Definition & Persistence (Canonical SQLite store, steps, templates, revision tracking)
2. Project Isolation & Workspace Scope (Strict project-scoped reads, 404 cross-project isolation)
3. Duplication & Templating (Workflow cloning, parameter placeholder substitution, template tags)
4. Execution Mapping & Runtime Governance (Single execution path via start_chat_run, occurrence tracking)
5. Recovery, Continuity & Concurrency (Server restarts, occurrence claims reconciliation, concurrent store instances)
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
from amca.automations import AutomationStore


class StudioAutomationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        
        # Initialize stores
        self.project_store = StudioProjectStore(self.database)
        self.automation_store = AutomationStore(self.database)

        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=self.root,
            database=self.database,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        
        session = self._request("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        try:
            self.temporary.cleanup()
        except Exception:
            pass

    def _request(self, path: str, method: str = "GET", body: dict | None = None, *, authorized: bool = True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(self.base + path, data=data, method=method)
        req.add_header("Origin", "http://localhost:1420")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if authorized:
            req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw.decode("utf-8", errors="replace")}
            return exc.code, parsed

    def _seed_project(self, project_id: str = "proj-wf-01") -> str:
        self.project_store.save({
            "project_id": project_id,
            "name": f"Workflow Project {project_id}",
            "description": "Container for workflow testing",
            "instructions": "Manage project workflows cleanly",
            "template": "software"
        })
        return project_id

    # =========================================================================
    # Group 1: Workflow Definition & Persistence (Tests 1-6)
    # =========================================================================

    def test_01_save_workflow_via_api(self) -> None:
        self._seed_project("p-wf-save")
        status, resp = self._request("/api/studio/workflows", method="POST", body={
            "project_id": "p-wf-save",
            "name": "Daily Code Review",
            "goal": "Review recent pull requests and summarize risk",
            "automation_type": "recurring",
            "schedule": "0 9 * * 1-5",
            "metadata": {
                "template": "code_review",
                "steps": ["fetch_diffs", "run_linter", "generate_summary"]
            }
        })
        self.assertEqual(status, 201)
        wf = resp["workflow"]
        self.assertEqual(wf["name"], "Daily Code Review")
        self.assertEqual(wf["project_id"], "p-wf-save")
        self.assertEqual(wf["automation_type"], "recurring")
        self.assertEqual(wf["metadata"]["template"], "code_review")

    def test_02_get_workflow_by_id(self) -> None:
        self._seed_project("p-wf-get")
        saved = self.automation_store.save({
            "project_id": "p-wf-get",
            "name": "Audit Task",
            "goal": "Audit project dependencies",
            "automation_type": "once",
            "schedule": "manual"
        })
        status, resp = self._request(f"/api/studio/workflows/{saved['id']}?project_id=p-wf-get")
        self.assertEqual(status, 200)
        wf = resp["workflow"]
        self.assertEqual(wf["id"], saved["id"])
        self.assertEqual(wf["name"], "Audit Task")

    def test_03_update_workflow_definition(self) -> None:
        self._seed_project("p-wf-upd")
        saved = self.automation_store.save({
            "project_id": "p-wf-upd",
            "name": "Initial Name",
            "goal": "Initial Goal",
            "automation_type": "once",
            "schedule": "manual"
        })
        status, resp = self._request("/api/studio/workflows", method="POST", body={
            "workflow_id": saved["id"],
            "project_id": "p-wf-upd",
            "name": "Updated Name",
            "goal": "Updated Goal",
            "automation_type": "once",
            "schedule": "manual"
        })
        self.assertEqual(status, 201)
        self.assertEqual(resp["workflow"]["name"], "Updated Name")
        self.assertEqual(resp["workflow"]["goal"], "Updated Goal")

    def test_04_workflow_requires_project_id_and_goal(self) -> None:
        status, _ = self._request("/api/studio/workflows", method="POST", body={
            "name": "Missing Project",
            "goal": "Some goal"
        })
        self.assertEqual(status, 400)

        status, _ = self._request("/api/studio/workflows", method="POST", body={
            "project_id": "p-missing-goal",
            "name": "Missing Goal"
        })
        self.assertEqual(status, 400)

    def test_05_workflow_revision_tracking(self) -> None:
        self._seed_project("p-wf-rev")
        saved = self.automation_store.save({
            "project_id": "p-wf-rev",
            "name": "Revision Track",
            "goal": "Goal 1",
            "automation_type": "once",
            "schedule": "manual"
        })
        self.assertEqual(saved["revision"], 1)
        updated = self.automation_store.save({
            "id": saved["id"],
            "project_id": "p-wf-rev",
            "name": "Revision Track",
            "goal": "Goal 2",
            "automation_type": "once",
            "schedule": "manual"
        })
        self.assertEqual(updated["revision"], 2)

    def test_06_persists_in_canonical_sqlite_database(self) -> None:
        self._seed_project("p-wf-sql")
        saved = self.automation_store.save({
            "project_id": "p-wf-sql",
            "name": "SQL Check",
            "goal": "Check SQL directly",
            "automation_type": "once",
            "schedule": "manual"
        })
        with sqlite3.connect(self.database) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM desktop_automations WHERE id = ?", (saved["id"],)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "SQL Check")

    # =========================================================================
    # Group 2: Project Isolation & Workspace Scope (Tests 7-12)
    # =========================================================================

    def test_07_workflows_strictly_isolated_by_project(self) -> None:
        self._seed_project("p-wf-iso-1")
        self._seed_project("p-wf-iso-2")
        self.automation_store.save({
            "project_id": "p-wf-iso-1", "name": "WF 1", "goal": "G1", "automation_type": "once", "schedule": "manual"
        })
        self.automation_store.save({
            "project_id": "p-wf-iso-2", "name": "WF 2", "goal": "G2", "automation_type": "once", "schedule": "manual"
        })

        status, resp1 = self._request("/api/studio/workflows?project_id=p-wf-iso-1")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp1["workflows"]), 1)
        self.assertEqual(resp1["workflows"][0]["name"], "WF 1")

        status, resp2 = self._request("/api/studio/workflows?project_id=p-wf-iso-2")
        self.assertEqual(status, 200)
        self.assertEqual(len(resp2["workflows"]), 1)
        self.assertEqual(resp2["workflows"][0]["name"], "WF 2")

    def test_08_cross_project_workflow_inspect_returns_404(self) -> None:
        self._seed_project("p-wf-cp-1")
        self._seed_project("p-wf-cp-2")
        saved = self.automation_store.save({
            "project_id": "p-wf-cp-1", "name": "Secret WF", "goal": "G", "automation_type": "once", "schedule": "manual"
        })
        status, _ = self._request(f"/api/studio/workflows/{saved['id']}?project_id=p-wf-cp-2")
        self.assertEqual(status, 404)

    def test_09_workflows_listing_requires_project_id(self) -> None:
        status, _ = self._request("/api/studio/workflows")
        self.assertEqual(status, 400)

    def test_10_workflow_inspect_requires_project_id(self) -> None:
        status, _ = self._request("/api/studio/workflows/some-wf-id")
        self.assertEqual(status, 400)

    def test_11_empty_project_returns_empty_list(self) -> None:
        self._seed_project("p-wf-empty")
        status, resp = self._request("/api/studio/workflows?project_id=p-wf-empty")
        self.assertEqual(status, 200)
        self.assertEqual(resp["workflows"], [])

    def test_12_nonexistent_workflow_returns_404(self) -> None:
        self._seed_project("p-wf-nonexist")
        status, _ = self._request("/api/studio/workflows/wf-nonexistent-999?project_id=p-wf-nonexist")
        self.assertEqual(status, 404)

    # =========================================================================
    # Group 3: Duplication & Templating (Tests 13-18)
    # =========================================================================

    def test_13_duplicate_workflow_via_api(self) -> None:
        self._seed_project("p-wf-dup")
        saved = self.automation_store.save({
            "project_id": "p-wf-dup",
            "name": "Standard Pipeline",
            "goal": "Build and test application",
            "automation_type": "once",
            "schedule": "manual",
            "metadata": {"template": "ci_pipeline", "steps": ["build", "test"]}
        })
        status, resp = self._request(f"/api/studio/workflows/{saved['id']}/duplicate", method="POST", body={
            "project_id": "p-wf-dup"
        })
        self.assertEqual(status, 201)
        cloned = resp["workflow"]
        self.assertEqual(cloned["name"], "Standard Pipeline (Copy)")
        self.assertEqual(cloned["goal"], "Build and test application")
        self.assertEqual(cloned["metadata"]["template"], "ci_pipeline")
        self.assertNotEqual(cloned["id"], saved["id"])

    def test_14_duplicate_workflow_cross_project_rejected(self) -> None:
        self._seed_project("p-wf-dup-a")
        self._seed_project("p-wf-dup-b")
        saved = self.automation_store.save({
            "project_id": "p-wf-dup-a", "name": "Pipeline A", "goal": "Goal", "automation_type": "once", "schedule": "manual"
        })
        status, _ = self._request(f"/api/studio/workflows/{saved['id']}/duplicate", method="POST", body={
            "project_id": "p-wf-dup-b"
        })
        self.assertEqual(status, 400)

    def test_15_duplicate_workflow_increments_project_workflow_count(self) -> None:
        self._seed_project("p-wf-cnt")
        saved = self.automation_store.save({
            "project_id": "p-wf-cnt", "name": "Base WF", "goal": "Goal", "automation_type": "once", "schedule": "manual"
        })
        self._request(f"/api/studio/workflows/{saved['id']}/duplicate", method="POST", body={"project_id": "p-wf-cnt"})
        
        status, resp = self._request("/api/studio/workflows?project_id=p-wf-cnt")
        self.assertEqual(len(resp["workflows"]), 2)

    def test_16_workflow_template_tags_preserved_in_metadata(self) -> None:
        self._seed_project("p-wf-meta")
        status, resp = self._request("/api/studio/workflows", method="POST", body={
            "project_id": "p-wf-meta",
            "name": "Custom Templated Flow",
            "goal": "Templated action",
            "template": "security_audit",
            "steps": ["scan", "report"],
            "participant_ids": ["ollama:deepseek-r1:14b"]
        })
        self.assertEqual(status, 201)
        meta = resp["workflow"]["metadata"]
        self.assertEqual(meta["template"], "security_audit")
        self.assertEqual(meta["steps"], ["scan", "report"])
        self.assertEqual(meta["participant_ids"], ["ollama:deepseek-r1:14b"])

    def test_17_conditional_trigger_definition(self) -> None:
        self._seed_project("p-wf-cond")
        status, resp = self._request("/api/studio/workflows", method="POST", body={
            "project_id": "p-wf-cond",
            "name": "File Watcher",
            "goal": "React to schema change",
            "automation_type": "conditional",
            "schedule": "*/5 * * * *",
            "condition": {
                "version": 1,
                "kind": "file_exists",
                "path": "schema.sql"
            }
        })
        self.assertEqual(status, 201)
        self.assertEqual(resp["workflow"]["automation_type"], "conditional")
        self.assertIsNotNone(resp["workflow"]["condition"])

    def test_18_workflow_dashboard_read_only_protection(self) -> None:
        status, _ = self._request("/api/studio/workflows/unknown-wf", method="PUT", body={})
        self.assertIn(status, (400, 404, 405, 501))

    # =========================================================================
    # Group 4: Execution Mapping & Runtime Governance (Tests 19-24)
    # =========================================================================

    def test_19_run_workflow_maps_to_chat_run(self) -> None:
        self._seed_project("p-wf-run")
        saved = self.automation_store.save({
            "project_id": "p-wf-run",
            "name": "Executable Flow",
            "goal": "Analyze dataset",
            "automation_type": "once",
            "schedule": "manual",
            "metadata": {"participant_ids": ["ollama:llama3.2"]}
        })
        status, resp = self._request(f"/api/studio/workflows/{saved['id']}/run", method="POST", body={
            "project_id": "p-wf-run"
        })
        self.assertEqual(status, 202)
        self.assertEqual(resp["workflow_id"], saved["id"])
        self.assertIn("job_id", resp["run"])

    def test_20_run_workflow_with_input_parameter_substitution(self) -> None:
        self._seed_project("p-wf-sub")
        saved = self.automation_store.save({
            "project_id": "p-wf-sub",
            "name": "Parameterized Flow",
            "goal": "Search for query: {query_term} in mode: {search_mode}",
            "automation_type": "once",
            "schedule": "manual"
        })
        status, resp = self._request(f"/api/studio/workflows/{saved['id']}/run", method="POST", body={
            "project_id": "p-wf-sub",
            "inputs": {
                "query_term": "SQLite WAL",
                "search_mode": "strict"
            }
        })
        self.assertEqual(status, 202)
        self.assertIn("job_id", resp["run"])

    def test_21_run_workflow_records_occurrence_in_store(self) -> None:
        self._seed_project("p-wf-occ")
        saved = self.automation_store.save({
            "project_id": "p-wf-occ",
            "name": "Occurrence Check",
            "goal": "Do task",
            "automation_type": "once",
            "schedule": "manual"
        })
        self._request(f"/api/studio/workflows/{saved['id']}/run", method="POST", body={"project_id": "p-wf-occ"})

        status, resp = self._request(f"/api/studio/workflows/{saved['id']}?project_id=p-wf-occ")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(resp["workflow"]["occurrences"]), 1)

    def test_22_run_workflow_cross_project_rejected(self) -> None:
        self._seed_project("p-wf-rx-1")
        self._seed_project("p-wf-rx-2")
        saved = self.automation_store.save({
            "project_id": "p-wf-rx-1", "name": "WF 1", "goal": "G", "automation_type": "once", "schedule": "manual"
        })
        status, _ = self._request(f"/api/studio/workflows/{saved['id']}/run", method="POST", body={
            "project_id": "p-wf-rx-2"
        })
        self.assertEqual(status, 400)

    def test_23_run_workflow_refuses_raw_api_keys_in_inputs(self) -> None:
        self._seed_project("p-wf-sec")
        saved = self.automation_store.save({
            "project_id": "p-wf-sec",
            "name": "Secret Input WF",
            "goal": "Use secret: {api_key}",
            "automation_type": "once",
            "schedule": "manual"
        })
        status, _ = self._request(f"/api/studio/workflows/{saved['id']}/run", method="POST", body={
            "project_id": "p-wf-sec",
            "inputs": {"api_key": "sk-proj-supersecretkey1234567890abcdef"}
        })
        self.assertEqual(status, 400)

    def test_24_workflow_execution_requires_session_token(self) -> None:
        status, _ = self._request("/api/studio/workflows/wf-1/run", method="POST", body={}, authorized=False)
        self.assertEqual(status, 401)

    # =========================================================================
    # Group 5: Recovery, Continuity & Concurrency (Tests 25-28)
    # =========================================================================

    def test_25_workflow_persists_across_server_restarts(self) -> None:
        self._seed_project("p-wf-restart")
        saved = self.automation_store.save({
            "project_id": "p-wf-restart",
            "name": "Persistent Pipeline",
            "goal": "Must survive restart",
            "automation_type": "once",
            "schedule": "manual"
        })
        wf_id = saved["id"]

        # Stop server
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

        # Restart server on same database
        new_server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        try:
            req_sess = Request(f"http://127.0.0.1:{new_server.server_port}/api/session")
            req_sess.add_header("Origin", "http://localhost:1420")
            with urlopen(req_sess) as resp:
                new_token = json.loads(resp.read())["session_token"]

            req_w = Request(f"http://127.0.0.1:{new_server.server_port}/api/studio/workflows/{wf_id}?project_id=p-wf-restart")
            req_w.add_header("Origin", "http://localhost:1420")
            req_w.add_header("X-AMCA-Session", new_token)
            with urlopen(req_w) as resp:
                doc = json.loads(resp.read())
            self.assertEqual(doc["workflow"]["name"], "Persistent Pipeline")
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=2)
            # Recreate original server for tearDown
            self.server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_26_concurrent_workflow_store_instances(self) -> None:
        store1 = AutomationStore(self.database)
        store2 = AutomationStore(self.database)
        saved = store1.save({
            "project_id": "p-wf-concur",
            "name": "Concurrent Flow",
            "goal": "Test concurrent stores",
            "automation_type": "once",
            "schedule": "manual"
        })
        fetched = store2.get(saved["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "Concurrent Flow")

    def test_27_delete_workflow_via_api(self) -> None:
        self._seed_project("p-wf-del")
        saved = self.automation_store.save({
            "project_id": "p-wf-del",
            "name": "To Delete",
            "goal": "Will be deleted",
            "automation_type": "once",
            "schedule": "manual"
        })
        status, resp = self._request(f"/api/studio/workflows/{saved['id']}?project_id=p-wf-del", method="DELETE")
        self.assertEqual(status, 200)
        self.assertEqual(resp["deleted"], saved["id"])
        self.assertIsNone(self.automation_store.get(saved["id"]))

    def test_28_delete_workflow_cross_project_rejected(self) -> None:
        self._seed_project("p-wf-dx-1")
        self._seed_project("p-wf-dx-2")
        saved = self.automation_store.save({
            "project_id": "p-wf-dx-1", "name": "Protected WF", "goal": "G", "automation_type": "once", "schedule": "manual"
        })
        status, _ = self._request(f"/api/studio/workflows/{saved['id']}?project_id=p-wf-dx-2", method="DELETE")
        self.assertEqual(status, 400)
        self.assertIsNotNone(self.automation_store.get(saved["id"]))


if __name__ == "__main__":
    unittest.main()
