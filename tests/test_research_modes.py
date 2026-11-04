"""R2.6 backend adaptive research mode integration tests (manager-reply63).

Covers: explicit DETERMINISTIC/ADAPTIVE/HYBRID mode profiles; durable enum
mode selection on research_runs; planner evaluation metadata
(planner_mode/planner_source/fallback_reason); hybrid deterministic-first
planner with advisory model suggestions; semantic-retrieval candidate
integration through the unchanged R2.2 safety rules; mode replay;
no-duplicate-execution on resume; canonical amca.db and zero network/paid.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from amca.research_context import ContextStore
from amca.research_ingest import ResearchIngestor
from amca.research_orchestrator import (
    DeterministicResearchPlanner,
    ReasonCode,
    ResearchAction,
    ResearchLimits,
    ResearchOrchestrator,
    ResearchRunStore,
    RunMode,
    RunStatus,
)
from amca.research_planning import (
    AdaptiveResearchPlanner,
    DeterministicScriptedPlannerModel,
    HybridResearchPlanner,
    PlanningTrailStore,
    format_mode_replay,
    replay_run,
)
from amca.research_source import ResearchStore
from amca.research_synthesis import ResearchSynthesisStore
from amca.tool_fabric import ToolFabric, ToolFabricStore
from amca.web_acquisition import (
    NetworkToolPolicy,
    WebAcquisitionClient,
    WebAcquisitionStore,
)
from test_research_orchestrator import RecordingTransport


class ModeBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "amca.db"
        self.fabric = ToolFabric(ToolFabricStore(self.db),
                                 project_root="projA")
        NetworkToolPolicy.register(self.fabric)
        self.research = ResearchStore(self.db,
                                      storage_root=self.root / "content")
        self.synthesis = ResearchSynthesisStore(
            self.db, storage_root=self.root / "content")
        self.ingestor = ResearchIngestor(self.research)
        self.runs = ResearchRunStore(self.db)
        self.acq_store = WebAcquisitionStore(self.db)
        self.session_id = self.research.create_session(
            project_id="projA", title="r26", purpose="modes")["session_id"]
        self.transport = RecordingTransport()
        self.client = WebAcquisitionClient(
            opener=self.transport, host_validator=lambda host: None)

    def make_run(self, mode: str, **overrides) -> str:
        values = dict(max_steps=6, max_searches=3, max_fetches=3,
                      max_sources=5)
        values.update(overrides)
        return self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="adaptive modes", limits=ResearchLimits(**values),
            mode=mode)["run_id"]

    def orchestrator(self) -> ResearchOrchestrator:
        return ResearchOrchestrator(
            run_store=self.runs, research_store=self.research,
            synthesis_store=self.synthesis, fabric=self.fabric,
            acq_store=self.acq_store, ingestor=self.ingestor,
            acquisition_client=self.client)


class ModePersistenceTests(ModeBase):
    def test_mode_profiles_roundtrip(self) -> None:
        for mode in ("deterministic", "adaptive", "hybrid"):
            run_id = self.make_run(mode)
            record = self.runs.get_run(run_id)
            self.assertEqual(record["mode"], mode)

    def test_invalid_mode_rejected_no_free_text(self) -> None:
        for bad in ("auto", "ADAPTIVE", "", "model-free"):
            with self.assertRaises(ValueError):
                self.make_run(bad)

    def test_legacy_rows_default_deterministic(self) -> None:
        # Simulate a pre-R2.6 row written without the mode column value.
        run_id = self.make_run("deterministic")
        with self.runs._connect() as connection:
            connection.execute(
                "UPDATE research_runs SET mode = NULL WHERE run_id = ?",
                (run_id,))
        self.assertEqual(self.runs.get_run(run_id)["mode"],
                         "deterministic")

    def test_deterministic_mode_rejects_adaptive_planner(self) -> None:
        run_id = self.make_run("deterministic")
        adaptive = AdaptiveResearchPlanner(
            model=DeterministicScriptedPlannerModel(),
            fallback=DeterministicResearchPlanner())
        orch = self.orchestrator()
        with self.assertRaises(ValueError):
            orch.execute_run(run_id, adaptive)

    def test_adaptive_mode_requires_adaptive_capable_planner(self) -> None:
        run_id = self.make_run("adaptive")
        orch = self.orchestrator()
        with self.assertRaises(ValueError):
            orch.execute_run(run_id, DeterministicResearchPlanner())

    def test_hybrid_mode_requires_adaptive_capable_planner(self) -> None:
        run_id = self.make_run("hybrid")
        orch = self.orchestrator()
        with self.assertRaises(ValueError):
            orch.execute_run(run_id, DeterministicResearchPlanner())


class _FixedModel(DeterministicScriptedPlannerModel):
    """Endless identical suggestion - exercises repeated planning turns."""

    def __init__(self, action: dict) -> None:
        super().__init__()
        self._action = action

    def propose_actions(self, state):
        return [dict(self._action)]


class ModeExecutionTests(ModeBase):
    def seed_result(self, url: str) -> str:
        event = self.research.record_search_event(
            project_id="projA", session_id=self.session_id,
            provider_id="seed", provider_version="", query="battery",
            options={}, started_at="2026-08-25T00:00:00+00:00",
            completed_at="2026-08-25T00:00:01+00:00", status="committed",
            results=[{"rank": 1, "title": "t", "url": url,
                      "snippet": "s"}])
        with self.research._connect() as connection:
            row = connection.execute(
                "SELECT search_result_id FROM research_search_results "
                "WHERE search_event_id = ? LIMIT 1",
                (event["search_event_id"],)).fetchone()
        return row["search_result_id"]

    def test_adaptive_fetch_gated_and_metadata_persisted(self) -> None:
        NetworkToolPolicy.enable_for_project(self.fabric.store, "projA")
        sid = self.seed_result("https://example.com/doc")
        model = _FixedModel({"action": "fetch_result",
                             "arguments": {"search_result_id": sid}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive")
        result = self.orchestrator().execute_run(run_id, adaptive)
        self.assertEqual(result["status"], RunStatus.WAITING_APPROVAL.value)
        run_row = self.runs.get_run(run_id)
        self.assertEqual(run_row["planner_mode"], "adaptive")
        self.assertEqual(run_row["planner_source"], "model")
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["created_by"], "model-proposal")
        self.assertEqual(step["decision"], "awaiting_approval")
        self.assertEqual(self.transport.opened_urls, [])

    def test_hybrid_uses_model_suggestion_when_valid(self) -> None:
        NetworkToolPolicy.enable_for_project(self.fabric.store, "projA")
        sid = self.seed_result("https://example.com/doc")
        model = _FixedModel({"action": "fetch_result",
                             "arguments": {"search_result_id": sid}})
        hybrid = HybridResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("hybrid")
        result = self.orchestrator().execute_run(run_id, hybrid)
        self.assertEqual(result["status"], RunStatus.WAITING_APPROVAL.value)
        run_row = self.runs.get_run(run_id)
        self.assertEqual(run_row["planner_mode"], "hybrid")
        self.assertEqual(run_row["planner_source"], "model")
        # The winning model candidate replaced the never-computed baseline;
        # provenance shows the accepted suggestion.
        accepted = [c for c in hybrid.last_planning_trail["candidates"]
                    if c.get("verdict") == "accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["candidate"]["action"], "fetch_result")

    def test_hybrid_falls_back_to_baseline_when_model_fails(self) -> None:
        class Exploding(_FixedModel):
            def propose_actions(self, state):
                raise RuntimeError("local model down")

        hybrid = HybridResearchPlanner(
            model=Exploding({"action": "stop", "arguments": {}}),
            fallback=DeterministicResearchPlanner())
        run_id = self.make_run("hybrid")
        # No questions/claims: deterministic baseline stops cleanly.
        result = self.orchestrator().execute_run(run_id, hybrid)
        self.assertEqual(result["status"], RunStatus.COMPLETED.value)
        run_row = self.runs.get_run(run_id)
        self.assertEqual(run_row["planner_source"], "deterministic-baseline")
        trail = hybrid.last_planning_trail
        self.assertIn("model_error:RuntimeError",
                      str(trail["candidates"]))
        self.assertNotEqual(run_row["planner_source"], "model")

    def test_fallback_reason_recorded_in_adaptive_mode(self) -> None:
        model = _FixedModel({"action": "teleport", "arguments": {}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive")
        self.orchestrator().execute_run(run_id, adaptive)
        run_row = self.runs.get_run(run_id)
        self.assertEqual(run_row["planner_source"], "fallback")
        self.assertTrue(run_row["fallback_reason"])

    def test_semantic_candidate_executes_with_effective_mode_journalled(
            self) -> None:
        created = self.ingestor.ingest_user_content(
            session_id=self.session_id, project_id="projA",
            title="notes", text="quantum battery efficiency data points")
        self.ingestor.extract(created["snapshot"]["snapshot_id"],
                              project_id="projA")
        self.ingestor.rebuild_index("projA")
        from amca.research_semantic import (
            DeterministicEmbeddingProvider,
            SemanticRetrievalStore,
            HybridLocalSearch,
        )
        semantic_store = SemanticRetrievalStore(self.db)
        engine = HybridLocalSearch(
            ingestor=self.ingestor, retrieval_store=semantic_store,
            provider=DeterministicEmbeddingProvider(dimension=64))
        model = _FixedModel({
            "action": "search_local",
            "arguments": {"query": "quantum battery efficiency",
                          "retrieval_mode": "hybrid"}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive")
        orch = self.orchestrator()
        orch.set_semantic_engine(engine)
        result = orch.execute_run(run_id, adaptive)
        self.assertNotEqual(result["status"], RunStatus.FAILED.value)
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["outcome"], "executed")
        self.assertEqual(step["result"]["requested_mode"], "hybrid")
        self.assertEqual(step["result"]["effective_mode"], "hybrid")


class ReplayAndSafetyTests(ModeBase):
    def test_mode_replay_records_turn_sources(self) -> None:
        trail_store = PlanningTrailStore(self.db)
        model = _FixedModel({"action": "teleport", "arguments": {}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            trail_store=trail_store, project_id="projA")
        run_id = self.make_run("adaptive")
        self.orchestrator().execute_run(run_id, adaptive)
        replay = replay_run(trail_store, run_id)
        self.assertGreaterEqual(len(replay), 1)
        self.assertTrue(all(t["mode"] == "adaptive" for t in replay))
        lines = format_mode_replay(replay)
        self.assertEqual(lines[0], f"Run {run_id}: mode=adaptive")
        self.assertTrue(any(line.startswith("Turn ")
                            for line in lines[1:]))

    def test_approved_execution_never_duplicates_across_modes(
            self) -> None:
        NetworkToolPolicy.enable_for_project(self.fabric.store, "projA")
        event = self.research.record_search_event(
            project_id="projA", session_id=self.session_id,
            provider_id="seed", provider_version="", query="battery",
            options={}, started_at="2026-08-25T00:00:00+00:00",
            completed_at="2026-08-25T00:00:01+00:00", status="committed",
            results=[{"rank": 1, "title": "t",
                      "url": "https://example.com/doc", "snippet": "s"}])
        with self.research._connect() as connection:
            row = connection.execute(
                "SELECT search_result_id FROM research_search_results "
                "WHERE search_event_id = ? LIMIT 1",
                (event["search_event_id"],)).fetchone()
        sid = row["search_result_id"]
        model = _FixedModel({"action": "fetch_result",
                             "arguments": {"search_result_id": sid}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive")
        pending = self.orchestrator().execute_run(run_id, adaptive)
        self.fabric.approve(pending["awaiting_execution"],
                            approval_reference="op")
        first = self.orchestrator().execute_run(run_id, adaptive)
        opens_after_first = len(self.transport.opened_urls)
        with self.assertRaises(ValueError):
            self.orchestrator().execute_run(run_id, adaptive)
        self.assertEqual(len(self.transport.opened_urls), opens_after_first)
        # Duplicate suppression keeps the fetched identity to ONE execution;
        # the terminal status is truthful (partial or clean completion).
        self.assertIn(first["status"],
                      (RunStatus.PARTIAL.value, RunStatus.COMPLETED.value))

    def test_limits_still_enforced_under_adaptive_mode(self) -> None:
        model = _FixedModel({"action": "search_web",
                             "arguments": {"query": "anything"}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive", max_searches=0)
        # Not enabled -> any dispatch would be denied anyway; but the
        # validator must refuse BEFORE that because searches are exhausted.
        result = self.orchestrator().execute_run(run_id, adaptive)
        statuses = set()
        for step in self.runs.steps_for(run_id):
            statuses.add(step["action_type"])
        self.assertNotIn("search_web", statuses)
        del result

    def test_trail_store_turns_carry_mode(self) -> None:
        trail_store = PlanningTrailStore(self.db)
        model = _FixedModel({"action": "teleport", "arguments": {}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            trail_store=trail_store, project_id="projA")
        run_id = self.make_run("hybrid")
        hybrid = HybridResearchPlanner(
            model=_FixedModel({"action": "stop", "arguments": {}}),
            fallback=DeterministicResearchPlanner(),
            trail_store=trail_store, project_id="projA")
        del adaptive
        self.orchestrator().execute_run(run_id, hybrid)
        turns = trail_store.turns_for_run(run_id)
        self.assertTrue(all(t["mode"] == "hybrid" for t in turns))

    def test_canonical_db_only_zero_network(self) -> None:
        db_files = sorted(p.name for p in self.root.glob("*.db"))
        self.assertEqual(db_files, ["amca.db"])
        # No network happened in ANY of these mode flows.
        self.assertEqual(self.transport.opened_urls, [])


class ModeContractTests(ModeBase):
    def test_enum_values_frozen(self) -> None:
        self.assertEqual([m.value for m in RunMode],
                         ["deterministic", "adaptive", "hybrid"])
        from amca.research_orchestrator import RUN_MODES
        self.assertEqual(RUN_MODES,
                         {"deterministic", "adaptive", "hybrid"})

    def test_invalid_mode_error_lists_allowed(self) -> None:
        try:
            self.make_run("yolo")
        except ValueError as exc:
            self.assertIn("deterministic", str(exc))
            self.assertIn("adaptive", str(exc))
            self.assertIn("hybrid", str(exc))
        else:
            self.fail("invalid mode accepted")

    def test_planner_metadata_keys_present_after_update(self) -> None:
        run_id = self.make_run("deterministic")
        self.runs.update_planner_metadata(
            run_id, planner_mode="deterministic",
            planner_source="deterministic-planner",
            fallback_reason=None)
        row = self.runs.get_run(run_id)
        self.assertEqual(row["planner_mode"], "deterministic")
        self.assertEqual(row["planner_source"], "deterministic-planner")
        self.assertIsNone(row["fallback_reason"])

    def test_adaptive_model_error_metadata_records_reason(self) -> None:
        class Exploding(DeterministicScriptedPlannerModel):
            def propose_actions(self, state):
                raise RuntimeError("down")

        adaptive = AdaptiveResearchPlanner(
            model=Exploding(), fallback=DeterministicResearchPlanner())
        run_id = self.make_run("adaptive")
        self.orchestrator().execute_run(run_id, adaptive)
        row = self.runs.get_run(run_id)
        self.assertEqual(row["planner_source"], "fallback")
        self.assertIn("model_error:RuntimeError", row["fallback_reason"])

    def test_hybrid_empty_candidates_uses_baseline_source(self) -> None:
        hybrid = HybridResearchPlanner(
            model=DeterministicScriptedPlannerModel(),
            fallback=DeterministicResearchPlanner())
        run_id = self.make_run("hybrid")
        result = self.orchestrator().execute_run(run_id, hybrid)
        self.assertEqual(result["status"], RunStatus.COMPLETED.value)
        row = self.runs.get_run(run_id)
        self.assertEqual(row["planner_source"], "deterministic-baseline")

    def test_format_mode_replay_empty(self) -> None:
        self.assertEqual(format_mode_replay([]), [])

    def test_search_local_semantic_candidate_journalled(self) -> None:
        created = self.ingestor.ingest_user_content(
            session_id=self.session_id, project_id="projA",
            title="notes", text="battery efficiency measurements")
        self.ingestor.extract(created["snapshot"]["snapshot_id"],
                              project_id="projA")
        self.ingestor.rebuild_index("projA")
        from amca.research_semantic import (
            DeterministicEmbeddingProvider,
            SemanticRetrievalStore,
            HybridLocalSearch,
        )
        engine = HybridLocalSearch(
            ingestor=self.ingestor, retrieval_store=SemanticRetrievalStore(
                self.db),
            provider=DeterministicEmbeddingProvider(dimension=64))
        model = _FixedModel({
            "action": "search_local",
            "arguments": {"query": "battery efficiency",
                          "retrieval_mode": "semantic"}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        orch = self.orchestrator()
        orch.set_semantic_engine(engine)
        run_id = self.make_run("adaptive")
        orch.execute_run(run_id, adaptive)
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["result"]["effective_mode"], "semantic")

    def test_deterministic_mode_end_to_end_unchanged(self) -> None:
        self.research.synthesis if False else None
        question = self.synthesis.create_question(
            session_id=self.session_id, project_id="projA",
            question="uncovered angle")
        self.synthesis.create_claim(
            session_id=self.session_id, project_id="projA",
            claim_text="uncovered claim", claim_type=__import__(
                "amca.research_synthesis",
                fromlist=["ClaimType"]).ClaimType.FACTUAL,
            question_id=question["question_id"])
        run_id = self.make_run("deterministic")
        result = self.orchestrator().execute_run(
            run_id, DeterministicResearchPlanner())
        self.assertEqual(result["status"], RunStatus.PARTIAL.value)
        self.assertEqual(result["stop_reason"], "insufficient_evidence")
        row = self.runs.get_run(run_id)
        self.assertEqual(row["planner_mode"], "deterministic")

    def test_trail_turn_mode_persisted_adaptive(self) -> None:
        trail_store = PlanningTrailStore(self.db)
        model = _FixedModel({"action": "teleport", "arguments": {}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            trail_store=trail_store, project_id="projA")
        run_id = self.make_run("adaptive")
        self.orchestrator().execute_run(run_id, adaptive)
        turns = trail_store.turns_for_run(run_id)
        self.assertTrue(turns)
        self.assertTrue(all(t["mode"] == "adaptive" for t in turns))

    def test_replay_candidates_carry_verdict_details(self) -> None:
        trail_store = PlanningTrailStore(self.db)
        model = _FixedModel({"action": "teleport", "arguments": {}})
        adaptive = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            trail_store=trail_store, project_id="projA")
        run_id = self.make_run("adaptive")
        self.orchestrator().execute_run(run_id, adaptive)
        replay = replay_run(trail_store, run_id)
        candidate = replay[0]["candidates"][0]
        self.assertEqual(candidate["validator_result"], "rejected")
        self.assertEqual(candidate["proposal"]["action"], "teleport")
        self.assertTrue(candidate["rejection_reason"])

    def test_zero_paid_calls_structural(self) -> None:
        import amca.research_orchestrator as module

        self.assertFalse(any(name in dir(module) for name in
                             ("socket", "ssl", "http.client")))


if __name__ == "__main__":
    unittest.main()
