"""AutoDecision -> ExecutionConfiguration, and the separated participant counts.

The manager's corrections:
  * section 5 — one explicit translation boundary that TRANSPORTS canonical
    decisions and does nothing else;
  * section 4 — team_size means ACTIVE PARTICIPANTS, and the five counts are
    distinct facts rather than one overloaded number;
  * section 3 — the decision must be the one the runtime consumed, not a
    parallel computation.
"""

from __future__ import annotations

import inspect
import unittest

from amca.auto_intelligence import (
    AutoDecision,
    ParticipantCounts,
    analyse_task,
    counts_from,
    to_execution_configuration,
)


def _participants():
    return (
        {"model_ref": "coder", "status": "active_bound", "agent_id": "specialist.coding"},
        {"model_ref": "writer", "status": "active_general", "agent_id": ""},
        {"model_ref": "spare", "status": "inactive"},
    )


class ParticipantCountTests(unittest.TestCase):
    """Section 4 — five facts, not one overloaded number."""

    def test_the_five_counts_are_distinct(self) -> None:
        counts = counts_from(_participants(), ("a", "b", "c", "d", "e"))
        self.assertEqual(counts.configured, 3)
        self.assertEqual(counts.active, 2)
        self.assertEqual(counts.specialist_roles, 5)
        self.assertEqual(counts.general_participants, 1)
        self.assertEqual(counts.inactive, 1)

    def test_team_size_is_active_participants(self) -> None:
        # Not the configured count and not the role count.
        counts = counts_from(_participants(), ("a", "b", "c", "d", "e"))
        self.assertEqual(counts.team_size, 2)

    def test_the_managers_example_is_representable(self) -> None:
        # "4 configured, 3 active models, 5 specialist roles, 1 inactive is valid."
        counts = ParticipantCounts(configured=4, active=3, specialist_roles=5,
                                   general_participants=0, inactive=1)
        self.assertEqual(counts.team_size, 3)
        payload = counts.to_dict()
        self.assertEqual(payload["configured"], 4)
        self.assertEqual(payload["specialist_roles"], 5)

    def test_specialist_roles_counts_roles_not_models(self) -> None:
        # One model may serve several roles; conflating them produced the
        # earlier inconsistency where team=3 and four models were both reported.
        counts = counts_from(
            ({"model_ref": "one", "status": "active_bound", "agent_id": "r"},),
            ("r1", "r2", "r3"),
        )
        self.assertEqual(counts.active, 1)
        self.assertEqual(counts.specialist_roles, 3)

    def test_an_empty_run_counts_zero(self) -> None:
        counts = counts_from((), ())
        self.assertEqual(counts.team_size, 0)
        self.assertEqual(counts.to_dict()["inactive"], 0)

    def test_a_model_with_no_agent_id_is_a_general_participant(self) -> None:
        counts = counts_from(
            ({"model_ref": "m", "status": "active_general", "agent_id": ""},), (),
        )
        self.assertEqual(counts.general_participants, 1)


class ExecutionConfigurationTests(unittest.TestCase):
    """Section 5 — transport, not decision."""

    def _decision(self, **overrides):
        fields = dict(
            run_id="r1", goal="Write a function.", analysis=analyse_task("Write a function."),
            agent_roles=("specialist.coding",),
            active_participants=_participants(),
            inactive_models=("spare",),
            collaboration_mode="joint", synchronization="soft",
            verification_strategy="Goal Contract criteria",
        )
        fields.update(overrides)
        return AutoDecision(**fields)

    def test_the_configuration_carries_what_the_runtime_needs(self) -> None:
        payload = to_execution_configuration(self._decision()).to_dict()
        for key in ("run_id", "goal", "active_participants", "inactive_participants",
                    "agent_bindings", "selection_decisions", "collaboration_mode",
                    "synchronization", "verification_strategy", "permission_context",
                    "counts"):
            with self.subTest(key=key):
                self.assertIn(key, payload)

    def test_inactive_participants_are_carried_separately(self) -> None:
        config = to_execution_configuration(self._decision())
        self.assertEqual(config.inactive_participants, ("spare",))
        self.assertNotIn("spare", config.active_participants)

    def test_the_configuration_does_not_rerank_or_rebind(self) -> None:
        # A pure transport: every value comes from the decision.  A branch in
        # here that could choose differently would make the decision
        # non-authoritative.
        source = inspect.getsource(to_execution_configuration)
        for forbidden in ("select_model", "bind_agents", "sort", "sorted",
                          "min(", "max(", "rank"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_configuration_does_not_change_permissions(self) -> None:
        source = inspect.getsource(to_execution_configuration)
        for forbidden in ("profile", "grant", "approval", "capabilit"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source.casefold())

    def test_the_counts_travel_with_the_configuration(self) -> None:
        config = to_execution_configuration(self._decision())
        self.assertEqual(config.counts.active, 2)
        self.assertEqual(config.counts.inactive, 1)
        self.assertEqual(config.counts.team_size, 2)

    def test_it_serialises(self) -> None:
        import json

        json.dumps(to_execution_configuration(self._decision()).to_dict())

    def test_a_decision_with_no_mode_defaults_safely(self) -> None:
        config = to_execution_configuration(self._decision(collaboration_mode=""))
        self.assertEqual(config.collaboration_mode, "solo")


class ProductionConsumesTheDecisionTests(unittest.TestCase):
    """Section 3 — the API must report the decision the runtime consumed."""

    def test_the_service_builds_the_decision_from_the_runs_real_choices(self) -> None:
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("to_execution_configuration", source)
        self.assertIn("execution_configuration", source)
        # It must be built from the participants the run actually has, not from
        # a separate analysis.
        self.assertIn('"participant_status"', source)

    def test_the_service_records_the_counts_the_runtime_used(self) -> None:
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("participant_counts", source)

    def test_the_record_is_not_computed_in_a_parallel_path(self) -> None:
        # The decision is constructed AFTER the participants are resolved, so
        # it describes what executed.  If it were built earlier it could
        # disagree with the run.
        from amca import service

        source = inspect.getsource(service)
        self.assertLess(
            source.index("agent_plan[\"model_assignments\"] = model_assignments"),
            source.index("to_execution_configuration("),
            "the decision must be derived from the resolved participants",
        )


class RuntimeConsumesTheConfigurationTests(unittest.TestCase):
    """Section 3, the final step: the runtime's INPUT is the configuration.

    Before this, the configuration DESCRIBED the run while the run derived its
    participants from a separate list.  A record adjacent to what executed is
    not a record of what executed.
    """

    def test_the_runtime_participants_come_from_the_configuration(self) -> None:
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("execution_configuration.active_participants", source)
        self.assertIn("execution_configuration.agent_bindings", source)

    def test_the_configuration_carries_the_fields_the_runtime_reads(self) -> None:
        # Consuming it must be a substitution, not a translation that can lose
        # a field the run depends on.
        from amca import service

        source = inspect.getsource(service)
        self.assertIn("execution_configuration.agent_bindings = tuple(model_assignments)",
                      source)

    def test_the_binding_entries_carry_what_the_consumer_indexes(self) -> None:
        # The runtime indexes agent_id and responsibility on each entry, so the
        # configuration must carry entries that have them.
        from amca import service

        source = inspect.getsource(service)
        for field in ('"participant_id"', '"agent_id"', '"responsibility"'):
            with self.subTest(field=field):
                self.assertIn(field, source)


if __name__ == "__main__":
    unittest.main()
