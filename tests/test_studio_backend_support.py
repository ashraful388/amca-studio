"""ZC-AMCA-STUDIO-BACKEND-SUPPORT-01 tests.

Read-only /api/research/* visibility endpoints over data already stored by
the R2.x stack: runs list/detail, execution trail summary, planning trail
summary, model participation, collaboration mode history, and provenance
references. Verifies project isolation (cross-project reads 404 without
existence leaks), bounded payloads (no raw arguments/state vectors),
authentication requirements, and canonical amca.db usage.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amca.research_orchestrator import (
    ReasonCode,
    ResearchAction,
    ResearchActionProposal,
    ResearchLimits,
    ResearchRunStore,
)
from amca.research_source import ResearchStore
from amca.research_ingest import ResearchIngestor
from amca.service import create_server
from amca.permissions import PermissionStore
from amca.studio_projects import StudioProjectStore
from amca.tool_fabric import (
    Decision,
    RiskClass,
    SideEffectClass,
    ToolCapability,
    ToolFabric,
    ToolFabricStore,
    ToolRequest,
)


def _proposal(action_type: ResearchAction, *, arguments=None):
    return ResearchActionProposal(
        action_id=f"act-{action_type.value}",
        run_id="seeded",
        step_number=1,
        action_type=action_type,
        arguments=arguments or {},
        reason_code=ReasonCode.NO_EVIDENCE,
        created_by="deterministic-planner",
    )


class ResearchVisibilityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            root=root,
            database=self.database,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])
        self.runs_store = ResearchRunStore(self.database)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(self, path: str, *, authorized: bool = True):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if authorized:
            request.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _seed_run(self, *, mode: str = "adaptive",
                  project_id: str = "projA",
                  session_id: str | None = None) -> str:
        run = self.runs_store.create_run(
            project_id=project_id,
            session_id=session_id or f"sess-{project_id}",
            objective="visibility seed",
            limits=ResearchLimits(max_steps=4, max_searches=2,
                                  max_fetches=2, max_sources=3),
            mode=mode)
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=_proposal(ResearchAction.SEARCH_WEB,
                               arguments={"query": "battery"}),
            decision="authorized", outcome="executed")
        self.runs_store.update_planner_metadata(
            run["run_id"], planner_mode=mode,
            planner_source="model", fallback_reason=None)
        return run["run_id"]

    # -- tests ----------------------------------------------------------

    def test_runs_list_empty_before_any_research_activity(self) -> None:
        status, payload = self._json("/api/research/runs")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runs"], [])
        self.assertEqual(payload["pagination"],
                         {"returned": 0, "total": 0, "limit": 200})

    def test_runs_list_and_project_filter(self) -> None:
        run_a = self._seed_run(project_id="projA")
        self.runs_store.create_run(
            project_id="projB", session_id="sess-b", objective="b",
            limits=ResearchLimits(), mode="deterministic")
        status, payload = self._json("/api/research/runs")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["runs"]), 2)
        # Pagination metadata (ZC-...-02): dashboard needs totals + caps.
        self.assertEqual(payload["pagination"],
                         {"returned": 2, "total": 2, "limit": 200})
        status, payload = self._json("/api/research/runs?project_id=projA")
        self.assertEqual([r["run_id"] for r in payload["runs"]], [run_a])
        self.assertEqual(payload["runs"][0]["mode"], "adaptive")
        self.assertEqual(payload["pagination"]["total"], 1)

    def test_run_details_exclude_raw_limits_payload(self) -> None:
        run_id = self._seed_run()
        status, details = self._json(
            f"/api/research/runs/{run_id}?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(details["mode"], "adaptive")
        self.assertEqual(details["planner_source"], "model")
        self.assertIn("limits", details)
        self.assertNotIn("limits_json", details)

    def test_cross_project_detail_is_404_without_leak(self) -> None:
        run_id = self._seed_run(project_id="projA")
        status, payload = self._json(
            f"/api/research/runs/{run_id}?project_id=projB")
        self.assertEqual(status, 404)
        self.assertNotIn("objective", str(payload))
        # Project scoping is mandatory: no filter, no run-level read.
        status, _ = self._json(f"/api/research/runs/{run_id}")
        self.assertEqual(status, 400)

    def test_trail_summary_is_bounded(self) -> None:
        run_id = self._seed_run()
        status, payload = self._json(
            f"/api/research/runs/{run_id}/trail?project_id=projA")
        self.assertEqual(status, 200)
        step = payload["steps"][0]
        self.assertEqual(step["action_type"], "search_web")
        self.assertEqual(step["created_by"], "deterministic-planner")
        rendered = json.dumps(payload)
        self.assertNotIn("arguments", rendered)
        self.assertNotIn("query", rendered)

    def test_participation_aggregates_model_contribution(self) -> None:
        run_id = self._seed_run()
        status, payload = self._json(
            f"/api/research/runs/{run_id}/participation?project_id=projA")
        self.assertEqual(status, 200)
        self.assertIn("deterministic-planner", payload["planners"])
        planner = payload["planners"]["deterministic-planner"]
        self.assertEqual(planner["executed"], 1)

    def test_mode_history_reflects_metadata(self) -> None:
        run_id = self._seed_run(mode="hybrid")
        status, payload = self._json(
            f"/api/research/runs/{run_id}/mode-history?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "hybrid")
        self.assertEqual(payload["planner_source"], "model")

    def test_unknown_sub_resource_404(self) -> None:
        run_id = self._seed_run()
        status, _ = self._json(
            f"/api/research/runs/{run_id}/teleport?project_id=projA")
        self.assertEqual(status, 404)

    def test_endpoints_require_session_authorization(self) -> None:
        status, _ = self._json("/api/research/runs", authorized=False)
        self.assertEqual(status, 401)

    def test_sub_resources_require_session_authorization(self) -> None:
        run_id = self._seed_run()
        for suffix in ("", "/trail", "/planning", "/participation",
                       "/mode-history", "/provenance"):
            status, _ = self._json(
                f"/api/research/runs/{run_id}{suffix}?project_id=projA",
                authorized=False)
            self.assertEqual(status, 401, suffix)

    def test_provenance_references_from_stored_r1_data(self) -> None:
        store = ResearchStore(self.database,
                              storage_root=self.database.parent / "content")
        session = store.create_session(
            project_id="projA", title="provenance", purpose="visibility")
        ingestor = ResearchIngestor(store)
        created = ingestor.ingest_user_content(
            session_id=session["session_id"], project_id="projA",
            title="provenance doc", text="battery efficiency content")
        evidence = store.add_evidence(
            created["snapshot"]["snapshot_id"], project_id="projA",
            locator={"type": "whole"}, excerpt="battery efficiency content")
        citation = store.add_citation(
            evidence["evidence_id"], project_id="projA",
            display_label="[1]")
        # Research Workspace metadata: claims + synthesis (R2.6A UI need).
        from amca.research_synthesis import (
            ClaimType as _ClaimType,
            Grounding as _Grounding,
            ResearchSynthesisStore as _Store,
            SynthesisOutputForm as _Form,
        )
        synth = _Store(self.database,
                       storage_root=self.database.parent / "content")
        question = synth.create_question(
            session_id=session["session_id"], project_id="projA",
            question="battery q")
        claim = synth.create_claim(
            session_id=session["session_id"], project_id="projA",
            claim_text="battery efficiency is measurable",
            claim_type=_ClaimType.FACTUAL,
            question_id=question["question_id"])
        plan = synth.create_plan(
            session_id=session["session_id"], project_id="projA",
            question_ids=[question["question_id"]],
            claim_ids=[claim["claim_id"]], output_form=_Form.BRIEF)
        synth.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=_Grounding.DRAFT,
            draft={"sections": [], "citations": [], "claims_used": []})
        run_id = self._seed_run(session_id=session["session_id"])
        status, payload = self._json(
            f"/api/research/runs/{run_id}/provenance?project_id=projA")
        self.assertEqual(status, 200)
        self.assertTrue(payload["sources"])
        self.assertTrue(payload["snapshots"])
        self.assertTrue(payload["evidence"])
        self.assertTrue(payload["citations"])
        self.assertEqual(payload["claims"][0]["claim_id"],
                         claim["claim_id"])
        self.assertLessEqual(len(payload["claims"][0]["claim_text"]), 200)
        self.assertEqual(payload["synthesis"][0]["plan_id"], plan["plan_id"])
        self.assertEqual(payload["synthesis"][0]["grounding_status"],
                         _Grounding.DRAFT.value)
        del citation

    def test_canonical_database_only(self) -> None:
        db_files = sorted(p.name for p in self.database.parent.glob("*.db"))
        self.assertEqual(db_files, ["amca.db"])


class UIJourneySimulationTests(unittest.TestCase):
    """Simulates the AMCA Studio Alpha dashboard/run/trail/provenance
    flows against the backend exactly as a UI client would drive them."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._get("/api/session")[1]
        self.token = str(session["session_token"])
        self.runs_store = ResearchRunStore(self.database)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _get(self, path: str):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if getattr(self, "token", None):
            request.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_empty_project_dashboard_loads_clean(self) -> None:
        status, payload = self._get("/api/research/runs?project_id=projA")
        self.assertEqual((status, payload["runs"], payload["pagination"]),
                         (200, [], {"returned": 0, "total": 0, "limit": 200}))

    def test_dashboard_run_trail_provenance_journey(self) -> None:
        store = ResearchStore(self.database,
                              storage_root=self.database.parent / "content")
        session_row = store.create_session(
            project_id="projA", title="journey", purpose="simulation")
        ingestor = ResearchIngestor(store)
        created = ingestor.ingest_user_content(
            session_id=session_row["session_id"], project_id="projA",
            title="journey doc", text="battery efficiency journey text")
        evidence = store.add_evidence(
            created["snapshot"]["snapshot_id"], project_id="projA",
            locator={"type": "whole"}, excerpt="battery efficiency journey text")
        run = self.runs_store.create_run(
            project_id="projA", session_id=session_row["session_id"],
            objective="studio alpha demo",
            limits=ResearchLimits(max_steps=3), mode="hybrid")
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=_proposal(ResearchAction.SEARCH_LOCAL,
                               arguments={"query": "battery"}),
            decision="authorized", outcome="executed")
        scope = "?project_id=projA"

        # Dashboard: run is visible with pagination metadata.
        status, dash = self._get(f"/api/research/runs{scope}")
        self.assertEqual(status, 200)
        self.assertEqual(dash["pagination"]["total"], 1)
        # Run opening.
        status, details = self._get(
            f"/api/research/runs/{run['run_id']}{scope}")
        self.assertEqual(status, 200)
        self.assertEqual(details["mode"], "hybrid")
        # Trail viewing: bounded summary without argument payloads.
        status, trail = self._get(
            f"/api/research/runs/{run['run_id']}/trail{scope}")
        self.assertEqual(status, 200)
        self.assertEqual(trail["steps"][0]["outcome"], "executed")
        self.assertNotIn("arguments", json.dumps(trail))
        # Provenance viewing: references only, no content bytes.
        status, prov = self._get(
            f"/api/research/runs/{run['run_id']}/provenance{scope}")
        self.assertEqual(status, 200)
        self.assertTrue(prov["sources"])
        rendered = json.dumps(prov)
        self.assertNotIn("journey text", rendered.replace("\\n", "\n"))

    def test_cross_project_and_missing_auth_during_journey(self) -> None:
        run = self.runs_store.create_run(
            project_id="projA", session_id="sess-projA",
            objective="isolation probe", limits=ResearchLimits())
        # Cross-project access from projB perspective: indistinguishable 404.
        status, _ = self._get(
            f"/api/research/runs/{run['run_id']}?project_id=projB")
        self.assertEqual(status, 404)
        # Missing session token anywhere on the journey: 401.
        request = Request(
            self.base + f"/api/research/runs/{run['run_id']}/trail"
                        "?project_id=projA")
        try:
            urlopen(request)
            self.fail("expected 401")
        except HTTPError as exc:
            self.assertEqual(exc.code, 401)

    def session_probe(self) -> str:
        return "unused"


