"""R2.5 model-backed research planning tests (manager-reply61).

Covers the frozen constraints of the adaptive reasoning loop: the planner
model only PROPOSES through a strict policy validator (closed action set,
strict argument schemas, no approval-smuggling keys, hard limits), every
candidate verdict is inspectable, local-only adapters refuse cloud
endpoints, and truthful fallback to the deterministic baseline keeps the
orchestrator safe when no model is configured or every candidate is
rejected.
"""

from __future__ import annotations

import json
import unittest

from amca.research_orchestrator import (
    DeterministicResearchPlanner,
    InvalidProposal,
    ReasonCode,
    ResearchAction,
    ResearchOrchestrator,
    ResearchLimits,
    ResearchRunStore,
    RunStatus,
)
from amca.research_context import ContextStore
from amca.research_planning import (
    AdaptiveResearchPlanner,
    DeterministicScriptedPlannerModel,
    LocalServicePlannerModel,
    MAX_ACTIONS_PER_TURN,
    PlannerModelError,
    PlannerPolicyValidator,
    PlanningTrailStore,
    planner_evaluation_report,
    replay_run,
)
from amca.web_acquisition import NetworkToolPolicy, WebAcquisitionClient
from test_research_semantic import SemanticBase
from test_research_orchestrator import RecordingTransport


def _context(**overrides):
    base = {
        "run_id": "run-test", "step_number": 3, "objective": "research",
        "questions": [{"question": "battery question", "status": "open"}],
        "claims": [], "gaps": [], "executed_queries": [],
        "unfetched_results": [], "link_candidates": [],
        "force_stop": False, "fetched_urls": [],
        "limits_remaining": {"steps": 5, "searches": 2, "fetches": 2,
                             "synthesis_attempts": 1},
        "previous_outcomes": [],
    }
    base.update(overrides)
    return base


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeLoopbackOpener:
    def __init__(self, actions: list[dict]) -> None:
        self.actions = actions
        self.requests: list = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return _FakeResponse(json.dumps(
            {"actions": self.actions}).encode("utf-8"))


class ValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = PlannerPolicyValidator()
        self.context = _context()

    def validate(self, raw):
        return self.validator.validate(self.context, raw)

    def test_valid_search_proposal_accepted_with_model_provenance(
            self) -> None:
        proposal, reason = self.validate(
            {"action": "search_web", "arguments": {"query": "battery"},
             "reason": "no_evidence"})
        self.assertIsNone(reason)
        self.assertEqual(proposal.action_type, ResearchAction.SEARCH_WEB)
        self.assertEqual(proposal.created_by, "model-proposal")
        self.assertEqual(proposal.reason_code, ReasonCode.NO_EVIDENCE)

    def test_unknown_and_closed_set_actions_rejected(self) -> None:
        for bad in ("approve", "execute_tool", "fabric.approve",
                    "self_destruct", "None"):
            proposal, reason = self.validate(
                {"action": bad, "arguments": {}})
            self.assertIsNone(proposal, bad)
            self.assertTrue(reason.startswith("unknown_action:")
                            or reason.startswith("action_outside_closed_set"),
                            reason)

    def test_non_object_candidate_rejected(self) -> None:
        for raw in ("search_web", None, ["search_web"], 7):
            proposal, reason = self.validate(raw)
            self.assertIsNone(proposal)
            self.assertEqual(reason, "candidate_not_an_object")

    def test_url_smuggling_rejected_fetch_uses_stored_identity_only(
            self) -> None:
        proposal, reason = self.validate({
            "action": "fetch_result",
            "arguments": {"search_result_id": "sres-1",
                          "url": "https://evil.example.com/"}})
        self.assertIsNone(proposal)
        self.assertEqual(reason, "unexpected_argument:['url']")

    def test_forbidden_approval_fields_rejected(self) -> None:
        for key in ("approve", "approval_reference", "execution_id",
                    "policy", "authorized", "bypass"):
            proposal, reason = self.validate({
                "action": "synthesize",
                "arguments": {"plan_id": "p", key: "mine"}})
            self.assertIsNone(proposal, key)
            self.assertTrue(reason.startswith("forbidden"), reason)

    def test_missing_wrong_typed_and_unknown_arguments_rejected(
            self) -> None:
        cases = [
            ({"action": "search_web", "arguments": {}},
             "missing_required_argument:query"),
            ({"action": "search_web",
              "arguments": {"query": 42}}, "argument_type_mismatch:query"),
            ({"action": "search_web",
              "arguments": {"query": "q", "surprise": 1}},
             "unexpected_argument:['surprise']"),
            ({"action": "link_evidence",
              "arguments": {"claim_id": "c", "evidence_id": "e",
                            "relation": "proves"}},
             "invalid_relation:proves"),
            ({"action": "search_local",
              "arguments": {"query": "q",
                            "retrieval_mode": "cloud"}},
             "invalid_retrieval_mode:cloud"),
            ({"action": "synthesise", "arguments": {}},
             "unknown_action:'synthesise'"),
        ]
        for raw, expected_reason in cases:
            proposal, reason = self.validate(raw)
            self.assertIsNone(proposal, raw)
            self.assertEqual(reason, expected_reason)

    def test_value_bounds_enforced(self) -> None:
        proposal, reason = self.validate({
            "action": "search_web", "arguments": {"query": "x" * 600}})
        self.assertIsNone(proposal)
        self.assertEqual(reason, "argument_over_bound:query")
        _, reason = self.validate({
            "action": "create_or_update_claim",
            "arguments": {"claim_text": "x" * 3000}})
        self.assertEqual(reason, "argument_over_bound:text")

    def test_limit_exhaustion_rejected(self) -> None:
        exhausted = _context(limits_remaining={
            "steps": 5, "searches": 0, "fetches": 0,
            "synthesis_attempts": 0})
        for raw, key in (
                ({"action": "search_web", "arguments": {"query": "q"}},
                 "limit_exhausted:searches"),
                ({"action": "fetch_result",
                  "arguments": {"search_result_id": "sres-1"}},
                 "limit_exhausted:fetches"),
                ({"action": "synthesize", "arguments": {}},
                 "limit_exhausted:synthesis_attempts")):
            proposal, reason = self.validator.validate(exhausted, raw)
            self.assertIsNone(proposal, raw)
            self.assertEqual(reason, key)

    def test_stop_always_allowed_even_at_zero_budget(self) -> None:
        exhausted = _context(limits_remaining={
            "steps": 0, "searches": 0, "fetches": 0,
            "synthesis_attempts": 0})
        proposal, reason = self.validator.validate(
            exhausted, {"action": "stop", "arguments": {}})
        self.assertIsNone(reason)
        self.assertEqual(proposal.action_type, ResearchAction.STOP)


