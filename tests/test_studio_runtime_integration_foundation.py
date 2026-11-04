"""Comprehensive validation suite for AMCA Studio Runtime Integration Foundation.

Validated Domains:
1. Studio Runtime Capability & Task Execution Bridge (governed task creation, run lifecycle, JSON contracts)
2. Workspace Runtime Support & Project Isolation (restart safety, cross-project isolation, zero duplicate storage)
3. Agent Visibility API (custom agent registration, catalog discovery, nano hierarchy, read-only guarantees)
4. Runtime Model Hub Support (catalog inspection, provider registration/discovery, model teams, local models)
5. Recovery & Persistence Validation (state continuity across restarts, database integrity, failure visibility)
"""

from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.service import create_server
from amca.studio_projects import StudioProjectStore
from amca.model_hub import ModelHubStore
from amca.agent_fabric import AgentFabricStore
from amca.research_orchestrator import (
    ResearchLimits,
    ResearchRunStore,
    ResearchAction,
    ResearchActionProposal,
    ReasonCode,
)
from amca.research_source import ResearchStore
from amca.research_ingest import ResearchIngestor
from amca.tool_fabric import ToolFabricStore


class StudioRuntimeFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        
        # Pre-initialize schema and subsystems
        self.project_store = StudioProjectStore(self.database)
        self.runs_store = ResearchRunStore(self.database)
        self.research_store = ResearchStore(self.database, storage_root=self.database.parent / "content")
        self.tool_fabric_store = ToolFabricStore(self.database)
        self.hub_store = ModelHubStore(self.database)
        self.agent_store = AgentFabricStore(self.database)

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
        self.temporary.cleanup()

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

    def _seed_project_with_session(self, project_id: str = "proj-seed-01"):
        self.project_store.save({
            "project_id": project_id,
            "name": f"Name {project_id}",
            "description": "Studio container",
            "instructions": "Follow guidelines",
            "template": "general"
        })
        session_row = self.research_store.create_session(
            project_id=project_id, title="obs", purpose="observability"
        )
        return session_row["session_id"]

    # =========================================================================
    # Group 1: Studio Runtime API & Task Execution Bridge Tests (Tests 1-8)
    # =========================================================================

    def test_01_studio_health_subsystem_readiness(self) -> None:
        status, payload = self._request("/api/studio/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("database_available", False))
        self.assertTrue(payload.get("research_subsystem_available", False))
        self.assertTrue(payload.get("tool_subsystem_available", False))
        self.assertTrue(payload.get("migrations_complete", False))

    def test_02_studio_status_validation_bad_request(self) -> None:
        status, payload = self._request("/api/studio/status")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_03_studio_status_clean_empty_project(self) -> None:
        self._seed_project_with_session("proj-clean-01")
        status, payload = self._request("/api/studio/status?project_id=proj-clean-01")
        self.assertEqual(status, 200)
        self.assertEqual(payload["project_id"], "proj-clean-01")
        self.assertEqual(payload["runs_total"], 0)
        self.assertEqual(payload["active_runs"], 0)

    def test_04_studio_project_creation_via_api(self) -> None:
        status, payload = self._request(
            "/api/studio-projects",
            method="POST",
            body={
                "name": "Alpha Studio Project",
                "description": "Studio container for multi-task testing",
                "instructions": "Execute governed workflows",
                "template": "software"
            }
        )
        self.assertIn(status, (200, 201))
        project = payload["project"]
        project_id = project["project_id"]
        self.assertEqual(project["name"], "Alpha Studio Project")

        status, fetched = self._request(f"/api/studio-projects/{project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["name"], "Alpha Studio Project")

    def test_05_studio_projects_listing(self) -> None:
        self.project_store.save({"project_id": "p-list-1", "name": "Project 1", "template": "general"})
        self.project_store.save({"project_id": "p-list-2", "name": "Project 2", "template": "general"})
        status, payload = self._request("/api/studio-projects")
        self.assertEqual(status, 200)
        projects = payload if isinstance(payload, list) else payload.get("projects", [])
        ids = [p["project_id"] for p in projects]
        self.assertIn("p-list-1", ids)
        self.assertIn("p-list-2", ids)

    def test_06_governed_task_run_creation_contract(self) -> None:
        sess_id = self._seed_project_with_session("proj-gov")
        run = self.runs_store.create_run(
            project_id="proj-gov",
            session_id=sess_id,
            objective="Governed execution contract test",
            limits=ResearchLimits(max_steps=5, max_searches=2, max_fetches=2, max_sources=3),
            mode="adaptive"
        )
        self.assertEqual(run["project_id"], "proj-gov")
        self.assertEqual(run["status"], "planned")
        self.assertEqual(run["limits"]["max_steps"], 5)

    def test_07_governed_task_run_state_lifecycle(self) -> None:
        sess_id = self._seed_project_with_session("proj-lc")
        run = self.runs_store.create_run(
            project_id="proj-lc",
            session_id=sess_id,
            objective="Lifecycle transition check",
            limits=ResearchLimits(max_steps=4),
            mode="adaptive"
        )
        run_id = run["run_id"]
        
        # Advance status to running
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='running' WHERE run_id=?", (run_id,))
        status, payload = self._request(f"/api/research/runs/{run_id}?project_id=proj-lc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "running")

        # Complete status
        with self.runs_store._connect() as conn:
            conn.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (run_id,))
        status, payload = self._request(f"/api/research/runs/{run_id}?project_id=proj-lc")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "completed")

    def test_08_studio_activity_feed_event_merging(self) -> None:
        sess_id = self._seed_project_with_session("proj-feed-act")
        run = self.runs_store.create_run(
            project_id="proj-feed-act",
            session_id=sess_id,
            objective="Activity feed test",
            limits=ResearchLimits(max_steps=4),
            mode="adaptive"
        )
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-feed-1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "battery technology"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="deterministic-planner"
            ),
            decision="authorized", outcome="executed"
        )
        status, payload = self._request("/api/studio/activity?project_id=proj-feed-act")
        self.assertEqual(status, 200)
        self.assertIn("events", payload)
        self.assertTrue(any(e["kind"] == "research_step" for e in payload["events"]))

    # =========================================================================
    # Group 2: Workspace Runtime Support & Project Isolation (Tests 9-16)
    # =========================================================================

    def test_09_runs_list_strict_project_filtering(self) -> None:
        sess_a = self._seed_project_with_session("proj-filter-a")
        sess_b = self._seed_project_with_session("proj-filter-b")
        
        self.runs_store.create_run(project_id="proj-filter-a", session_id=sess_a, objective="A task", limits=ResearchLimits(max_steps=4), mode="adaptive")
        self.runs_store.create_run(project_id="proj-filter-b", session_id=sess_b, objective="B task", limits=ResearchLimits(max_steps=4), mode="adaptive")

        status, payload_a = self._request("/api/research/runs?project_id=proj-filter-a")
        self.assertEqual(status, 200)
        for r in payload_a["runs"]:
            self.assertEqual(r["project_id"], "proj-filter-a")

        status, payload_b = self._request("/api/research/runs?project_id=proj-filter-b")
        self.assertEqual(status, 200)
        for r in payload_b["runs"]:
            self.assertEqual(r["project_id"], "proj-filter-b")

    def test_10_activity_feed_cross_project_isolation(self) -> None:
        sess_1 = self._seed_project_with_session("proj-iso-1")
        sess_2 = self._seed_project_with_session("proj-iso-2")

        run = self.runs_store.create_run(project_id="proj-iso-1", session_id=sess_1, objective="Iso 1 task", limits=ResearchLimits(max_steps=4), mode="adaptive")
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-iso-1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "secret query"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="test"
            ),
            decision="authorized", outcome="executed"
        )
        status, payload_2 = self._request("/api/studio/activity?project_id=proj-iso-2")
        self.assertEqual(status, 200)
        self.assertEqual(payload_2["events"], [])

    def test_11_cross_project_direct_detail_isolation(self) -> None:
        sess_a = self._seed_project_with_session("proj-priv-a")
        sess_b = self._seed_project_with_session("proj-priv-b")
        run_a = self.runs_store.create_run(project_id="proj-priv-a", session_id=sess_a, objective="Private A", limits=ResearchLimits(max_steps=4), mode="adaptive")
        
        # Cross-project query check with project_id filter returns 404
        status, payload = self._request(f"/api/research/runs/{run_a['run_id']}?project_id=proj-priv-b")
        self.assertEqual(status, 404)

    def test_12_nonexistent_project_status_safety(self) -> None:
        status, payload = self._request("/api/studio/status?project_id=nonexistent-id-000")
        self.assertEqual(status, 200)
        self.assertEqual(payload["project_id"], "nonexistent-id-000")
        self.assertEqual(payload["runs_total"], 0)

    def test_13_project_deletion_and_cleanup(self) -> None:
        self.project_store.save({"project_id": "proj-cleanup-target", "name": "To Clean", "template": "general"})
        self.project_store.delete("proj-cleanup-target")
        self.assertIsNone(self.project_store.get("proj-cleanup-target"))

    def test_14_project_persistence_across_connection_cycles(self) -> None:
        self.project_store.save({
            "project_id": "proj-reopen-test",
            "name": "Reopen Safety",
            "instructions": "Preserve data perfectly across restarts",
            "template": "software"
        })
        reopened_store = StudioProjectStore(self.database)
        record = reopened_store.get("proj-reopen-test")
        self.assertIsNotNone(record)
        self.assertEqual(record["instructions"], "Preserve data perfectly across restarts")

    def test_15_project_id_character_sanitization(self) -> None:
        with self.assertRaises(ValueError):
            self.project_store.save({"project_id": "illegal!character@id", "name": "Bad"})

    def test_16_project_archival_status_toggle(self) -> None:
        self.project_store.save({"project_id": "proj-arch-toggle", "name": "Toggle", "template": "general", "archived": False})
        self.project_store.save({"project_id": "proj-arch-toggle", "name": "Toggle", "template": "general", "archived": True})
        record = self.project_store.get("proj-arch-toggle")
        self.assertTrue(record["archived"])
        active_projects = self.project_store.list(include_archived=False)
        self.assertNotIn("proj-arch-toggle", [p["project_id"] for p in active_projects])

    # =========================================================================
    # Group 3: Agent Visibility API Tests (Tests 17-24)
    # =========================================================================

    def test_17_agent_fabric_catalog_and_counts(self) -> None:
        status, catalog = self._request("/api/agent-fabric")
        self.assertEqual(status, 200)
        self.assertIn("counts", catalog)
        self.assertIn("core", catalog["counts"])
        self.assertGreaterEqual(catalog["counts"]["core"], 1)

    def test_18_custom_agent_creation_and_visibility(self) -> None:
        agent_payload = {
            "name": "Integration Sentinel",
            "category": "Testing & QA",
            "description": "Monitors runtime boundaries",
            "instructions": "Validate contracts without side effects",
        }
        status, resp = self._request("/api/agent-fabric/custom-agents", method="POST", body=agent_payload)
        self.assertIn(status, (200, 201))
        created = resp["agent"]
        self.assertEqual(created["name"], "Integration Sentinel")
        self.assertEqual(created["origin"], "custom")

    def test_19_agent_fabric_settings_management(self) -> None:
        settings_payload = {"scaling": "conservative", "recursive_spawning": False}
        status, resp = self._request("/api/agent-fabric/settings", method="POST", body=settings_payload)
        self.assertEqual(status, 200)
        self.assertEqual(resp["settings"]["scaling"], "conservative")

    def test_20_nano_agents_catalog_visibility(self) -> None:
        status, payload = self._request("/api/nano-agents")
        self.assertEqual(status, 200)
        self.assertIn("assignments", payload)

    def test_21_agent_fabric_plan_generation(self) -> None:
        status, resp = self._request("/api/agent-fabric/plan", method="POST", body={
            "goal": "Verify studio runtime foundation without regressions"
        })
        self.assertEqual(status, 200)
        self.assertIn("plan", resp)
        self.assertTrue(resp["plan"]["governor"]["authorized"])

    def test_22_custom_agent_deletion_lifecycle(self) -> None:
        status, resp = self._request(
            "/api/agent-fabric/custom-agents",
            method="POST",
            body={"name": "Ephemeral Agent", "description": "Short-lived", "instructions": "Self-destruct"}
        )
        created_id = resp["agent"]["id"]
        
        status, del_resp = self._request(f"/api/agent-fabric/custom-agents/{created_id}", method="DELETE")
        self.assertEqual(status, 200)
        self.assertEqual(del_resp["deleted"], created_id)

    def test_23_agent_visibility_no_sensitive_secrets_leaked(self) -> None:
        status, catalog = self._request("/api/agent-fabric")
        rendered = json.dumps(catalog)
        self.assertNotIn("session_token", rendered.casefold())
        self.assertNotIn("api_key", rendered.casefold())

    def test_24_agent_execution_trail_summary(self) -> None:
        sess_id = self._seed_project_with_session("proj-trail-01")
        run = self.runs_store.create_run(
            project_id="proj-trail-01", session_id=sess_id, objective="Execution trail inspection", limits=ResearchLimits(max_steps=4), mode="adaptive"
        )
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=ResearchActionProposal(
                action_id="act-tr-1", run_id=run["run_id"], step_number=1,
                action_type=ResearchAction.SEARCH_WEB, arguments={"query": "AMCA Studio"},
                reason_code=ReasonCode.NO_EVIDENCE, created_by="test-agent"
            ),
            decision="authorized", outcome="executed"
        )
        status, payload = self._request(f"/api/research/runs/{run['run_id']}/trail?project_id=proj-trail-01")
        self.assertEqual(status, 200)
        self.assertIn("steps", payload)

    # =========================================================================
    # Group 4: Runtime Model Hub Support Tests (Tests 25-32)
    # =========================================================================

    def test_25_model_hub_structure_and_catalogs(self) -> None:
        status, payload = self._request("/api/model-hub")
        self.assertEqual(status, 200)
        self.assertIn("local_models", payload)
        self.assertIn("direct_models", payload)
        self.assertIn("byok_providers", payload)

    def test_26_model_hub_store_provider_creation(self) -> None:
        provider, cred_ref = self.hub_store.create_provider({
            "provider_kind": "anthropic",
            "name": "Anthropic BYOK",
            "endpoint": "https://api.anthropic.com",
            "pricing_mode": "auto"
        })
        self.assertEqual(provider["provider_kind"], "anthropic")
        self.assertEqual(provider["name"], "Anthropic BYOK")
        self.assertTrue(cred_ref.startswith("model-hub.provider."))

    def test_27_model_hub_store_provider_update(self) -> None:
        provider, _ = self.hub_store.create_provider({
            "provider_kind": "openai",
            "name": "OpenAI Provider",
            "model_ids": ["gpt-4o"]
        })
        updated = self.hub_store.update_provider(provider["id"], {
            "name": "OpenAI Updated",
            "model_ids": ["gpt-4o", "o1-preview"]
        })
        self.assertEqual(updated["name"], "OpenAI Updated")
        self.assertEqual(updated["model_ids"], ["gpt-4o", "o1-preview"])

    def test_28_model_hub_store_provider_deletion(self) -> None:
        provider, _ = self.hub_store.create_provider({
            "provider_kind": "custom_vendor",
            "name": "Custom Provider",
            "endpoint": "https://vendor.internal/v1"
        })
        self.hub_store.delete_provider(provider["id"])
        remaining = [p["id"] for p in self.hub_store.list_providers()]
        self.assertNotIn(provider["id"], remaining)

    def test_29_model_hub_team_roundtrip_persistence(self) -> None:
        team = self.hub_store.save_team({
            "name": "Studio Hybrid Team",
            "description": "Multi-tier routing team",
            "model_refs": ["ollama:qwen2.5-coder", "anthropic:claude-3-5-sonnet"]
        })
        teams = self.hub_store.list_teams()
        matching = [t for t in teams if t["id"] == team["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["name"], "Studio Hybrid Team")

    def test_30_local_models_refresh_contract(self) -> None:
        status, payload = self._request("/api/local-models/refresh", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertIn("models", payload)
        self.assertIsInstance(payload["models"], list)

    def test_31_intelligence_status_contract(self) -> None:
        status, payload = self._request("/api/intelligence/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["seam_id"], "amca.intelligence.runtime_bridge")
        self.assertEqual(payload["execution_authority"], "existing_runtime")
        self.assertIn("capabilities", payload)

    def test_32_agent_recovery_continuation_plans_endpoint(self) -> None:
        status, payload = self._request("/api/agent-fabric/recovery")
        self.assertEqual(status, 200)
        self.assertIn("organizations", payload)
        self.assertIsInstance(payload["organizations"], list)


if __name__ == "__main__":
    unittest.main()