class StudioObservabilityTests(unittest.TestCase):
    """ZC-AMCA-STUDIO-OBSERVABILITY-HARDENING-01: bounded status summary +
    health/readiness for the Studio UI (read-only, project-scoped)."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])
        self.runs_store = ResearchRunStore(self.database)
        # A real deployment initializes the tool subsystem at startup.
        from amca.tool_fabric import ToolFabricStore
        ToolFabricStore(self.database)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(self, path: str, *, authorized: bool = True):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if authorized and getattr(self, "token", None):
            request.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _seed(self, status_runnable: bool = True) -> None:
        store = ResearchStore(self.database,
                              storage_root=self.database.parent / "content")
        session_row = store.create_session(
            project_id="projA", title="obs", purpose="observability")
        ingestor = ResearchIngestor(store)
        ingestor.ingest_user_content(
            session_id=session_row["session_id"], project_id="projA",
            title="doc", text="battery content")
        limits = ResearchLimits(max_steps=4)
        run_ok = self.runs_store.create_run(
            project_id="projA", session_id=session_row["session_id"],
            objective="healthy run",
            limits=limits, mode="adaptive")
        run_fail = self.runs_store.create_run(
            project_id="projA", session_id=session_row["session_id"],
            objective="interrupted probe",
            limits=limits, mode="hybrid")
        del status_runnable
        with self.runs_store._connect() as connection:
            connection.execute(
                "UPDATE research_runs SET status='completed' WHERE run_id=?",
                (run_ok["run_id"],))
            connection.execute(
                "UPDATE research_runs SET status='interrupted' WHERE run_id=?",
                (run_fail["run_id"],))
        # Initialize the planning-trail tables so migrations stay complete.
        from amca.research_planning import PlanningTrailStore
        PlanningTrailStore(self.database)

    def test_status_requires_auth_and_project(self) -> None:
        status, _ = self._json("/api/studio/status?project_id=projA",
                               authorized=False)
        self.assertEqual(status, 401)
        status, payload = self._json("/api/studio/status", authorized=True)
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_empty_project_status_shape(self) -> None:
        status, payload = self._json("/api/studio/status?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runs_total"], 0)
        self.assertEqual(payload["active_runs"], 0)
        self.assertEqual(payload["recent_runs"], [])
        self.assertIsNone(payload["last_activity"])

    def test_active_project_counts_modes_and_recent_runs(self) -> None:
        self._seed()
        status, payload = self._json("/api/studio/status?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runs_total"], 2)
        self.assertEqual(payload["runs_by_status"].get("completed"), 1)
        self.assertEqual(payload["runs_by_status"].get("interrupted"), 1)
        self.assertEqual(payload["active_runs"], 0)
        self.assertEqual(payload["collaboration_modes"],
                         {"adaptive": 1, "hybrid": 1})
        objectives = " ".join(r["objective"] for r in payload["recent_runs"])
        self.assertIn("healthy run", objectives)
        self.assertIsNotNone(payload["last_activity"])

    def test_failure_state_visible_as_interrupted_not_hidden(self) -> None:
        self._seed()
        _, payload = self._json("/api/studio/status?project_id=projA")
        recent = {r["status"] for r in payload["recent_runs"]}
        self.assertIn("interrupted", recent)

    def test_no_sensitive_payload_leakage(self) -> None:
        self._seed()
        rendered = json.dumps(self._json("/api/studio/status?project_id=projA")[1])
        for banned in ("credential", "password", "state_json",
                       "vector_blob", "prompt"):
            self.assertNotIn(banned, rendered.lower())

    def test_health_reports_subsystem_readiness(self) -> None:
        self._seed()  # initializes the R2 research tables on this database
        status, payload = self._json("/api/studio/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["database_available"])
        self.assertTrue(payload["research_subsystem_available"])
        self.assertTrue(payload["tool_subsystem_available"])
        self.assertTrue(payload["migrations_complete"])

    def test_project_isolation_of_status(self) -> None:
        self._seed()
        _, other = self._json("/api/studio/status?project_id=projB")
        self.assertEqual(other["runs_total"], 0)


class StudioActivityTests(unittest.TestCase):
    """ZC-AMCA-STUDIO-ALPHA-PRODUCT-READINESS-01: unified read-only
    activity feed for the Studio Logs panel."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        self.database = Path(self.temporary.name) / "amca.db"
        self.server = create_server(
            host="127.0.0.1", port=0, root=root, database=self.database)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        session = self._json("/api/session", authorized=False)[1]
        self.token = str(session["session_token"])
        self.runs_store = ResearchRunStore(self.database)
        ToolFabricStore(self.database)

    def tearDown(self) -> None:
        # PermissionStore holds its sqlite connection open; release it
        # before Windows temp-directory cleanup removes amca.db.
        if getattr(self, "approval_store", None) is not None:
            self.approval_store.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def _json(self, path: str, *, authorized: bool = True):
        request = Request(self.base + path)
        request.add_header("Origin", "http://localhost:1420")
        if authorized:
            request.add_header("X-AMCA-Session", self.token)
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_activity_auth_and_project_required(self) -> None:
        status, _ = self._json("/api/studio/activity?project_id=projA",
                               authorized=False)
        self.assertEqual(status, 401)
        status, payload = self._json("/api/studio/activity")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_activity_merges_sources_sorted_and_bounded(self) -> None:
        StudioProjectStore(self.database).save(
            {"project_id": "projA", "name": "Activity Seed",
             "template": "general"})
        fabric = ToolFabric(ToolFabricStore(self.database),
                            project_root="projA")
        request = ToolRequest(
            request_id="req-audit", organization_id="agent-fabric",
            project_id="projA", tool_id="math.evaluate", operation="evaluate",
            normalized_arguments={}, requested_resource_scope=None,
            actor="test")
        # Register a benign capability so submit persists a request+execution.
        fabric.register_capability(ToolCapability(
            tool_id="math.evaluate", version="1.0.0", category="compute",
            description="bounded math", risk_class=RiskClass.COMPUTE_ONLY,
            side_effect_class=SideEffectClass.NONE,
            supported_operations=("evaluate",),
            resource_scopes=(), approval_policy=Decision.ALLOW))
        decision, record = fabric.submit(request, {})
        self.assertEqual(decision.decision, Decision.ALLOW)
        fabric.execute(record.execution_id)

        # A pending approval decision for the same project.
        self.approval_store = PermissionStore(self.database)
        self.approval_store.request(
            project_id="projA", action="run_code",
            scope={"path": "scripts/demo"},
            reason="activity feed seed")

        # A research step for the same project.
        run = self.runs_store.create_run(
            project_id="projA", session_id="sess-projA",
            objective="activity seed", limits=ResearchLimits(max_steps=2))
        self.runs_store.add_step(
            run_id=run["run_id"], ordinal=1,
            proposal=_proposal(ResearchAction.SEARCH_WEB,
                               arguments={"query": "battery"}),
            decision="authorized", outcome="executed")

        status, payload = self._json("/api/studio/activity?project_id=projA")
        self.assertEqual(status, 200)
        kinds = {e["kind"] for e in payload["events"]}
        self.assertIn("tool_execution", kinds)
        self.assertIn("approval_decision", kinds)
        self.assertIn("research_step", kinds)
        self.assertEqual(
            payload["sources"], {"tool_executions": True,
                                 "approval_decisions": True,
                                 "research_steps": True})
        stamps = [e["occurred_at"] for e in payload["events"]
                  if e.get("occurred_at")]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        rendered = json.dumps(payload)
        self.assertNotIn("normalized_arguments", rendered)
        self.assertNotIn("credential", rendered.lower())
        for event in payload["events"]:
            self.assertEqual(
                set(event) <= {"kind", "ref", "detail", "action", "status",
                               "occurred_at"}, True)

    def test_activity_isolated_per_project(self) -> None:
        _, empty = self._json("/api/studio/activity?project_id=projB")
        self.assertEqual(empty["events"], [])
        self.assertEqual(empty["limit"], 50)
        self.assertTrue(
            all(isinstance(flag, bool) for flag in empty["sources"].values()))


if __name__ == "__main__":
    unittest.main()