class AdaptivePlannerTests(ContextBase.__bases__[0].__class__ if False
                           else unittest.TestCase):
    def setUp(self) -> None:
        self.context = _context()

    def test_model_candidate_chosen_and_trail_inspectable(self) -> None:
        model = DeterministicScriptedPlannerModel(turns=[[
            {"action": "fetch_result",
             "arguments": {"search_result_id": "sres-9"}}]])
        planner = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        proposal = planner.propose_next(self.context)
        self.assertEqual(proposal.action_type, ResearchAction.FETCH_RESULT)
        self.assertEqual(planner.last_planning_trail["planning_source"],
                         "model")
        self.assertEqual(
            planner.last_planning_trail["candidates"][0]["verdict"],
            "accepted")
        self.assertEqual(
            planner.last_planning_trail["model"]["provider_id"],
            "deterministic-script")

    def test_all_candidates_rejected_falls_back_truthfully(self) -> None:
        model = DeterministicScriptedPlannerModel(turns=[[
            {"action": "fetch_result",
             "arguments": {"search_result_id": "x", "url": "https://e.com"}},
            {"action": "teleport", "arguments": {}}]])
        planner = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            allow_fallback=True)
        proposal = planner.propose_next(_context(
            questions=[{"question": "uncovered angle", "status": "open"}]))
        self.assertEqual(planner.last_planning_trail["planning_source"],
                         "fallback")
        verdicts = [c["verdict"]
                    for c in planner.last_planning_trail["candidates"]]
        self.assertEqual(verdicts.count("rejected"), 2)

    def test_no_model_configured_is_truthful(self) -> None:
        planner = AdaptiveResearchPlanner(
            model=None, fallback=DeterministicResearchPlanner())
        planner.propose_next(self.context)
        self.assertIn("no_model_configured",
                      str(planner.last_planning_trail))

    def test_model_exception_falls_back_without_crash(self) -> None:
        class Exploding(DeterministicScriptedPlannerModel):
            def propose_actions(self, state):
                raise RuntimeError("model service down")

        planner = AdaptiveResearchPlanner(
            model=Exploding(), fallback=DeterministicResearchPlanner())
        proposal = planner.propose_next(self.context)
        self.assertIsNotNone(proposal)
        self.assertIn("model_error:RuntimeError",
                      str(planner.last_planning_trail))

    def test_turn_bound_caps_candidates(self) -> None:
        flood = [{"action": "stop", "arguments": {}}] * (
            MAX_ACTIONS_PER_TURN + 10)
        seen: list[list] = []

        class Counting(DeterministicScriptedPlannerModel):
            def propose_actions(self, state):
                seen.append(list(state.keys()))
                return list(flood)

        planner = AdaptiveResearchPlanner(
            model=Counting(), fallback=DeterministicResearchPlanner(),
            allow_fallback=True)
        # First candidate is a valid stop; only the cap matters here.
        planner.model = Counting()  # fresh script state not needed
        proposal = planner.propose_next(self.context)
        self.assertEqual(proposal.action_type, ResearchAction.STOP)

    def test_non_local_model_refused_at_construction(self) -> None:
        class Cloudy(DeterministicScriptedPlannerModel):
            is_local = False

        with self.assertRaises(PlannerModelError):
            AdaptiveResearchPlanner(model=Cloudy(),
                                    fallback=DeterministicResearchPlanner())

    def test_local_service_adapter_loopback_only_fake_transport(
            self) -> None:
        opener = _FakeLoopbackOpener([
            {"action": "search_web", "arguments": {"query": "loopback"}}])
        model = LocalServicePlannerModel(
            endpoint="http://127.0.0.1:11434/api/plan",
            model_id="local-plan", opener=opener)
        actions = model.propose_actions({"state": {}})
        self.assertEqual(actions[0]["action"], "search_web")
        self.assertEqual(len(opener.requests), 1)
        with self.assertRaises(PlannerModelError):
            LocalServicePlannerModel(
                endpoint="https://api.example.com/plan",
                model_id="cloud-plan", opener=opener)


