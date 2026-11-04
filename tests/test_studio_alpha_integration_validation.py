"""AMCA Studio Alpha Integration Validation Suite.

Task: AG-AMCA-STUDIO-A1-ALPHA-INTEGRATION-VALIDATION-01

Validates:
1. Full Studio User Journey (Project creation, conversation linking, asset upload, activity tracking)
2. Frontend <-> Runtime Contract Alignment (TypeScript/React types vs Python/HTTP contracts)
3. Recovery UX & Truthful State Reporting (Interrupted run visibility, continuation plans, zero false completion)
4. Security Boundaries & Zero Credential Leakage (Header-based auth, CORS, sanitized error payloads)
5. Multi-Project Concurrency & Lifecycle (Isolation, project switching, archive/delete cleanups)
"""

from __future__ import annotations

import base64
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
from amca.research_orchestrator import (
    ResearchLimits,
    ResearchRunStore,
    ResearchAction,
    ResearchActionProposal,
    ReasonCode,
)
from amca.research_source import ResearchStore
from amca.tool_fabric import ToolFabricStore


class StudioAlphaIntegrationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        
        # Initialize subsystem stores
        self.project_store = StudioProjectStore(self.database)
        self.runs_store = ResearchRunStore(self.database)
        self.research_store = ResearchStore(self.database, storage_root=self.database.parent / "content")
        self.tool_fabric_store = ToolFabricStore(self.database)
        self.hub_store = ModelHubStore(self.database)
        self.agent_store = AgentFabricStore(self.database)
        self.recovery_store = AgentRecoveryStore(self.database)

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
            return exc.code, json.loads(raw) if raw else {}

    def _seed_project(self, project_id: str = "proj-alpha-seed"):
        self.project_store.save({
            "project_id": project_id,
            "name": f"Alpha Project {project_id}",
            "description": "Integration container",
            "instructions": "Alpha validation specification",
            "template": "software"
        })
        session_row = self.research_store.create_session(
            project_id=project_id, title="obs", purpose="observability"
        )
        return session_row["session_id"]

    # =========================================================================
    # Group 1: Full Studio User Journey Validation (Tests 1-6)
    # =========================================================================

    def test_01_complete_studio_project_lifecycle_journey(self) -> None:
        # 1. Create project
        status, resp = self._request("/api/studio-projects", method="POST", body={
            "name": "Commercial Alpha App",
            "description": "Desktop commercial release test",
            "instructions": "Follow zero-regression guidelines",
            "template": "software"
        })
        self.assertIn(status, (200, 201))
        project_id = resp["project"]["project_id"]

        # 2. Attach conversation
        status, conv_resp = self._request("/api/conversations", method="POST", body={
            "mode": "solo",
            "participant_ids": [],
            "workspace_project_id": project_id
        })
        self.assertIn(status, (200, 201))
        conv_id = conv_resp["conversation"]["conversation_id"]

        # 3. Upload studio asset
        status, asset_resp = self._request("/api/studio-assets", method="POST", body={
            "workspace_project_id": project_id,
            "filename": "specification.md",
            "data_base64": base64.b64encode(b"# Specification\nAlpha ready.").decode("ascii")
        })
        self.assertIn(status, (200, 201))

        # 4. Read back aggregated project document
        status, doc = self._request(f"/api/studio-projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(doc["chat_count"], 1)
        self.assertEqual(len(doc["chats"]), 1)
        self.assertEqual(len(doc["files"]), 1)
        self.assertEqual(doc["files"][0]["filename"], "specification.md")

    def test_02_task_execution_to_activity_feed_journey(self) -> None:
        sess_id = self._seed_project("proj-act-journey")
        run = self.runs_store.create_run(
            project_id="proj-act-journey", session_id=sess_id,
            objective="Journey task execution", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-j1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "AMCA journey"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="test-agent"
            ),
            decision="authorized", outcome="executed"
        )
        status, feed = self._request("/api/studio/activity?project_id=proj-act-journey")
        self.assertEqual(status, 200)
        self.assertTrue(len(feed["events"]) >= 1)
        self.assertEqual(feed["events"][0]["kind"], "research_step")

    def test_03_dashboard_status_reflects_active_and_completed_runs(self) -> None:
        sess_id = self._seed_project("proj-dash-status")
        run = self.runs_store.create_run(
            project_id="proj-dash-status", session_id=sess_id,
            objective="Dashboard verification", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (run["run_id"],))

        status, stat = self._request("/api/studio/status?project_id=proj-dash-status")
        self.assertEqual(status, 200)
        self.assertEqual(stat["runs_total"], 1)
        self.assertEqual(stat["runs_by_status"]["completed"], 1)

    def test_04_project_switching_preserves_isolated_state(self) -> None:
        sess_1 = self._seed_project("proj-switch-1")
        sess_2 = self._seed_project("proj-switch-2")
        
        self.runs_store.create_run(project_id="proj-switch-1", session_id=sess_1, objective="P1", limits=ResearchLimits(max_steps=4), mode="adaptive")

        status, s1 = self._request("/api/studio/status?project_id=proj-switch-1")
        status, s2 = self._request("/api/studio/status?project_id=proj-switch-2")
        self.assertEqual(s1["runs_total"], 1)
        self.assertEqual(s2["runs_total"], 0)

    def test_05_conversation_unlinking_updates_chat_count(self) -> None:
        sess_id = self._seed_project("proj-unlink")
        status, conv = self._request("/api/conversations", method="POST", body={
            "workspace_project_id": "proj-unlink"
        })
        conv_id = conv["conversation"]["conversation_id"]

        status, doc_before = self._request("/api/studio-projects/proj-unlink")
        self.assertEqual(doc_before["chat_count"], 1)

        status, _ = self._request(f"/api/conversations/{conv_id}/update", method="POST", body={
            "workspace_project_id": None
        })
        status, doc_after = self._request("/api/studio-projects/proj-unlink")
        self.assertEqual(doc_after["chat_count"], 0)

    def test_06_studio_health_subsystem_readiness_journey(self) -> None:
        status, health = self._request("/api/studio/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["database_available"])
        self.assertTrue(health["research_subsystem_available"])

    # =========================================================================
    # Group 2: Frontend ? Runtime Contract Audit (Tests 7-12)
    # =========================================================================

    def test_07_intelligence_status_contract_matches_ui_types(self) -> None:
        status, payload = self._request("/api/intelligence/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["seam_id"], "amca.intelligence.runtime_bridge")
        self.assertIn("capabilities", payload)
        caps = payload["capabilities"]
        for name in ("deterministic", "adaptive_planner", "hybrid_execution", "nano_agents", "model_router"):
            self.assertIn(name, caps)
            self.assertIn("implemented", caps[name])
            self.assertIn("enabled", caps[name])
            self.assertIn("runtime_ready", caps[name])

    def test_08_model_hub_structure_matches_desktop_types(self) -> None:
        status, hub = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        self.assertIn("byok_providers", hub)
        self.assertIn("direct_models", hub)
        self.assertIn("local_models", hub)
        self.assertIn("model_teams", hub)

    def test_09_nano_agents_structure_matches_desktop_types(self) -> None:
        status, nano = self._request("/api/nano-agents")
        self.assertEqual(status, 200)
        self.assertIn("assignments", nano)
        self.assertIsInstance(nano["assignments"], list)

    def test_10_agent_fabric_catalog_matches_desktop_types(self) -> None:
        status, fabric = self._request("/api/agent-fabric")
        self.assertEqual(status, 200)
        self.assertIn("agents", fabric)
        self.assertIn("counts", fabric)
        self.assertIn("settings", fabric)

    def test_11_activity_feed_events_conform_to_ui_schema(self) -> None:
        sess_id = self._seed_project("proj-schema-feed")
        run = self.runs_store.create_run(
            project_id="proj-schema-feed", session_id=sess_id,
            objective="Schema test", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-s1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "test"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="test-agent"
            ),
            decision="authorized", outcome="executed"
        )
        status, feed = self._request("/api/studio/activity?project_id=proj-schema-feed")
        self.assertEqual(status, 200)
        event = feed["events"][0]
        allowed_keys = {"kind", "ref", "detail", "action", "status", "occurred_at"}
        self.assertTrue(set(event.keys()) <= allowed_keys)

    def test_12_studio_status_conforms_to_ui_schema(self) -> None:
        self._seed_project("proj-schema-stat")
        status, stat = self._request("/api/studio/status?project_id=proj-schema-stat")
        self.assertEqual(status, 200)
        expected_keys = {"project_id", "runs_total", "runs_by_status", "active_runs", "collaboration_modes", "recent_runs", "last_activity"}
        self.assertEqual(set(stat.keys()), expected_keys)

    # =========================================================================
    # Group 3: Recovery UX Validation (Tests 13-17)
    # =========================================================================

    def test_13_interrupted_run_renders_truthfully_on_status(self) -> None:
        sess_id = self._seed_project("proj-recov-vis")
        run = self.runs_store.create_run(
            project_id="proj-recov-vis", session_id=sess_id, objective="Crash sim", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='interrupted', stop_reason='aborted_by_restart' WHERE run_id=?", (run["run_id"],))

        status, stat = self._request("/api/studio/status?project_id=proj-recov-vis")
        self.assertEqual(status, 200)
        self.assertEqual(stat["runs_by_status"].get("interrupted", 0), 1)
        self.assertEqual(stat["recent_runs"][0]["status"], "interrupted")
        self.assertEqual(stat["recent_runs"][0]["stop_reason"], "aborted_by_restart")

    def test_14_no_false_completion_state_on_aborted_tasks(self) -> None:
        sess_id = self._seed_project("proj-no-false-comp")
        run = self.runs_store.create_run(
            project_id="proj-no-false-comp", session_id=sess_id, objective="No mask", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='interrupted' WHERE run_id=?", (run["run_id"],))

        status, stat = self._request("/api/studio/status?project_id=proj-no-false-comp")
        self.assertEqual(stat["runs_by_status"].get("completed", 0), 0)
        self.assertEqual(stat["runs_by_status"].get("interrupted", 0), 1)

    def test_15_agent_recovery_endpoint_returns_valid_organizations(self) -> None:
        status, payload = self._request("/api/agent-fabric/recovery")
        self.assertEqual(status, 200)
        self.assertIn("organizations", payload)

    def test_16_clean_restart_recovery_preserves_studio_projects(self) -> None:
        self._seed_project("proj-restart-safety")
        new_store = StudioProjectStore(self.database)
        doc = new_store.get("proj-restart-safety")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["name"], "Alpha Project proj-restart-safety")

    def test_17_execution_trail_persists_across_session_drops(self) -> None:
        sess_id = self._seed_project("proj-trail-persist")
        run = self.runs_store.create_run(
            project_id="proj-trail-persist", session_id=sess_id, objective="Trail test", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-t1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "persisted step"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="test-agent"
            ),
            decision="authorized", outcome="executed"
        )
        status, trail = self._request(f"/api/research/runs/{run['run_id']}/trail?project_id=proj-trail-persist")
        self.assertEqual(status, 200)
        self.assertEqual(len(trail["steps"]), 1)

    # =========================================================================
    # Group 4: Security Boundary Validation (Tests 18-22)
    # =========================================================================

    def test_18_zero_credential_leakage_across_all_read_endpoints(self) -> None:
        endpoints = [
            "/api/studio/health",
            "/api/agent-fabric",
            "/api/model-hub",
            "/api/nano-agents",
            "/api/studio-projects",
        ]
        for ep in endpoints:
            status, payload = self._request(ep)
            self.assertEqual(status, 200)
            dump = json.dumps(payload).casefold()
            self.assertNotIn("session_token", dump)
            self.assertNotIn("password", dump)

    def test_19_cross_origin_request_blocking(self) -> None:
        req = Request(self.base + "/api/studio/health")
        req.add_header("Origin", "http://untrusted-external-site.org")
        req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                status = resp.status
        except HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)

    def test_20_sanitized_404_on_missing_resources(self) -> None:
        status, payload = self._request("/api/studio-projects/nonexistent-uuid-999")
        self.assertEqual(status, 404)
        self.assertNotIn("exception", json.dumps(payload).casefold())

    def test_21_read_only_protection_on_diagnostics(self) -> None:
        status, _ = self._request("/api/studio/health", method="POST", body={})
        self.assertIn(status, (400, 404, 405))

    def test_22_unauthorized_conversation_creation_blocked(self) -> None:
        status, _ = self._request("/api/conversations", method="POST", body={}, authorized=False)
        self.assertEqual(status, 401)

    # =========================================================================
    # Group 5: End-to-End Alpha Consistency (Tests 23-26)
    # =========================================================================

    def test_23_multiple_conversations_in_single_studio_project(self) -> None:
        sess_id = self._seed_project("proj-multi-chat")
        c1 = self._request("/api/conversations", method="POST", body={"workspace_project_id": "proj-multi-chat"})[1]["conversation"]
        c2 = self._request("/api/conversations", method="POST", body={"workspace_project_id": "proj-multi-chat"})[1]["conversation"]
        c3 = self._request("/api/conversations", method="POST", body={"workspace_project_id": "proj-multi-chat"})[1]["conversation"]

        status, doc = self._request("/api/studio-projects/proj-multi-chat")
        self.assertEqual(status, 200)
        self.assertEqual(doc["chat_count"], 3)
        chat_ids = [c["conversation_id"] for c in doc["chats"]]
        self.assertIn(c1["conversation_id"], chat_ids)
        self.assertIn(c2["conversation_id"], chat_ids)
        self.assertIn(c3["conversation_id"], chat_ids)

    def test_24_project_archival_filtration_journey(self) -> None:
        self.project_store.save({"project_id": "p-active", "name": "Active", "template": "general", "archived": False})
        self.project_store.save({"project_id": "p-archived", "name": "Archived", "template": "general", "archived": True})

        status, active_list = self._request("/api/studio-projects")
        projects = active_list if isinstance(active_list, list) else active_list.get("projects", [])
        ids = [p["project_id"] for p in projects]
        self.assertIn("p-active", ids)
        self.assertNotIn("p-archived", ids)

    def test_25_project_deletion_cleans_conversation_workspace_references(self) -> None:
        self._seed_project("proj-del-unlink")
        conv = self._request("/api/conversations", method="POST", body={"workspace_project_id": "proj-del-unlink"})[1]["conversation"]
        
        status, _ = self._request("/api/studio-projects/proj-del-unlink", method="DELETE")
        self.assertEqual(status, 200)

        status, updated_conv = self._request(f"/api/conversations/{conv['conversation_id']}")
        self.assertIsNone(updated_conv["conversation"]["workspace_project_id"])

    def test_26_local_models_diagnostics_integration(self) -> None:
        status, hub = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        local_models = hub.get("local_models", {})
        self.assertIn("provider", local_models)
        self.assertIn("recommended", local_models)
        self.assertTrue(len(local_models["recommended"]) >= 1)


if __name__ == "__main__":
    unittest.main()
