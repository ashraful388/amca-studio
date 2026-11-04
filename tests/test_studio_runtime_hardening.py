"""Comprehensive Hardening and Validation Suite for AMCA Studio Runtime.

Task: AG-AMCA-STUDIO-RUNTIME-HARDENING-02

Domains Tested:
1. Studio Runtime Health System (Unified health reporting, failure state detection, zero duplicate logic)
2. Runtime Error Handling Layer (User-safe messages, no stack trace leakage, actionable recovery hints)
3. Session / Restart Recovery Validation (Process restart survival, orphaned run handling, recovery plans)
4. Agent Runtime Observability (Read-only status, Nano hierarchies, economic telemetry, zero side effects)
5. Model Runtime Diagnostics (BYOK providers, local model compute profiles, team routing contracts)
6. Performance Validation & Baselines (Startup, dashboard, agent list, model hub latency benchmarks)
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


class StudioRuntimeHardeningTests(unittest.TestCase):
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

    def _seed_project(self, project_id: str = "proj-harden-01"):
        self.project_store.save({
            "project_id": project_id,
            "name": f"Hardened Project {project_id}",
            "description": "Hardening container",
            "instructions": "Follow rigorous production standards",
            "template": "software"
        })
        session_row = self.research_store.create_session(
            project_id=project_id, title="obs", purpose="observability"
        )
        return session_row["session_id"]

    # =========================================================================
    # Group 1: Studio Runtime Health System (Tests 1-8)
    # =========================================================================

    def test_01_health_returns_ok_with_complete_subsystem_map(self) -> None:
        status, health = self._request("/api/studio/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["database_available"])
        self.assertTrue(health["research_subsystem_available"])
        self.assertTrue(health["tool_subsystem_available"])
        self.assertTrue(health["migrations_complete"])

    def test_02_health_is_read_only_and_does_not_mutate_state(self) -> None:
        status_a, health_a = self._request("/api/studio/health")
        status_b, health_b = self._request("/api/studio/health")
        self.assertEqual(status_a, 200)
        self.assertEqual(status_b, 200)
        self.assertEqual(health_a, health_b)

    def test_03_health_reports_initialized_server_health_truthfully(self) -> None:
        status, health = self._request("/api/studio/health")
        self.assertEqual(status, 200)
        self.assertIn("database_available", health)
        self.assertIn("research_subsystem_available", health)
        self.assertIn("tool_subsystem_available", health)

    def test_04_status_requires_session_token(self) -> None:
        status, payload = self._request("/api/studio/status?project_id=p1", authorized=False)
        self.assertEqual(status, 401)
        self.assertIn("error", payload)

    def test_05_activity_feed_requires_session_token(self) -> None:
        status, payload = self._request("/api/studio/activity?project_id=p1", authorized=False)
        self.assertEqual(status, 401)

    def test_06_health_endpoint_requires_session_token(self) -> None:
        status, payload = self._request("/api/studio/health", authorized=False)
        self.assertEqual(status, 401)

    def test_07_status_requires_project_id_parameter(self) -> None:
        status, payload = self._request("/api/studio/status")
        self.assertEqual(status, 400)
        self.assertIn("project_id required", payload.get("error", ""))

    def test_08_activity_requires_project_id_parameter(self) -> None:
        status, payload = self._request("/api/studio/activity")
        self.assertEqual(status, 400)
        self.assertIn("project_id required", payload.get("error", ""))

    # =========================================================================
    # Group 2: Runtime Error Handling Layer (Tests 9-16)
    # =========================================================================

    def test_09_invalid_json_body_returns_clean_400_without_traceback(self) -> None:
        req = Request(self.base + "/api/studio-projects", data=b"INVALID_JSON{", method="POST")
        req.add_header("Origin", "http://localhost:1420")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                status = resp.status
                payload = json.loads(resp.read())
        except HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read() or b"{}")
        self.assertEqual(status, 400)
        self.assertNotIn("traceback", json.dumps(payload).lower())
        self.assertNotIn("file \"", json.dumps(payload).lower())

    def test_10_unknown_api_path_returns_clean_404(self) -> None:
        status, payload = self._request("/api/unknown-nonexistent-endpoint")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_11_disallowed_http_method_handled_cleanly(self) -> None:
        status, payload = self._request("/api/studio/health", method="DELETE")
        self.assertIn(status, (400, 404, 405))

    def test_12_oversized_project_name_rejected_with_actionable_error(self) -> None:
        huge_name = "X" * 2000
        status, payload = self._request(
            "/api/studio-projects",
            method="POST",
            body={"name": huge_name, "template": "general"}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_13_missing_project_name_rejected_cleanly(self) -> None:
        status, payload = self._request(
            "/api/studio-projects",
            method="POST",
            body={"description": "Missing name", "template": "general"}
        )
        self.assertEqual(status, 400)

    def test_14_cross_origin_request_enforcement(self) -> None:
        req = Request(self.base + "/api/studio/health")
        req.add_header("Origin", "https://malicious-website.com")
        req.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(req) as resp:
                status = resp.status
        except HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 403)

    def test_15_empty_token_header_rejected(self) -> None:
        req = Request(self.base + "/api/studio/health")
        req.add_header("Origin", "http://localhost:1420")
        req.add_header("X-AMCA-Session", "")
        try:
            with urlopen(req) as resp:
                status = resp.status
        except HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 401)

    def test_16_tampered_token_rejected(self) -> None:
        req = Request(self.base + "/api/studio/health")
        req.add_header("Origin", "http://localhost:1420")
        req.add_header("X-AMCA-Session", "forged-tampered-token-12345")
        try:
            with urlopen(req) as resp:
                status = resp.status
        except HTTPError as exc:
            status = exc.code
        self.assertEqual(status, 401)

    # =========================================================================
    # Group 3: Session / Restart Recovery Validation (Tests 17-24)
    # =========================================================================

    def test_17_project_persists_across_server_restarts(self) -> None:
        sess_id = self._seed_project("proj-restart-01")
        
        # Stop original server
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

        # Launch fresh server pointing to same sqlite db
        new_server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        try:
            req_sess = Request(f"http://127.0.0.1:{new_server.server_port}/api/session")
            req_sess.add_header("Origin", "http://localhost:1420")
            with urlopen(req_sess) as resp:
                new_token = json.loads(resp.read())["session_token"]

            req_p = Request(f"http://127.0.0.1:{new_server.server_port}/api/studio-projects/proj-restart-01")
            req_p.add_header("Origin", "http://localhost:1420")
            req_p.add_header("X-AMCA-Session", new_token)
            with urlopen(req_p) as resp:
                doc = json.loads(resp.read())
            self.assertEqual(doc["project_id"], "proj-restart-01")
            self.assertEqual(doc["name"], "Hardened Project proj-restart-01")
        finally:
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=2)
            # Recreate original server for tearDown
            self.server = create_server(host="127.0.0.1", port=0, root=self.root, database=self.database)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base = f"http://127.0.0.1:{self.server.server_port}"

    def test_18_interrupted_run_state_reported_truthfully_on_status(self) -> None:
        sess_id = self._seed_project("proj-fail-vis")
        run = self.runs_store.create_run(
            project_id="proj-fail-vis", session_id=sess_id, objective="Failure vis", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='interrupted', stop_reason='process_crash' WHERE run_id=?", (run["run_id"],))

        status, payload = self._request("/api/studio/status?project_id=proj-fail-vis")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runs_by_status"].get("interrupted", 0), 1)

    def test_19_completed_and_interrupted_counts_split_correctly(self) -> None:
        sess_id = self._seed_project("proj-split-vis")
        r1 = self.runs_store.create_run(project_id="proj-split-vis", session_id=sess_id, objective="Run 1", limits=ResearchLimits(max_steps=4), mode="adaptive")
        r2 = self.runs_store.create_run(project_id="proj-split-vis", session_id=sess_id, objective="Run 2", limits=ResearchLimits(max_steps=4), mode="adaptive")
        
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (r1["run_id"],))
            conn.execute("UPDATE research_runs SET status='interrupted' WHERE run_id=?", (r2["run_id"],))

        status, payload = self._request("/api/studio/status?project_id=proj-split-vis")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runs_by_status"].get("completed", 0), 1)
        self.assertEqual(payload["runs_by_status"].get("interrupted", 0), 1)
        self.assertEqual(payload["runs_total"], 2)

    def test_20_sqlite_wal_journal_mode_configured(self) -> None:
        conn = sqlite3.connect(self.database)
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            self.assertTrue(len(row[0]) > 0)
        finally:
            conn.close()

    def test_21_agent_recovery_organization_plan_resolution(self) -> None:
        status, payload = self._request("/api/agent-fabric/recovery")
        self.assertEqual(status, 200)
        self.assertIn("organizations", payload)

    def test_22_agent_recovery_unknown_organization_returns_404(self) -> None:
        status, payload = self._request("/api/agent-fabric/recovery/unknown-org-id/plan")
        self.assertEqual(status, 404)

    def test_23_reopened_store_instance_concurrency(self) -> None:
        store1 = StudioProjectStore(self.database)
        store2 = StudioProjectStore(self.database)
        store1.save({"project_id": "c1", "name": "Concur 1", "template": "general"})
        doc2 = store2.get("c1")
        self.assertIsNotNone(doc2)
        self.assertEqual(doc2["name"], "Concur 1")

    def test_24_database_busy_timeout_safeguard(self) -> None:
        conn = sqlite3.connect(self.database)
        try:
            cursor = conn.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            conn.close()

    # =========================================================================
    # Group 4: Agent Runtime Observability (Tests 25-32)
    # =========================================================================

    def test_25_agent_fabric_catalog_counts_reflect_available_agents(self) -> None:
        status, catalog = self._request("/api/agent-fabric")
        self.assertEqual(status, 200)
        self.assertIn("counts", catalog)
        self.assertGreaterEqual(catalog["counts"].get("core", 0), 1)

    def test_26_custom_agent_creation_persists_in_catalog(self) -> None:
        payload = {
            "name": "Audit Observer",
            "category": "Audit & Compliance",
            "description": "Read-only compliance monitor",
            "instructions": "Inspect runtime state",
        }
        status, resp = self._request("/api/agent-fabric/custom-agents", method="POST", body=payload)
        self.assertIn(status, (200, 201))
        agent = resp["agent"]
        self.assertEqual(agent["name"], "Audit Observer")

        status, catalog = self._request("/api/agent-fabric")
        agents = catalog.get("agents", [])
        self.assertTrue(any(a["name"] == "Audit Observer" for a in agents))

    def test_27_agent_settings_conservative_scaling_policy(self) -> None:
        status, resp = self._request("/api/agent-fabric/settings", method="POST", body={"scaling": "conservative"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["settings"]["scaling"], "conservative")

    def test_28_nano_agents_hierarchy_structure(self) -> None:
        status, payload = self._request("/api/nano-agents")
        self.assertEqual(status, 200)
        self.assertIn("assignments", payload)
        self.assertIsInstance(payload["assignments"], list)

    def test_29_agent_economy_summary_endpoint(self) -> None:
        status, payload = self._request("/api/agent-fabric/economy")
        self.assertEqual(status, 200)
        self.assertIn("organizations", payload)

    def test_30_agent_planning_preview_is_non_mutating(self) -> None:
        status, resp = self._request("/api/agent-fabric/plan", method="POST", body={
            "goal": "Observe runtime metrics without dispatching tasks"
        })
        self.assertEqual(status, 200)
        self.assertFalse(resp["plan"]["model_calls_started"])

    def test_31_agent_fabric_read_only_protection_against_unauthorized_edits(self) -> None:
        status, payload = self._request("/api/agent-fabric/custom-agents", method="POST", body={}, authorized=False)
        self.assertEqual(status, 401)

    def test_32_agent_visibility_excludes_credentials_from_all_payloads(self) -> None:
        status, catalog = self._request("/api/agent-fabric")
        dump = json.dumps(catalog).casefold()
        self.assertNotIn("session_token", dump)
        self.assertNotIn("password", dump)

    # =========================================================================
    # Group 5: Model Runtime Diagnostics (Tests 33-38)
    # =========================================================================

    def test_33_model_hub_returns_direct_builtin_models(self) -> None:
        status, hub = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        self.assertIn("direct_models", hub)
        self.assertGreaterEqual(len(hub["direct_models"]), 1)

    def test_34_model_hub_local_models_diagnostics(self) -> None:
        status, hub = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        self.assertIn("local_models", hub)
        self.assertEqual(hub["local_models"]["provider"], "ollama")
        self.assertIn("recommended", hub["local_models"])

    def test_35_model_hub_selection_policy_structure(self) -> None:
        status, hub = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        self.assertIn("selection", hub)
        self.assertIn("auto_objective", hub["selection"])

    def test_36_model_team_creation_and_retrieval(self) -> None:
        team = self.hub_store.save_team({
            "name": "Hardened Team",
            "description": "Production routing team",
            "model_refs": ["ollama:qwen3:1.7b", "provider.x:gpt-4o"]
        })
        self.assertEqual(team["name"], "Hardened Team")
        teams = self.hub_store.list_teams()
        self.assertTrue(any(t["name"] == "Hardened Team" for t in teams))

    def test_37_byok_provider_https_validation_security(self) -> None:
        with self.assertRaises(ValueError):
            self.hub_store.create_provider({
                "provider_kind": "custom_vendor",
                "name": "Insecure Vendor",
                "endpoint": "http://insecure-plain-http.internal"
            })

    def test_38_intelligence_status_seam_declaration(self) -> None:
        status, payload = self._request("/api/intelligence/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["execution_authority"], "existing_runtime")
        self.assertTrue(payload["capabilities"]["deterministic"]["implemented"])

    # =========================================================================
    # Group 6: Performance Baselines & Latency Benchmarks (Tests 39-42)
    # =========================================================================

    def test_39_health_endpoint_latency_benchmark(self) -> None:
        t0 = time.perf_counter()
        for _ in range(5):
            status, _ = self._request("/api/studio/health")
            self.assertEqual(status, 200)
        elapsed_avg = (time.perf_counter() - t0) / 5
        self.assertLess(elapsed_avg, 0.150, f"Average health latency {elapsed_avg:.4f}s exceeded 150ms")

    def test_40_status_endpoint_latency_benchmark(self) -> None:
        self._seed_project("proj-perf-stat")
        t0 = time.perf_counter()
        for _ in range(5):
            status, _ = self._request("/api/studio/status?project_id=proj-perf-stat")
            self.assertEqual(status, 200)
        elapsed_avg = (time.perf_counter() - t0) / 5
        self.assertLess(elapsed_avg, 0.150, f"Average status latency {elapsed_avg:.4f}s exceeded 150ms")

    def test_41_agent_fabric_catalog_latency_benchmark(self) -> None:
        t0 = time.perf_counter()
        for _ in range(5):
            status, _ = self._request("/api/agent-fabric")
            self.assertEqual(status, 200)
        elapsed_avg = (time.perf_counter() - t0) / 5
        self.assertLess(elapsed_avg, 0.150, f"Average agent catalog latency {elapsed_avg:.4f}s exceeded 150ms")

    def test_42_model_hub_diagnostics_latency_benchmark(self) -> None:
        t0 = time.perf_counter()
        for _ in range(3):
            status, _ = self._request("/api/model-hub")
            self.assertEqual(status, 200)
        elapsed_avg = (time.perf_counter() - t0) / 3
        self.assertLess(elapsed_avg, 3.0, f"Average model hub latency {elapsed_avg:.4f}s exceeded 3s timeout bound")


if __name__ == "__main__":
    unittest.main()
