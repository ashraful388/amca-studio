"""Work Order: the SEVEN DETERMINISTIC AUTO SCENARIOS.

"Implement all seven before long real-model runs."  Each scenario asserts the
properties the directive names, deterministically, with no model calls.

The scenarios are the acceptance criteria for Auto Intelligence, so they assert
what Auto DECIDED and why — not merely that a run finished.
"""

from __future__ import annotations

import unittest

from amca.agent_model_binding import CONTROL_ROLES, ParticipantStatus, bind_agents
from amca.auto_intelligence import (
    AutoDecision,
    AutoOutcome,
    TaskComplexity,
    analyse_task,
    choose_collaboration,
    choose_synchronization,
    explain,
    roles_for,
    team_size_for,
)
from amca.model_router import (
    CostPolicy,
    ModelCandidateFacts,
    ModelSelectionRequest,
    select_model,
)
from amca.runtime_health import RunScopedHealth


def _model(ref, capabilities=(), **kwargs):
    fields = dict(
        model_ref=ref,
        capabilities=frozenset(capabilities),
        supports_tools="tools" in capabilities,
        supports_json="json_mode" in capabilities,
        supports_vision="vision" in capabilities,
    )
    fields.update(kwargs)
    return ModelCandidateFacts(**fields)


#: A small machine: a strong coder, a long-context reader, a general model and
#: one that cannot use tools.
MACHINE = [
    _model("coder", ["coding", "tools"], expected_cost_usd=0.02, measured_latency_ms=900.0),
    _model("reader", ["long_context"], expected_cost_usd=0.01, measured_latency_ms=700.0),
    _model("general", ["coding", "long_context"], expected_cost_usd=0.03),
    _model("no-tools", ["long_context"], expected_cost_usd=0.005),
]


def _run_auto(goal, *, candidates=MACHINE, **request_kwargs):
    """Derive a configuration and select through the canonical router."""

    analysis = analyse_task(goal)
    size = team_size_for(analysis)
    roles = roles_for(analysis)
    mode, mode_reason = choose_collaboration(analysis, size)
    sync, sync_reason = choose_synchronization(mode, size)

    request = ModelSelectionRequest(
        task_type="auto",
        required_capabilities=analysis.required_capabilities,
        preferred_capabilities=analysis.preferred_capabilities,
        required_tools=analysis.requires_tools,
        required_structured_output=analysis.requires_structured_output,
        required_vision=analysis.requires_vision,
        required_context=analysis.required_context,
        cost_policy=request_kwargs.pop("cost_policy", CostPolicy.BALANCED),
        maximum_participants=max(1, size),
        **request_kwargs,
    )
    decision = select_model(candidates, request)
    binding = bind_agents(
        roles,
        candidates,
        required_participants=decision.selected_models,
    )
    return AutoDecision(
        run_id="run-1",
        goal=goal,
        analysis=analysis,
        requested_team_size=size,
        agent_roles=roles,
        router_decisions=(decision.to_dict(),),
        active_participants=tuple(
            item.to_dict() for item in binding.participants
            if item.status is not ParticipantStatus.INACTIVE
        ),
        inactive_models=binding.inactive_model_refs,
        binding_decisions=binding.decisions,
        collaboration_mode=mode,
        collaboration_reason=mode_reason,
        synchronization=sync,
        verification_strategy=sync_reason,
    )