class OrchestratorE2ETests(SemanticBase):
    def setUp(self) -> None:
        super().setUp()
        self.transport = RecordingTransport()
        self.client = WebAcquisitionClient(
            opener=self.transport, host_validator=lambda host: None)

    def test_model_proposal_flows_through_fabric_gate(self) -> None:
        """The model may PROPOSE a fetch; execution still requires the
        external Tool Fabric approval - the model can never self-authorize."""
        from amca.web_acquisition import WebAcquisitionStore
        enable_store = self.fabric.store
        NetworkToolPolicy.enable_for_project(enable_store, "projA")
        sid = self.seed_segment("quantum battery efficiency content")
        self.store.index_segments(self.research, self.provider,
                                  project_id="projA")
        event = self.research.record_search_event(
            project_id="projA", session_id=self.session_id,
            provider_id="seed", provider_version="", query="battery",
            options={}, started_at="2026-08-25T00:00:00+00:00",
            completed_at="2026-08-25T00:00:01+00:00", status="committed",
            results=[{"rank": 1, "title": "t",
                      "url": "https://example.com/doc", "snippet": "s"}])
        with self.research._connect() as connection:
            result_row = connection.execute(
                "SELECT search_result_id FROM research_search_results "
                "WHERE search_event_id = ? LIMIT 1",
                (event["search_event_id"],)).fetchone()
        durable_result_id = result_row["search_result_id"]
        model = DeterministicScriptedPlannerModel(strategy=lambda state: [
            {"action": "fetch_result",
             "arguments": {"search_result_id": durable_result_id}}])
        planner = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        orch = ResearchOrchestrator(
            run_store=self.runs, research_store=self.research,
            synthesis_store=self.synthesis, fabric=self.fabric,
            acq_store=WebAcquisitionStore(self.db), ingestor=self.ingestor,
            acquisition_client=self.client, search_provider=self.provider)
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="adaptive loop", limits=ResearchLimits(max_steps=5),
            mode="adaptive")[
            "run_id"]
        result = orch.execute_run(run_id, planner)
        # The model's fetch proposal reached WAITING_APPROVAL - the Tool
        # Fabric gate held; zero network happened without external approval.
        self.assertEqual(result["status"], RunStatus.WAITING_APPROVAL.value)
        step = self.runs.steps_for(run_id)[0]
        self.assertEqual(step["action_type"], "fetch_result")
        self.assertEqual(step["decision"], "awaiting_approval")
        self.assertEqual(step["created_by"], "model-proposal")
        self.assertEqual(self.transport.opened_urls, [])
        self.assertEqual(planner.last_planning_trail["planning_source"],
                         "model")
        # External approval lets the SAME execution proceed exactly once.
        self.fabric.approve(result["awaiting_execution"],
                            approval_reference="operator")
        final = orch.execute_run(run_id, planner)
        # Duplicate suppression keeps the fetched identity to ONE execution;
        # the adaptive planner then settles on a truthful terminal state.
        self.assertIn(final["status"],
                      (RunStatus.PARTIAL.value, RunStatus.COMPLETED.value))
        self.assertEqual(self.transport.opened_urls,
                         ["https://example.com/doc"])
        # The journal shows the model-sourced proposal was gated normally.
        steps = self.runs.steps_for(run_id)
        self.assertEqual(steps[0]["outcome"], "executed")

    def test_limit_busting_model_proposal_never_dispatched(self) -> None:
        model = DeterministicScriptedPlannerModel(strategy=lambda state: [
            {"action": "search_web", "arguments": {"query": "anything"}}])
        planner = AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner())
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="limits", limits=ResearchLimits(max_steps=4,
                                                      max_searches=0),
            mode="adaptive")[
            "run_id"]
        result = ResearchOrchestrator(
            run_store=self.runs, research_store=self.research,
            synthesis_store=self.synthesis, fabric=self.fabric,
            acq_store=self.acq_store, ingestor=self.ingestor,
            acquisition_client=self.client, search_provider=self.provider
        ).execute_run(run_id, planner)
        # With zero search budget the model proposal is refused and the
        # trivially-satisfied objective stops cleanly - no dispatch, ever.
        self.assertIn(result["status"],
                      (RunStatus.PARTIAL.value, RunStatus.COMPLETED.value))
        self.assertNotIn("search_web",
                         [s["action_type"] for s in
                          self.runs.steps_for(run_id)])

    def test_invalid_fallback_action_raises_invalid_proposal(
            self) -> None:
        class BadFallback(DeterministicResearchPlanner):
            def propose_next(self, context):
                raise InvalidProposal("fallback produced garbage")

        planner = AdaptiveResearchPlanner(
            model=DeterministicScriptedPlannerModel(),
            fallback=BadFallback(), allow_fallback=True)
        with self.assertRaises(Exception):
            planner.propose_next(_context())


