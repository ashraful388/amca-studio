"""ZC-AMCA-RESEARCH-EXECUTION-GATEWAY-01 tests.

Research Execution Gateway lifecycle API over the frozen R1/R2 stack:
create / status / cancel / resume with the approved lifecycle projection,
idempotent cancel, guarded resume, ownership (session + project_id +
run_id), indistinguishable 404s, and payload safety.
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
    ResearchLimits,
    ResearchRunStore,
    RunStatus,
    StopReason,
)
from amca.research_gateway import STATUS_PROJECTION
from amca.service import create_server

_FORBIDDEN = ("prompt", "planner_state", "credential", "normalized_arguments",
              "reasoning", "vector")


class ResearchGatewayTests(unittest.TestCase):
    """Service-level lifecycle contract for the research gateway."""

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
        self.store = ResearchRunStore(self.database)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    # -- helpers ---------------------------------------------------------

    def _request(self, path: str, *, method: str = "GET", body=None,
                 authorized: bool = True):
        request = Request(self.base + path, method=method)
        request.add_header("Origin", "http://localhost:1420")
        if authorized:
            request.add_header("X-AMCA-Session", self.token)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, data=data) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _json(self, path: str, *, authorized: bool = True):
        return self._request(path, authorized=authorized)

    def _create_run(self, *, project_id: str = "projA",
                    objective: str = "gateway lifecycle probe",
                    mode: str = "deterministic"):
        status, payload = self._request(
            "/api/research/runs", method="POST",
            body={"project_id": project_id, "objective": objective,
                  "mode": mode})
        self.assertEqual(status, 200)
        return payload

    def _set_status(self, run_id: str, status: RunStatus,
                    stop_reason: StopReason | None = None) -> None:
        """Test-only setup through the frozen store's public API."""
        self.store.set_run_status(run_id, status, stop_reason)

    def _assert_clean(self, payload) -> None:
        rendered = json.dumps(payload).lower()
        for needle in _FORBIDDEN:
            self.assertNotIn(needle, rendered)

    # -- create ------------------------------------------------------------

    def test_create_requires_authentication(self) -> None:
        status, payload = self._request(
            "/api/research/runs", method="POST", authorized=False,
            body={"project_id": "projA", "objective": "x",
                  "mode": "deterministic"})
        self.assertEqual(status, 401)
        self._assert_clean(payload)

    def test_create_validates_input(self) -> None:
        base = {"project_id": "projA", "objective": "obj",
                "mode": "deterministic"}
        for mutation in (
                {"project_id": ""}, {"project_id": None},
                {"objective": ""}, {"objective": None},
                {"mode": "hyper"}):
            broken = dict(base)
            broken.update(mutation)
            status, payload = self._request(
                "/api/research/runs", method="POST", body=broken)
            self.assertEqual(status, 400)
            self.assertIn("error", payload)

    def test_create_returns_created_projection_and_persists(self) -> None:
        payload = self._create_run()
        self.assertEqual(set(payload),
                         {"run_id", "status", "created_at"})
        self.assertEqual(payload["status"], "created")

        status, projected = self._json(
            f"/api/research/runs/{payload['run_id']}/status"
            "?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(projected["status"], "planning")
        self.assertEqual(projected["objective"], "gateway lifecycle probe")
        self.assertIsNone(projected["stop_reason"])
        self.assertEqual(set(projected),
                         {"run_id", "status", "objective", "created_at",
                          "updated_at", "stop_reason"})
        self._assert_clean(projected)

    # -- status ------------------------------------------------------------

    def test_status_ownership_rules(self) -> None:
        run = self._create_run()
        status, missing = self._json(
            f"/api/research/runs/{run['run_id']}/status")
        self.assertEqual(status, 400)
        self.assertIn("error", missing)

        wrong_code, wrong = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projB")
        unknown_code, unknown = self._json(
            "/api/research/runs/run-does-not-exist/status"
            "?project_id=projB")
        # Wrong project and unknown run are indistinguishable.
        self.assertEqual(wrong_code, 404)
        self.assertEqual(unknown_code, 404)
        self.assertEqual(wrong, unknown)

    def test_status_reads_are_consistent_snapshots(self) -> None:
        run = self._create_run()
        first = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        second = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        self.assertEqual(first, second)

    # -- cancel --------------------------------------------------------------

    def test_cancel_is_idempotent(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.RUNNING)
        path = f"/api/research/runs/{run['run_id']}/cancel"
        status, first = self._request(path, method="POST",
                                      body={"project_id": "projA"})
        self.assertEqual(status, 200)
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(set(first), {"run_id", "status", "updated_at"})

        status, second = self._request(path, method="POST",
                                       body={"project_id": "projA"})
        self.assertEqual(status, 200)
        self.assertEqual(second["status"], "cancelled")
        self.assertEqual(second["updated_at"], first["updated_at"])

        code, projected = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        self.assertEqual(code, 200)
        self.assertEqual(projected["status"], "cancelled")
        self.assertEqual(projected["stop_reason"], "user_cancelled")
        self._assert_clean(projected)

    def test_cancel_rejects_terminal_states(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.COMPLETED,
                         StopReason.OBJECTIVE_SATISFIED)
        status, payload = self._request(
            f"/api/research/runs/{run['run_id']}/cancel", method="POST",
            body={"project_id": "projA"})
        self.assertEqual(status, 409)
        self.assertIn("error", payload)

    def test_cancel_ownership_required(self) -> None:
        run = self._create_run()
        status, payload = self._request(
            f"/api/research/runs/{run['run_id']}/cancel", method="POST",
            body={})
        self.assertEqual(status, 400)
        status, payload = self._request(
            f"/api/research/runs/{run['run_id']}/cancel", method="POST",
            body={"project_id": "other"})
        self.assertEqual(status, 404)

    # -- resume ---------------------------------------------------------------

    def test_resume_from_interrupted_then_guarded_repeat(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.INTERRUPTED,
                         StopReason.INTERRUPTED_EXTERNAL_ACTION)
        path = f"/api/research/runs/{run['run_id']}/resume"

        status, resumed = self._request(path, method="POST",
                                        body={"project_id": "projA"})
        self.assertEqual(status, 200)
        self.assertEqual(resumed["status"], "planning")
        self.assertEqual(set(resumed), {"run_id", "status", "updated_at"})

        _, projected = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        self.assertEqual(projected["status"], "planning")
        self.assertIsNone(projected["stop_reason"])

        # Guarded duplicate resume: PLANNING -> PLANNING safe no-op.
        status, repeat = self._request(path, method="POST",
                                       body={"project_id": "projA"})
        self.assertEqual(status, 200)
        self.assertEqual(repeat["status"], "planning")
        self._assert_clean(repeat)

    def test_resume_rejects_disallowed_states(self) -> None:
        run = self._create_run()
        path = f"/api/research/runs/{run['run_id']}/resume"
        for stored, stop in (
                (RunStatus.RUNNING, None),
                (RunStatus.WAITING_APPROVAL, None),
                (RunStatus.CANCELLED, StopReason.USER_CANCELLED),
                (RunStatus.COMPLETED, StopReason.OBJECTIVE_SATISFIED)):
            self._set_status(run["run_id"], stored, stop)
            status, payload = self._request(path, method="POST",
                                            body={"project_id": "projA"})
            self.assertEqual(status, 409)
            self.assertIn("error", payload)

    def test_resume_ownership_required(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.INTERRUPTED,
                         StopReason.INTERRUPTED_EXTERNAL_ACTION)
        status, _ = self._request(
            f"/api/research/runs/{run['run_id']}/resume", method="POST",
            body={})
        self.assertEqual(status, 400)
        status, _ = self._request(
            f"/api/research/runs/{run['run_id']}/resume", method="POST",
            body={"project_id": "elsewhere"})
        self.assertEqual(status, 404)

    # -- projection completeness ---------------------------------------------

    def test_projection_covers_every_stored_status(self) -> None:
        stored = {member.value for member in RunStatus}
        self.assertEqual(stored, set(STATUS_PROJECTION))

    # -- execution continuation -----------------------------------------------

    def _gateway(self):
        return self.server.amca_context.research_gateway

    def test_execute_requires_ownership_and_planning_state(self) -> None:
        run = self._create_run()
        path = f"/api/research/runs/{run['run_id']}/execute"
        status, payload = self._request(path, method="POST", body={})
        self.assertEqual(status, 400)
        status, payload = self._request(path, method="POST",
                                        body={"project_id": "other"})
        self.assertEqual(status, 404)

    def test_execute_starts_injected_engine_once_and_guards(self) -> None:
        run = self._create_run()
        calls: list[str] = []
        invoked = threading.Event()

        def engine(run_id, runs_store):
            calls.append(run_id)
            invoked.set()

        self._gateway().set_execution_engine(engine)
        try:
            path = f"/api/research/runs/{run['run_id']}/execute"
            status, payload = self._request(path, method="POST",
                                            body={"project_id": "projA"})
            self.assertEqual(status, 200)
            self.assertEqual(payload, {"run_id": run["run_id"],
                                       "status": "running"})
            self.assertTrue(invoked.wait(timeout=5))

            # Duplicate execution is impossible while RUNNING.
            status, repeat = self._request(path, method="POST",
                                           body={"project_id": "projA"})
            self.assertEqual(status, 409)
            self.assertIn("error", repeat)
            self.assertEqual(len(calls), 1)

            code, projected = self._json(
                f"/api/research/runs/{run['run_id']}/status"
                "?project_id=projA")
            self.assertEqual(code, 200)
            self.assertEqual(projected["status"], "running")
        finally:
            self._gateway().set_execution_engine(None)

    def test_execute_rejects_terminal_runs(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.COMPLETED,
                         StopReason.OBJECTIVE_SATISFIED)
        status, payload = self._request(
            f"/api/research/runs/{run['run_id']}/execute", method="POST",
            body={"project_id": "projA"})
        self.assertEqual(status, 409)

    def test_execute_rejects_adaptive_without_model_planner(self) -> None:
        run = self._create_run(mode="adaptive")
        status, payload = self._request(
            f"/api/research/runs/{run['run_id']}/execute", method="POST",
            body={"project_id": "projA"})
        self.assertEqual(status, 409)
        self.assertIn("model-backed planner", payload["error"])
        # Truthful state: still planning, not failed.
        _, projected = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        self.assertEqual(projected["status"], "planning")

    def test_execute_real_orchestrator_zero_network_gates_at_approval(self):
        """Create -> plan -> execute against the REAL deterministic stack.

        No provider ships configured and the project holds no approval.
        Truthful bounded outcomes: on an empty R1 store the planner has no
        open questions/claims and stops objective_satisfied (COMPLETED);
        an unapproved project with researchable questions would gate at
        Tool Fabric approval (WAITING_APPROVAL) before any backend runs.
        """
        from amca.research_gateway import DefaultRunExecutor
        run = self._create_run()
        self._gateway().set_execution_engine(
            DefaultRunExecutor(self.database,
                               payloads_root=Path(self.temporary.name)))
        try:
            status, payload = self._request(
                f"/api/research/runs/{run['run_id']}/execute",
                method="POST", body={"project_id": "projA"})
            self.assertEqual(status, 200)
            deadline = threading.Event()
            final_code, final = None, None
            for _ in range(100):
                final_code, final = self._json(
                    f"/api/research/runs/{run['run_id']}/status"
                    "?project_id=projA")
                if final["status"] != "running":
                    break
                if deadline.wait(timeout=0.1):
                    break
            self.assertIn(final["status"],
                          {"completed", "partial", "waiting_approval"})
            if final["status"] == "waiting_approval":
                self.assertIsNone(final["stop_reason"])
            self._assert_clean(final)
        finally:
            self._gateway().set_execution_engine(None)


    # -- crash recovery reconciliation ---------------------------------------

    def test_reconcile_marks_orphaned_running_as_interrupted(self) -> None:
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.RUNNING)
        gateway = self._gateway()
        recovered = gateway.reconcile_orphaned_runs()
        self.assertEqual(recovered, 1)
        code, projected = self._json(
            f"/api/research/runs/{run['run_id']}/status?project_id=projA")
        self.assertEqual(code, 200)
        self.assertEqual(projected["status"], "interrupted")
        self.assertEqual(projected["stop_reason"],
                         "interrupted_external_action")
        self._assert_clean(projected)

    def test_reconcile_leaves_legitimate_states_alone(self) -> None:
        planned_run = self._create_run()
        done_run = self._create_run(objective="already done")
        waiting_run = self._create_run(objective="awaiting approval")
        self._set_status(done_run["run_id"], RunStatus.COMPLETED,
                         StopReason.OBJECTIVE_SATISFIED)
        self._set_status(waiting_run["run_id"], RunStatus.WAITING_APPROVAL)
        recovered = self._gateway().reconcile_orphaned_runs()
        self.assertEqual(recovered, 0)
        _, planned = self._json(
            f"/api/research/runs/{planned_run['run_id']}/status"
            "?project_id=projA")
        self.assertEqual(planned["status"], "planning")
        _, done = self._json(
            f"/api/research/runs/{done_run['run_id']}/status"
            "?project_id=projA")
        self.assertEqual(done["status"], "completed")
        _, waiting = self._json(
            f"/api/research/runs/{waiting_run['run_id']}/status"
            "?project_id=projA")
        self.assertEqual(waiting["status"], "waiting_approval")

    def test_restart_reconciles_and_full_resume_loop_works(self) -> None:
        """RUNNING -> (simulated crash) -> restart -> interrupted ->
        resume -> planning -> execute -> running again."""
        run = self._create_run()
        self._set_status(run["run_id"], RunStatus.RUNNING)

        # A fresh server over the same database simulates the restart.
        second = create_server(host="127.0.0.1", port=0,
                               root=Path(__file__).resolve().parents[1],
                               database=self.database)
        second_thread = threading.Thread(target=second.serve_forever,
                                         daemon=True)
        second_thread.start()
        try:
            # The restarted process issued its own session token.
            session_request = Request(
                f"http://127.0.0.1:{second.server_port}/api/session")
            with urlopen(session_request) as response:
                restart_token = str(json.loads(response.read())["session_token"])
            request = Request(
                f"http://127.0.0.1:{second.server_port}"
                f"/api/research/runs/{run['run_id']}/status?project_id=projA")
            request.add_header("Origin", "http://localhost:1420")
            request.add_header("X-AMCA-Session", restart_token)
            with urlopen(request) as response:
                projected = json.loads(response.read())
            self.assertEqual(projected["status"], "interrupted")

            # The full recovery loop through the ORIGINAL server.
            status, resumed = self._request(
                f"/api/research/runs/{run['run_id']}/resume",
                method="POST", body={"project_id": "projA"})
            self.assertEqual(status, 200)
            self.assertEqual(resumed["status"], "planning")

            calls: list[str] = []
            started = threading.Event()

            def engine(rid, runs_store):
                calls.append(rid)
                started.set()

            self._gateway().set_execution_engine(engine)
            try:
                status, payload = self._request(
                    f"/api/research/runs/{run['run_id']}/execute",
                    method="POST", body={"project_id": "projA"})
                self.assertEqual(status, 200)
                self.assertEqual(payload["status"], "running")
                self.assertTrue(started.wait(timeout=5))
            finally:
                self._gateway().set_execution_engine(None)
        finally:
            second.shutdown()
            second.server_close()
            second_thread.join(timeout=2)


    # -- studio alpha workflow reads -----------------------------------------

    def _seed_evidence_and_synthesis(self, *, project_id: str = "projA"):
        """Seed source/snapshot + plan/synthesis through public store APIs."""
        from amca.research_source import ResearchStore
        from amca.research_synthesis import (
            Grounding,
            ResearchSynthesisStore,
            SynthesisOutputForm,
        )
        root = Path(self.temporary.name)
        research = ResearchStore(self.database,
                                 storage_root=root / "content")
        session_id = research.create_session(
            project_id=project_id, title="workflow seed",
            purpose="studio-alpha")["session_id"]
        source = research.create_source(
            session_id, source_kind="web",
            canonical_origin="https://example.com/batteries",
            title="Battery study")
        snapshot = research.add_snapshot(
            source["source_id"], project_id=project_id,
            observed_at="2026-08-26T00:00:00+00:00",
            content=b"Lithium batteries hold charge well.",
            content_type="text/plain")
        synthesis = ResearchSynthesisStore(self.database,
                                           storage_root=root / "content")
        plan = synthesis.create_plan(
            session_id=session_id, project_id=project_id,
            question_ids=[], claim_ids=[],
            output_form=SynthesisOutputForm.BRIEF)
        version = synthesis.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=Grounding.GROUNDED,
            draft={"sections": [{"heading": "Findings",
                                 "text": "Batteries hold charge."}],
                   "claims_used": [], "citations": []})
        return {"source": source, "snapshot": snapshot,
                "plan": plan, "version": version}

    def _bound_run_for_seeded_session(self) -> dict:
        """Create a run row on the same session the stores seeded."""
        from amca.research_orchestrator import ResearchLimits
        return self.store.create_run(
            project_id="projA", session_id=self._last_session_id(),
            objective="bound to seeded session", limits=ResearchLimits())

    def test_workflow_view_snapshot_content(self) -> None:
        seeded = self._seed_evidence_and_synthesis()
        bound = self._bound_run_for_seeded_session()
        path = (f"/api/research/runs/{bound['run_id']}/evidence/"
                f"{seeded['snapshot']['snapshot_id']}?project_id=projA")
        status, payload = self._json(path)
        self.assertEqual(status, 200)
        self.assertEqual(payload["content_digest"],
                         seeded["snapshot"]["content_digest"])
        self.assertIn("Lithium batteries", payload["content"])
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["run_id"], bound["run_id"])
        self._assert_clean(payload)

    def test_workflow_snapshot_ownership_is_strict(self) -> None:
        seeded = self._seed_evidence_and_synthesis()
        bound = self._bound_run_for_seeded_session()
        wrong_code, wrong = self._json(
            f"/api/research/runs/{bound['run_id']}/evidence/"
            f"{seeded['snapshot']['snapshot_id']}?project_id=projB")
        unknown_code, unknown = self._json(
            f"/api/research/runs/{bound['run_id']}/evidence/"
            "snap-unknown?project_id=projB")
        self.assertEqual(wrong_code, 404)
        self.assertEqual(unknown_code, 404)
        self.assertEqual(wrong, unknown)

    def test_workflow_view_synthesis_result(self) -> None:
        self._seed_evidence_and_synthesis()
        bound = self._bound_run_for_seeded_session()
        status, payload = self._json(
            f"/api/research/runs/{bound['run_id']}/synthesis"
            "?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        entry = payload["plans"][0]
        self.assertEqual(entry["output_form"], "brief")
        self.assertEqual(entry["version"]["grounding_status"], "grounded")
        self.assertEqual(entry["version"]["sections"][0]["text"],
                         "Batteries hold charge.")
        self.assertFalse(entry["version"]["truncated"])
        self._assert_clean(payload)

    def _last_session_id(self) -> str:
        import sqlite3 as _sq
        connection = _sq.connect(self.database)
        try:
            row = connection.execute(
                "SELECT session_id FROM research_sessions "
                "ORDER BY created_at DESC LIMIT 1").fetchone()
        finally:
            connection.close()
        return row[0]

    def test_workflow_synthesis_empty_for_fresh_run(self) -> None:
        run = self._create_run()
        status, payload = self._json(
            f"/api/research/runs/{run['run_id']}/synthesis"
            "?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"run_id": run["run_id"], "plans": [],
                                   "count": 0})
        status, _ = self._json(
            f"/api/research/runs/{run['run_id']}/synthesis")
        self.assertEqual(status, 400)


    # -- alpha backend hardening ----------------------------------------------

    def test_project_id_length_is_bounded(self) -> None:
        run = self._create_run()
        long_project = "p" * 129
        status, payload = self._json(
            f"/api/research/runs/{run['run_id']}/status"
            f"?project_id={long_project}")
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_bare_evidence_path_returns_404_not_provenance(self) -> None:
        run = self._create_run()
        status, payload = self._json(
            f"/api/research/runs/{run['run_id']}/evidence?project_id=projA")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)
        # The visibility provenance payload must never leak through the
        # evidence route shape.
        self.assertNotIn("sources", payload)

    def test_synthesis_sections_truncate_at_section_granularity(self) -> None:
        from amca.research_source import ResearchStore
        from amca.research_synthesis import (
            Grounding,
            ResearchSynthesisStore,
            SynthesisOutputForm,
        )
        root = Path(self.temporary.name)
        research = ResearchStore(self.database,
                                 storage_root=root / "content")
        session_id = research.create_session(
            project_id="projA", title="truncation seed",
            purpose="hardening")["session_id"]
        synthesis = ResearchSynthesisStore(self.database,
                                           storage_root=root / "content")
        plan = synthesis.create_plan(
            session_id=session_id, project_id="projA",
            question_ids=[], claim_ids=[],
            output_form=SynthesisOutputForm.REPORT)
        synthesis.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=Grounding.GROUNDED,
            draft={"sections": [
                {"heading": "kept", "text": "small"},
                {"heading": "dropped", "text": "x" * 21_000},
            ], "claims_used": [], "citations": []})
        bound = self.store.create_run(
            project_id="projA", session_id=session_id,
            objective="truncation probe",
            limits=ResearchLimits())
        status, payload = self._json(
            f"/api/research/runs/{bound['run_id']}/synthesis"
            "?project_id=projA")
        self.assertEqual(status, 200)
        version = payload["plans"][0]["version"]
        self.assertTrue(version["truncated"])
        # Well-formed entries only: nothing cut mid-JSON.
        self.assertEqual(len(version["sections"]), 1)
        self.assertEqual(version["sections"][0]["heading"], "kept")
        self._assert_clean(payload)


    # -- ZC-AMCA-ALPHA-FINAL-BACKEND-VALIDATION-01 ----------------------------

    def test_alpha_end_to_end_research_flow(self) -> None:
        """Create -> plan -> execute -> observe -> evidence -> synthesis
        -> artifact reuse, over the REAL deterministic stack (zero
        network), as one continuous flow."""
        from amca.artifacts import ArtifactStore
        from amca.research_gateway import DefaultRunExecutor
        from amca.research_source import ResearchStore
        from amca.research_synthesis import (
            Grounding,
            ResearchSynthesisStore,
            SynthesisOutputForm,
        )

        root = Path(self.temporary.name)
        research = ResearchStore(self.database,
                                 storage_root=root / "content")
        session_id = research.create_session(
            project_id="projA", title="alpha final",
            purpose="validation")["session_id"]
        source = research.create_source(
            session_id, source_kind="web",
            canonical_origin="https://example.com/alpha",
            title="Alpha source")
        snapshot = research.add_snapshot(
            source["source_id"], project_id="projA",
            observed_at="2026-08-26T00:00:00+00:00",
            content=b"Grounded alpha evidence body.",
            content_type="text/plain")
        synthesis_store = ResearchSynthesisStore(
            self.database, storage_root=root / "content")
        plan = synthesis_store.create_plan(
            session_id=session_id, project_id="projA",
            question_ids=[], claim_ids=[],
            output_form=SynthesisOutputForm.REPORT)
        synthesis_store.save_synthesis_version(
            plan_id=plan["plan_id"], grounding_status=Grounding.GROUNDED,
            draft={"sections": [{"heading": "Result",
                                 "text": "Alpha synthesis conclusion."}],
                   "claims_used": [], "citations": []})

        run = self.store.create_run(
            project_id="projA", session_id=session_id,
            objective="final alpha validation",
            limits=ResearchLimits())

        # Execute: REAL orchestrator pass, no provider configured.
        self._gateway().set_execution_engine(
            DefaultRunExecutor(self.database,
                               payloads_root=root))
        try:
            status, payload = self._request(
                f"/api/research/runs/{run['run_id']}/execute",
                method="POST", body={"project_id": "projA"})
            self.assertEqual(status, 200)
            self.assertEqual(payload["status"], "running")
            deadline = threading.Event()
            for _ in range(100):
                _, observed = self._json(
                    f"/api/research/runs/{run['run_id']}/status"
                    "?project_id=projA")
                if observed["status"] != "running":
                    break
                if deadline.wait(timeout=0.1):
                    break
            self.assertIn(observed["status"],
                          {"completed", "partial", "waiting_approval"})
        finally:
            self._gateway().set_execution_engine(None)

        # Observe -> evidence content view.
        status, evidence = self._json(
            f"/api/research/runs/{run['run_id']}/evidence/"
            f"{snapshot['snapshot_id']}?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(evidence["content_digest"],
                         snapshot["content_digest"])
        self.assertIn("alpha evidence body", evidence["content"])

        # Observe -> synthesis result view.
        status, result = self._json(
            f"/api/research/runs/{run['run_id']}/synthesis"
            "?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(result["count"], 1)
        version = result["plans"][0]["version"]
        self.assertEqual(version["grounding_status"], "grounded")

        # Artifact reuse: export the synthesis text through the same
        # public authoring path the Studio artifact APIs use.
        artifacts_dir = root / "artifacts"
        store = ArtifactStore(artifacts_dir / "amca.db",
                              payloads_root=artifacts_dir / "payloads")
        authored = store.author(
            project_id="projA",
            artifact_type="markdown",
            title="Alpha synthesis export",
            content=version["sections"][0]["text"],
            created_by="alpha_validation")
        meta = store.get(authored["artifact_id"])
        self.assertEqual(meta["title"], "Alpha synthesis export")
        self.assertGreaterEqual(meta["current_version"], 1)
        exported = store.read_source(authored["artifact_id"])
        self.assertEqual(exported["content"], "Alpha synthesis conclusion.")
        self._assert_clean(evidence)
        self._assert_clean(result)

    def test_contract_surface_complete_and_documented(self) -> None:
        """All seven gateway operations exist and every documented route
        string appears in both the code module and the contract doc."""
        gateway = self._gateway()
        for operation in ("create_run", "get_status", "cancel_run",
                          "resume_run", "execute_run", "snapshot_content",
                          "run_synthesis"):
            self.assertTrue(callable(getattr(gateway, operation, None)),
                            f"missing gateway operation: {operation}")
        doc_path = (Path(__file__).resolve().parents[1]
                    / "docs" / "research-api-contract.md")
        contract = doc_path.read_text(encoding="utf-8")
        for fragment in (
                "POST /api/research/runs",
                "/api/research/runs/{run_id}/status",
                "/api/research/runs/{run_id}/cancel",
                "/api/research/runs/{run_id}/resume",
                "/api/research/runs/{run_id}/execute",
                "/api/research/runs/{run_id}/evidence/",
                "/api/research/runs/{run_id}/synthesis"):
            self.assertIn(fragment, contract)


    # -- ZC-AMCA-SYNTHESIS-HISTORY-TIMELINE-01 ------------------------------

    def _seed_history(self, version_count: int) -> tuple[dict, dict]:
        from amca.research_source import ResearchStore
        from amca.research_synthesis import (
            Grounding,
            ResearchSynthesisStore,
            SynthesisOutputForm,
        )
        root = Path(self.temporary.name)
        research = ResearchStore(self.database,
                                 storage_root=root / "content")
        session_id = research.create_session(
            project_id="projA", title="history seed",
            purpose="timeline")["session_id"]
        synthesis = ResearchSynthesisStore(self.database,
                                           storage_root=root / "content")
        plan = synthesis.create_plan(
            session_id=session_id, project_id="projA",
            question_ids=[], claim_ids=[],
            output_form=SynthesisOutputForm.REPORT)
        for i in range(version_count):
            synthesis.save_synthesis_version(
                plan_id=plan["plan_id"], grounding_status=Grounding.GROUNDED,
                draft={"sections": [{"heading": f"v{i + 1}",
                                     "text": f"body {i + 1}"}],
                       "claims_used": [], "citations": []})
        run = self.store.create_run(
            project_id="projA", session_id=session_id,
            objective="history probe", limits=ResearchLimits())
        return run, plan

    def test_history_success_ordering_and_key_contract(self) -> None:
        run, _ = self._seed_history(3)
        status, payload = self._json(
            f"/api/research/runs/{run['run_id']}/synthesis/history"
            "?project_id=projA")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"run_id", "plans", "count"})
        self.assertEqual(payload["count"], 1)
        entry = payload["plans"][0]
        self.assertEqual(set(entry), {"plan_id", "output_form",
                                      "created_at", "versions",
                                      "versions_truncated"})
        # Documented ordering: seq ASCENDING (chronological evolution).
        self.assertEqual([v["seq"] for v in entry["versions"]],
                         [1, 2, 3])
        for v in entry["versions"]:
            self.assertEqual(
                set(v), {"version_id", "seq", "grounding_status",
                         "output_form", "content_digest", "created_at"})
            self.assertNotIn("draft", v)
        self.assertFalse(entry["versions_truncated"])
        self._assert_clean(payload)

    def test_history_empty_for_fresh_run(self) -> None:
        fresh = self._create_run()
        _, payload = self._json(
            f"/api/research/runs/{fresh['run_id']}/synthesis/history"
            "?project_id=projA")
        self.assertEqual(payload, {"run_id": fresh["run_id"],
                                   "plans": [], "count": 0})

    def test_history_plan_without_versions_has_empty_timeline(self) -> None:
        from amca.research_synthesis import (
            ResearchSynthesisStore,
            SynthesisOutputForm,
        )
        root = Path(self.temporary.name)
        research_store = None
        from amca.research_source import ResearchStore as _RS
        research_store = _RS(self.database, storage_root=root / "content")
        session_id = research_store.create_session(
            project_id="projA", title="empty history",
            purpose="timeline")["session_id"]
        synthesis = ResearchSynthesisStore(self.database,
                                           storage_root=root / "content")
        synthesis.create_plan(session_id=session_id, project_id="projA",
                              question_ids=[], claim_ids=[],
                              output_form=SynthesisOutputForm.BRIEF)
        bound = self.store.create_run(
            project_id="projA", session_id=session_id,
            objective="empty timeline", limits=ResearchLimits())
        _, payload = self._json(
            f"/api/research/runs/{bound['run_id']}/synthesis/history"
            "?project_id=projA")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["plans"][0]["versions"], [])
        self.assertFalse(payload["plans"][0]["versions_truncated"])

    def test_history_project_isolation_auth_and_bounds(self) -> None:
        run, _ = self._seed_history(1)
        base = f"/api/research/runs/{run['run_id']}/synthesis/history"
        wrong_code, wrong = self._json(f"{base}?project_id=projB")
        unknown_code, unknown = self._json(
            "/api/research/runs/run-unknown/synthesis/history"
            "?project_id=projB")
        self.assertEqual(wrong_code, 404)
        self.assertEqual(unknown_code, 404)
        self.assertEqual(wrong, unknown)
        status, _ = self._json(base)
        self.assertEqual(status, 400)
        status, _ = self._json(f"{base}?project_id=projA",
                               authorized=False)
        self.assertEqual(status, 401)

    def test_history_bounded_with_truthful_truncation(self) -> None:
        run, _ = self._seed_history(51)
        _, payload = self._json(
            f"/api/research/runs/{run['run_id']}/synthesis/history"
            "?project_id=projA")
        entry = payload["plans"][0]
        self.assertTrue(entry["versions_truncated"])
        self.assertEqual(len(entry["versions"]), 50)
        self.assertEqual([v["seq"] for v in entry["versions"]],
                         list(range(1, 51)))
        self._assert_clean(payload)


if __name__ == "__main__":
    unittest.main()