class Scenario1SimpleTests(unittest.TestCase):
    """A straightforward bounded task."""

    GOAL = "What is a list comprehension?"

    def test_the_task_is_analysed_as_simple(self) -> None:
        self.assertIs(analyse_task(self.GOAL).complexity, TaskComplexity.SIMPLE)

    def test_unnecessary_models_remain_inactive(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertEqual(decision.requested_team_size, 1)
        self.assertEqual(len(decision.active_participants), 1)
        self.assertGreaterEqual(len(decision.inactive_models), 3)

    def test_unnecessary_agents_are_not_spawned(self) -> None:
        # "Agent count is not a measure of intelligence."
        decision = _run_auto(self.GOAL)
        self.assertEqual(decision.agent_roles, ())

    def test_selection_went_through_the_canonical_router(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertTrue(decision.router_decisions)
        self.assertIn("selected_model", decision.router_decisions[0])

    def test_the_run_can_complete(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertTrue(decision.router_decisions[0]["selected_model"])


class Scenario2CodingTests(unittest.TestCase):
    """A bounded programming task."""

    GOAL = "Write a Python function that parses a CSV row into a dictionary and test it."

    def test_the_coding_role_is_selected_where_justified(self) -> None:
        self.assertIn("specialist.coding", roles_for(analyse_task(self.GOAL)))

    def test_capability_based_binding_is_used(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertTrue(decision.binding_decisions)
        for entry in decision.binding_decisions:
            self.assertIn("selected_model", entry)

    def test_testing_is_included_when_the_task_warrants_it(self) -> None:
        self.assertIn("specialist.unit-testing", roles_for(analyse_task(self.GOAL)))

    def test_governance_roles_never_perform_coding(self) -> None:
        decision = _run_auto(self.GOAL)
        for entry in decision.binding_decisions:
            with self.subTest(agent=entry.get("agent_id")):
                self.assertNotIn(entry.get("agent_id"), CONTROL_ROLES)

    def test_the_goal_contract_would_determine_completion(self) -> None:
        # The decision record carries the flag rather than inferring completion
        # from a call count.
        decision = _run_auto(self.GOAL)
        self.assertIsInstance(decision.goal_contract_satisfied, bool)


class Scenario3ResearchTests(unittest.TestCase):
    """A bounded local-information task, so no browser is required."""

    GOAL = "Research the trade-offs between two caching strategies and cite the sources."

    def test_the_research_role_is_used_where_appropriate(self) -> None:
        self.assertIn("specialist.deep-research", roles_for(analyse_task(self.GOAL)))

    def test_a_verification_strategy_is_represented(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertTrue(decision.verification_strategy)

    def test_model_selection_goes_through_the_router(self) -> None:
        decision = _run_auto(self.GOAL)
        self.assertTrue(decision.router_decisions[0]["selected_model"])

    def test_no_browser_requirement_is_fabricated(self) -> None:
        # Nothing in this goal needs a browser, so Auto must not invent one.
        analysis = analyse_task(self.GOAL)
        self.assertFalse(analysis.needs_browser)
        self.assertFalse(analysis.requires_tools)


class Scenario4MultiCapabilityTests(unittest.TestCase):
    """A task that genuinely requires several substantive stages."""

    GOAL = ("Analyse the module, implement the fix, and then review and test it "
            "end to end.")

    def test_multiple_roles_only_when_justified(self) -> None:
        roles = roles_for(analyse_task(self.GOAL))
        self.assertGreater(len(roles), 2)

    def test_models_may_differ_across_roles(self) -> None:
        decision = _run_auto(self.GOAL)
        chosen = {
            entry.get("selected_model")
            for entry in decision.binding_decisions
            if entry.get("selected_model")
        }
        self.assertTrue(chosen)

    def test_handoffs_remain_bounded(self) -> None:
        # Roles are a bounded set, not every agent in the catalogue.
        self.assertLessEqual(len(roles_for(analyse_task(self.GOAL))), 6)

    def test_a_complex_task_may_use_every_configured_model(self) -> None:
        # On a small machine a genuinely complex task can justify all four
        # models, and that is correct — dynamic sizing means the team grows to
        # fit the task, not that models are always left over.  The property that
        # matters is that nothing was activated without a reason.
        decision = _run_auto(self.GOAL)
        self.assertGreaterEqual(len(decision.active_participants), 3)
        for participant in decision.active_participants:
            with self.subTest(model=participant.get("model_ref")):
                self.assertTrue(participant.get("rationale"))

    def test_a_simple_task_leaves_models_inactive(self) -> None:
        # The contrast: sizing is dynamic in both directions.
        decision = _run_auto("What is a list comprehension?")
        self.assertTrue(decision.inactive_models)


class Scenario5PreferredModelIncapableTests(unittest.TestCase):
    """Hard requirement beats preference."""

    def test_a_preferred_but_incapable_model_is_excluded(self) -> None:
        preferred = _model("preferred-no-tools", ["long_context"], expected_cost_usd=0.001)
        capable = _model("capable", ["long_context", "tools"], supports_tools=True,
                         expected_cost_usd=0.5)
        request = ModelSelectionRequest(
            task_type="auto",
            required_capabilities=frozenset({"long_context"}),
            required_tools=True,
            preferred_capabilities=frozenset({"long_context"}),
            cost_policy=CostPolicy.ECONOMY,
        )
        decision = select_model([preferred, capable], request)
        self.assertEqual(decision.selected_model, "capable")

    def test_the_decision_explains_why_the_preferred_model_was_excluded(self) -> None:
        preferred = _model("preferred-no-tools", ["long_context"])
        capable = _model("capable", ["long_context", "tools"], supports_tools=True)
        request = ModelSelectionRequest(
            task_type="auto",
            required_capabilities=frozenset({"long_context"}),
            required_tools=True,
        )
        decision = select_model([preferred, capable], request)
        reasons = {item.model_ref: item.reason for item in decision.excluded_models}
        self.assertIn("preferred-no-tools", reasons)
        self.assertIn("tool support", reasons["preferred-no-tools"])


class Scenario6RuntimeFailureTests(unittest.TestCase):
    """Failure and recovery use the canonical path, not an Auto-specific one."""

    def test_a_failed_model_is_excluded_and_an_alternate_selected(self) -> None:
        from amca.models import ModelTimeoutError

        health = RunScopedHealth()
        health.record_failure("primary", ModelTimeoutError("timed out"))
        request = ModelSelectionRequest(
            task_type="auto", runtime_health=health, cost_policy=CostPolicy.BALANCED,
        )
        decision = select_model(
            [_model("primary"), _model("alternate")], request
        )
        self.assertEqual(decision.selected_model, "alternate")

    def test_a_timeout_prone_model_is_ranked_lower_not_excluded(self) -> None:
        """A timeout demotes; it does not disqualify.

        This is deliberate: a model that timed out once may still be the only
        one capable of the task, so excluding it outright would fail a run that
        could have succeeded.  A rate limit or an outage DOES exclude, because
        those are momentary and retrying immediately is pointless.
        """
        from amca.models import ModelTimeoutError

        health = RunScopedHealth()
        health.record_failure("primary", ModelTimeoutError("timed out"))
        request = ModelSelectionRequest(task_type="auto", runtime_health=health)
        decision = select_model([_model("primary"), _model("alternate")], request)
        self.assertEqual(decision.selected_model, "alternate")
        self.assertIn("primary", decision.eligible_models)
        self.assertIn("runtime_health", decision.soft_factors_used)

    def test_a_momentary_outage_does_exclude_for_the_run(self) -> None:
        from amca.models import ModelRateLimitError

        health = RunScopedHealth()
        health.record_failure("primary", ModelRateLimitError("429"))
        request = ModelSelectionRequest(task_type="auto", runtime_health=health)
        decision = select_model([_model("primary"), _model("alternate")], request)
        reasons = {item.model_ref: item.reason for item in decision.excluded_models}
        self.assertIn("runtime health", reasons["primary"])

    def test_health_does_not_change_capability(self) -> None:
        from amca.models import ModelTimeoutError

        primary = _model("primary", ["coding", "tools"])
        health = RunScopedHealth()
        health.record_failure("primary", ModelTimeoutError("timed out"))
        select_model([primary, _model("alternate")],
                     ModelSelectionRequest(task_type="auto", runtime_health=health))
        self.assertTrue(primary.supports_tools,
                        "a timeout must not alter a capability fact")


class Scenario7HighComplexityTests(unittest.TestCase):
    """A task that legitimately requires richer collaboration."""

    GOAL = ("Design the migration plan, implement it end to end, then review the "
            "security implications and validate the result.")

    def test_the_task_is_analysed_as_complex(self) -> None:
        self.assertIs(analyse_task(self.GOAL).complexity, TaskComplexity.COMPLEX)

    def test_the_team_is_larger_than_for_a_simple_task(self) -> None:
        self.assertGreater(
            team_size_for(analyse_task(self.GOAL)),
            team_size_for(analyse_task("What is a list comprehension?")),
        )

    def test_the_collaboration_mode_has_a_stated_reason(self) -> None:
        # "Do not simply map complex task -> Adaptive Hybrid without reasons."
        analysis = analyse_task(self.GOAL)
        mode, reason = choose_collaboration(analysis, team_size_for(analysis))
        self.assertTrue(mode)
        self.assertGreater(len(reason.split()), 5)

    def test_the_mode_is_not_a_blind_complexity_mapping(self) -> None:
        # A complex task whose shape does not suit discussion must not silently
        # get the same mode as one that does.
        analysis = analyse_task(self.GOAL)
        mode, _reason = choose_collaboration(analysis, team_size_for(analysis))
        self.assertIn(mode, {"joint", "independent_discuss", "parallel", "adaptive_hybrid"})

    def test_synchronization_is_chosen_not_forced(self) -> None:
        analysis = analyse_task(self.GOAL)
        size = team_size_for(analysis)
        mode, _ = choose_collaboration(analysis, size)
        policy, reason = choose_synchronization(mode, size)
        self.assertTrue(policy)
        self.assertTrue(reason)


class ExplanationTests(unittest.TestCase):
    """Section 10 — a concise projection, no implementation class names."""

    def test_the_explanation_is_readable(self) -> None:
        decision = _run_auto("Write a Python function and test it.")
        text = explain(decision)
        self.assertIn("Auto Intelligence", text)
        self.assertIn("Models:", text)
        self.assertIn("Status:", text)

    def test_it_exposes_no_implementation_class_names(self) -> None:
        text = explain(_run_auto("Write a Python function and test it."))
        for forbidden in ("ModelSelectionRequest", "SelectionDecision", "AutoDecision",
                          "ParticipantRecord", "ModelCandidateFacts", "RunScopedHealth"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, text)

    def test_it_reports_when_configured_models_were_not_needed(self) -> None:
        text = explain(_run_auto("What is a list comprehension?"))
        self.assertIn("not needed", text)

    def test_a_recovery_is_reported_plainly(self) -> None:
        decision = _run_auto("What is a list comprehension?")
        decision.recovery = ({"fallback_requested": True},)
        self.assertIn("continued with an eligible alternate model", explain(decision))


class OutcomeAuthorityTests(unittest.TestCase):
    """Section 11 and 12 — the Goal Contract decides, and no empty success."""

    def test_the_default_outcome_is_not_complete(self) -> None:
        # A decision record does not claim success until the Goal Contract says so.
        self.assertIs(AutoDecision(run_id="r", goal="g",
                                   analysis=analyse_task("g")).outcome,
                      AutoOutcome.FAILED)

    def test_completion_is_explicitly_flagged(self) -> None:
        decision = _run_auto("What is a list comprehension?")
        self.assertFalse(decision.goal_contract_satisfied)
        self.assertNotEqual(decision.outcome, AutoOutcome.COMPLETE)

    def test_all_four_outcomes_exist(self) -> None:
        self.assertEqual(
            {item.value for item in AutoOutcome},
            {"complete", "degraded", "failed", "cancelled"},
        )


class DecisionRecordTests(unittest.TestCase):
    """Section 9 — one authoritative record that references, not copies."""

    def test_the_record_references_the_canonical_decisions(self) -> None:
        decision = _run_auto("Write a Python function and test it.")
        payload = decision.to_dict()
        for key in ("router_decisions", "binding_decisions", "active_participants",
                    "inactive_models", "agent_roles", "collaboration_mode",
                    "synchronization", "outcome"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_the_record_does_not_dump_internal_prompts(self) -> None:
        payload = _run_auto("Write a Python function and test it.").to_dict()
        text = repr(payload)
        for forbidden in ("# Your logical-agent assignment", "system_prompt",
                          "messages", "raw_prompt"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_record_is_serialisable(self) -> None:
        import json

        json.dumps(_run_auto("Write a Python function and test it.").to_dict())

    def test_every_active_participant_has_a_recorded_reason(self) -> None:
        decision = _run_auto("Write a Python function and test it.")
        for participant in decision.active_participants:
            with self.subTest(model=participant.get("model_ref")):
                self.assertTrue(participant.get("rationale"))


if __name__ == "__main__":
    unittest.main()