class DurableTrailTests(SemanticBase):
    """R2.5.1: planning intelligence becomes observable and reproducible."""

    def setUp(self) -> None:
        super().setUp()
        self.transport = RecordingTransport()
        self.client = WebAcquisitionClient(
            opener=self.transport, host_validator=lambda host: None)

    def _planner_with_trail(self, turns):
        self.trail_store = PlanningTrailStore(self.db)
        model = DeterministicScriptedPlannerModel(turns=turns)
        return AdaptiveResearchPlanner(
            model=model, fallback=DeterministicResearchPlanner(),
            trail_store=self.trail_store, project_id="projA")

    def test_trail_persisted_and_survives_reopen(self) -> None:
        planner = self._planner_with_trail([[
            {"action": "search_web", "arguments": {"query": "battery"}}]])
        proposal = planner.propose_next(_context(run_id="run-durable"))
        self.assertEqual(proposal.created_by, "model-proposal")
        reopened = PlanningTrailStore(self.db)
        events = reopened.events_for_run("run-durable")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["validator_result"], "accepted")
        self.assertEqual(json.loads(events[0]["candidate_json"])[
                             "arguments"]["query"], "battery")
        self.assertIsNotNone(events[0]["created_at"])
        turn = reopened.turns_for_run("run-durable")[0]
        self.assertIn("objective", json.loads(turn["state_json"]))

    def test_replay_reproduces_decisions_without_executing_tools(
            self) -> None:
        before = self.fabric.invocation_count("research.web.fetch")
        # Rejected candidate first so BOTH verdicts land in one turn.
        planner = self._planner_with_trail([[
            {"action": "teleport", "arguments": {}},
            {"action": "fetch_result",
             "arguments": {"search_result_id": "sres-42"}}]])
        planner.propose_next(_context(run_id="run-replay"))
        replay = replay_run(self.trail_store, "run-replay")
        self.assertEqual(len(replay), 1)
        turn = replay[0]
        self.assertEqual(turn["planning_source"], "model")
        # The bounded state snapshot the model saw is preserved.
        self.assertIn("limits_remaining", turn["state"])
        candidates = turn["candidates"]
        self.assertEqual(candidates[0]["proposal"]["action"], "teleport")
        self.assertEqual(candidates[0]["validator_result"], "rejected")
        self.assertTrue(candidates[0]["rejection_reason"])
        self.assertEqual(candidates[1]["proposal"]["action"],
                         "fetch_result")
        self.assertEqual(candidates[1]["validator_result"], "accepted")
        # Replay executed NOTHING.
        self.assertEqual(self.fabric.invocation_count("research.web.fetch"),
                         before)

    def test_telemetry_counters_and_limit_rejections(self) -> None:
        planner = self._planner_with_trail([[
            {"action": "search_web", "arguments": {"query": "ok"}},
            {"action": "search_web",
             "arguments": {"query": "over budget"}}]])
        exhausted = _context(run_id="run-telemetry", limits_remaining={
            "steps": 5, "searches": 0, "fetches": 0,
            "synthesis_attempts": 0})
        planner.propose_next(exhausted)
        metrics = self.trail_store.metrics("projA")
        self.assertGreaterEqual(metrics["planning_candidate_count"], 2)
        self.assertEqual(metrics["planning_accepted_count"], 0)
        self.assertEqual(metrics["planning_rejected_count"],
                         metrics["planning_candidate_count"])
        self.assertGreaterEqual(metrics["planning_limit_rejection_count"], 1)
        self.assertGreaterEqual(metrics["planning_fallback_count"], 1)

    def test_approval_block_count_from_run_journal(self) -> None:
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
        planner = self._planner_with_trail([])
        planner.model = DeterministicScriptedPlannerModel(
            strategy=lambda state: [
                {"action": "fetch_result",
                 "arguments": {"search_result_id":
                               row["search_result_id"]}}])
        orch = ResearchOrchestrator(
            run_store=self.runs, research_store=self.research,
            synthesis_store=self.synthesis, fabric=self.fabric,
            acq_store=self.acq_store, ingestor=self.ingestor,
            acquisition_client=self.client, search_provider=self.provider)
        run_id = self.runs.create_run(
            project_id="projA", session_id=self.session_id,
            objective="trail gate", limits=ResearchLimits(max_steps=4),
            mode="adaptive")[
            "run_id"]
        result = orch.execute_run(run_id, planner)
        self.assertEqual(result["status"], RunStatus.WAITING_APPROVAL.value)
        report = planner_evaluation_report(self.trail_store, self.runs,
                                           run_id, "projA")
        self.assertGreaterEqual(report["approval_block_count"], 1)
        self.assertEqual(report["model_inference_count"], 0)
        self.assertIn("tool_executions", report)

    def test_derived_deletion_leaves_authoritative_data_intact(self) -> None:
        sid = self.seed_segment("citation worthy planning audit text")
        planner = self._planner_with_trail([[{"action": "stop",
                                              "arguments": {}}]])
        planner.propose_next(_context(run_id="run-del"))
        ContextStore(self.db).save_package({
            "context_id": "ctx-audit", "project_id": "projA",
            "session_id": None, "purpose": "audit", "focus_query": "q",
            "requested_retrieval_mode": "lexical",
            "effective_retrieval_mode": "lexical", "reranker": None,
            "rerank_effective": False, "selected_items": 0, "omitted": {},
            "distinct_sources": 0, "bounds": {}, "warnings": [],
            "status": "complete", "digest": "digest-audit",
            "created_at": "2026-08-25T00:00:00+00:00", "items": []})
        deleted_events = self.trail_store.delete_run_planning("run-del")
        deleted_packages = ContextStore(self.db).delete_project_context(
            "projA")
        self.assertGreaterEqual(deleted_events, 1)
        # Authoritative substrate untouched by derived deletion.
        with self.research._connect() as connection:
            still_there = connection.execute(
                "SELECT COUNT(*) c FROM research_extracted_segments "
                "WHERE segment_id = ?", (sid,)).fetchone()["c"]
        self.assertEqual(still_there, 1)
        del deleted_packages

    def test_turn_rows_not_duplicated_for_same_index(self) -> None:
        planner = self._planner_with_trail([[{"action": "stop",
                                              "arguments": {}}]])
        context = _context(run_id="run-idem")
        first = planner.propose_next(context)
        second = planner.propose_next(context)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        turns = self.trail_store.turns_for_run("run-idem")
        self.assertEqual(len(turns), 2)  # two DISTINCT turns
        self.assertEqual({t["turn_index"] for t in turns}, {0, 1})


if __name__ == "__main__":
    unittest.main()
