"""Deterministic tests for the C1.1 desktop collaboration contract."""

from __future__ import annotations

import unittest

from amca.collaboration import (
    ARCHITECTURE_SYNC_DEFAULTS,
    ArchitectureDecision,
    CollaborationDecision,
    CollaborationSignals,
    DesktopCollaborationRouter,
    DeterministicDecompositionPlanner,
    ModelSetDecision,
    SUPPORTED_ARCHITECTURES,
    TaskShape,
    parse_requested_mode,
)
from amca.domain import (
    CollaborationMode,
    ComplexityLevel,
    Disagreement,
    PrivacyMode,
    ProjectState,
    Severity,
    SyncMode,
)


def _shape(**overrides: object) -> TaskShape:
    base = {
        "complexity": ComplexityLevel.LOW,
        "coupled": True,
        "decomposable": False,
        "verification_heavy": False,
        "novelty_required": False,
    }
    base.update(overrides)
    return TaskShape(**base)  # type: ignore[arg-type]


class ParseRequestedModeTests(unittest.TestCase):
    def test_auto_sentinels_resolve_to_auto_ownership(self) -> None:
        for value in (None, "", "   ", "auto", "AUTO"):
            self.assertIsNone(parse_requested_mode(value))

    def test_all_five_architectures_parse(self) -> None:
        for mode in SUPPORTED_ARCHITECTURES:
            self.assertIs(parse_requested_mode(mode.value), mode)

    def test_domain_aliases_remain_backward_compatible(self) -> None:
        self.assertIs(parse_requested_mode("joint_deliberation"), CollaborationMode.JOINT)
        self.assertIs(
            parse_requested_mode("parallel_decomposition"), CollaborationMode.PARALLEL
        )

    def test_unknown_mode_lists_supported_values(self) -> None:
        with self.assertRaises(ValueError) as raised:
            parse_requested_mode("telepathy")
        message = str(raised.exception)
        for mode in SUPPORTED_ARCHITECTURES:
            self.assertIn(mode.value, message)


class TaskShapeTests(unittest.TestCase):
    def test_trivial_goal_is_routine_coupled_low_complexity(self) -> None:
        shape = TaskShape.from_goal("Reply only: AUTO_OK")
        self.assertIs(shape.complexity, ComplexityLevel.LOW)
        self.assertFalse(shape.verification_heavy)
        self.assertFalse(shape.decomposable)
        self.assertTrue(shape.coupled)

    def test_multi_domain_goal_is_decomposable(self) -> None:
        shape = TaskShape.from_goal(
            "Research the evidence, then update the repository code and run tests."
        )
        self.assertTrue(shape.decomposable)
        self.assertFalse(shape.verification_heavy)

    def test_verification_language_is_flagged(self) -> None:
        shape = TaskShape.from_goal("Please verify these citations are accurate.")
        self.assertTrue(shape.verification_heavy)

    def test_novelty_language_is_flagged(self) -> None:
        shape = TaskShape.from_goal("Invent a novel ranking algorithm.")
        self.assertTrue(shape.novelty_required)

    def test_shared_deliverable_keeps_decomposed_work_coupled(self) -> None:
        shape = TaskShape.from_goal(
            "Research sources, analyze the data, and combine everything into one report."
        )
        self.assertTrue(shape.decomposable)
        self.assertTrue(shape.coupled)


class CollaborationSignalsTests(unittest.TestCase):
    def test_critical_objection_comes_from_state_not_wording(self) -> None:
        plain = CollaborationSignals.from_state(
            ProjectState(
                project_id="p",
                goal="Explain what a critical objection means",
                privacy_mode=PrivacyMode.OFFLINE,
            )
        )
        self.assertFalse(plain.has_critical_objection)
        flagged = CollaborationSignals.from_state(
            ProjectState(
                project_id="p",
                goal="anything",
                privacy_mode=PrivacyMode.OFFLINE,
                active_disagreements=[
                    Disagreement(topic="scope", severity=Severity.CRITICAL)
                ],
            )
        )
        self.assertTrue(flagged.has_critical_objection)
        self.assertTrue(flagged.high_disagreement)

    def test_high_disagreement_without_criticality(self) -> None:
        signals = CollaborationSignals.from_state(
            ProjectState(
                project_id="p",
                goal="g",
                privacy_mode=PrivacyMode.OFFLINE,
                active_disagreements=[
                    Disagreement(topic="scope", severity=Severity.HIGH)
                ],
            )
        )
        self.assertFalse(signals.has_critical_objection)
        self.assertTrue(signals.high_disagreement)


class DesktopCollaborationRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = DesktopCollaborationRouter()

    def test_safety_override_beats_trivial_task_and_lexical_suggestions(self) -> None:
        decision = self.router.decide(
            TaskShape.from_goal("say hello"),
            CollaborationSignals(has_critical_objection=True),
            eligible_model_count=1,
        )
        self.assertIs(decision.mode, CollaborationMode.JOINT)
        self.assertIs(decision.sync_mode, SyncMode.HARD)
        self.assertEqual(decision.policy_rule, "safety.canonical_conflict_to_joint")

    def test_parallel_conflict_forces_joint(self) -> None:
        decision = self.router.decide(
            _shape(),
            CollaborationSignals(parallel_conflict=True),
            eligible_model_count=4,
        )
        self.assertIs(decision.mode, CollaborationMode.JOINT)

    def test_user_lock_is_honored_without_degradation(self) -> None:
        decision = self.router.decide(
            TaskShape.from_goal("say hello"),
            CollaborationSignals(),
            requested_mode=CollaborationMode.INDEPENDENT_DISCUSS,
            eligible_model_count=1,
        )
        self.assertIs(decision.mode, CollaborationMode.INDEPENDENT_DISCUSS)
        self.assertEqual(decision.requested_by, "user_lock")
        self.assertIsNone(decision.degradation_reason)

    def test_verification_heavy_task_routes_to_independent_discuss(self) -> None:
        decision = self.router.decide(
            TaskShape.from_goal("Verify the proof and cross-check every step."),
            CollaborationSignals(),
            eligible_model_count=3,
        )
        self.assertIs(decision.mode, CollaborationMode.INDEPENDENT_DISCUSS)
        self.assertIs(decision.sync_mode, SyncMode.QUORUM)
        self.assertEqual(
            decision.policy_rule, "task.verification_or_novelty_to_independent"
        )

    def test_novelty_required_routes_to_independent_discuss(self) -> None:
        decision = self.router.decide(
            _shape(novelty_required=True),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        self.assertIs(decision.mode, CollaborationMode.INDEPENDENT_DISCUSS)

    def test_decomposable_weakly_coupled_task_routes_to_parallel(self) -> None:
        decision = self.router.decide(
            _shape(decomposable=True, coupled=False),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        self.assertIs(decision.mode, CollaborationMode.PARALLEL)
        self.assertEqual(
            decision.policy_rule, "task.decomposable_weak_coupling_to_parallel"
        )

    def test_high_complexity_coupled_task_routes_to_joint(self) -> None:
        decision = self.router.decide(
            _shape(complexity=ComplexityLevel.HIGH),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        self.assertIs(decision.mode, CollaborationMode.JOINT)
        self.assertEqual(decision.expected_rounds, 2)

    def test_ambiguous_medium_task_routes_to_adaptive(self) -> None:
        decision = self.router.decide(
            _shape(
                complexity=ComplexityLevel.MEDIUM,
                decomposable=True,
                coupled=True,
            ),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        self.assertIs(decision.mode, CollaborationMode.ADAPTIVE_HYBRID)
        self.assertEqual(
            decision.policy_rule, "task.ambiguous_decomposition_to_adaptive"
        )

    def test_auto_never_promotes_routine_tasks_because_of_model_count(self) -> None:
        for eligible in (1, 2, 4):
            decision = self.router.decide(
                TaskShape.from_goal("say hello"),
                CollaborationSignals(),
                eligible_model_count=eligible,
            )
            self.assertIs(
                decision.mode, CollaborationMode.SOLO, f"eligible={eligible}"
            )
            self.assertEqual(
                decision.policy_rule, "task.routine_single_focus_to_solo"
            )
            self.assertIsNone(decision.degradation_reason)

    def test_multi_participant_architecture_degrades_with_one_eligible_model(
        self,
    ) -> None:
        decision = self.router.decide(
            _shape(verification_heavy=True),
            CollaborationSignals(),
            eligible_model_count=1,
        )
        self.assertIs(decision.mode, CollaborationMode.SOLO)
        self.assertIs(decision.candidate_mode, CollaborationMode.INDEPENDENT_DISCUSS)
        self.assertTrue(decision.degraded)
        self.assertIn("independent_discuss", str(decision.degradation_reason))

    def test_zero_eligible_models_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.router.decide(_shape(), CollaborationSignals(), eligible_model_count=0)

    def test_sync_override_is_respected(self) -> None:
        decision = self.router.decide(
            _shape(complexity=ComplexityLevel.HIGH),
            CollaborationSignals(),
            sync_override=SyncMode.HARD,
            eligible_model_count=2,
        )
        self.assertIs(decision.sync_mode, SyncMode.HARD)

    def test_every_supported_architecture_has_sync_and_round_defaults(self) -> None:
        for mode in SUPPORTED_ARCHITECTURES:
            self.assertIn(mode, ARCHITECTURE_SYNC_DEFAULTS)

    def test_identical_inputs_produce_identical_decisions(self) -> None:
        goal = "Research sources then verify the claims in the report."
        first = DesktopCollaborationRouter().decide(
            TaskShape.from_goal(goal),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        second = DesktopCollaborationRouter().decide(
            TaskShape.from_goal(goal),
            CollaborationSignals(),
            eligible_model_count=2,
        )
        self.assertEqual(first.to_dict(), second.to_dict())


class ContractRecordTests(unittest.TestCase):
    def test_decision_document_represents_all_five_architectures(self) -> None:
        router = DesktopCollaborationRouter()
        seen_modes: set[str] = set()
        cases = [
            (_shape(), CollaborationSignals(), 1, None),
            (_shape(complexity=ComplexityLevel.HIGH), CollaborationSignals(), 2, None),
            (_shape(verification_heavy=True), CollaborationSignals(), 2, None),
            (_shape(decomposable=True, coupled=False), CollaborationSignals(), 2, None),
            (
                _shape(
                    complexity=ComplexityLevel.MEDIUM,
                    decomposable=True,
                    coupled=True,
                ),
                CollaborationSignals(),
                2,
                None,
            ),
        ]
        for shape, signals, eligible, lock in cases:
            decision = CollaborationDecision(
                model_set=ModelSetDecision(
                    policy="auto_intelligence",
                    model_refs=("ollama:a", "ollama:b")[:eligible],
                    rationale="test",
                    confidence="high",
                    expected_cost_usd=0.0,
                ),
                architecture=router.decide(
                    shape,
                    signals,
                    requested_mode=lock,
                    eligible_model_count=eligible,
                ),
                eligible_model_count=eligible,
                task_shape=shape,
                signals=signals,
            )
            document = decision.to_dict()
            self.assertEqual(document["contract_version"], 1)
            self.assertIn("model_set", document)
            self.assertIn("architecture", document)
            architecture = document["architecture"]
            assert isinstance(architecture, dict)
            self.assertTrue(str(architecture["mode"]))
            seen_modes.add(str(architecture["mode"]))
            self.assertIsInstance(document["eligible_model_count"], int)
        self.assertEqual(
            seen_modes,
            {mode.value for mode in SUPPORTED_ARCHITECTURES},
        )

    def test_architecture_decision_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            ArchitectureDecision(
                mode="telepathy",  # type: ignore[arg-type]
                sync_mode=SyncMode.SOFT,
                policy_rule="test",
                triggers=(),
                reasons=(),
                requested_by="auto",
                expected_rounds=1,
            )

    def test_architecture_decision_rejects_orphan_degradation_reason(self) -> None:
        with self.assertRaises(ValueError):
            ArchitectureDecision(
                mode=CollaborationMode.SOLO,
                sync_mode=SyncMode.SOFT,
                policy_rule="test",
                triggers=(),
                reasons=(),
                requested_by="auto",
                expected_rounds=1,
                degradation_reason="missing candidate",
            )


class DeterministicDecompositionPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = DeterministicDecompositionPlanner()

    def test_sequencing_cues_slice_units_and_add_integration(self) -> None:
        plan, meta = self.planner.plan(
            "Research the sources, then analyze the data; then write the report",
            ("model-a", "model-b"),
        )
        self.assertEqual(meta["basis"], "sequencing")
        self.assertEqual(meta["work_unit_count"], 3)
        integration = plan.unit("integration")
        self.assertTrue(integration.required)
        self.assertEqual(
            integration.dependencies, ("unit-1", "unit-2", "unit-3")
        )
        self.assertEqual(len(plan.topological_levels()), 2)

    def test_assignees_rotate_round_robin(self) -> None:
        plan, meta = self.planner.plan(
            "First draft the outline. Then write the sections.",
            ("model-a", "model-b"),
        )
        owners = [
            unit.assignee_ids[0]
            for unit in plan.work_units
            if unit.id != "integration"
        ]
        self.assertEqual(owners, ["model-a", "model-b"])
        self.assertEqual(plan.unit("integration").assignee_ids[0], "model-a")

    def test_domain_groups_split_without_sequencing(self) -> None:
        plan, meta = self.planner.plan(
            "Debug the repository code and summarize the csv statistics",
            ("model-a",),
        )
        self.assertEqual(meta["basis"], "domains")
        self.assertGreaterEqual(meta["work_unit_count"], 2)

    def test_single_focus_degrades_to_one_unit_plus_integration(self) -> None:
        plan, meta = self.planner.plan("Say hello", ("model-a",))
        self.assertEqual(meta["basis"], "single")
        self.assertEqual(meta["work_unit_count"], 1)
        self.assertEqual(
            plan.unit("integration").dependencies, ("unit-1",)
        )

    def test_identical_inputs_produce_identical_plan_digests(self) -> None:
        goal = "Research sources then analyze the data"
        first, first_meta = self.planner.plan(goal, ("a", "b"))
        second, second_meta = self.planner.plan(goal, ("a", "b"))
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(first_meta, second_meta)

    def test_work_unit_overflow_is_grouped_not_dropped(self) -> None:
        goal = "; ".join(f"step {index}: do part {index}" for index in range(1, 9))
        plan, meta = self.planner.plan(goal, ("a", "b"))
        self.assertLessEqual(meta["work_unit_count"], self.planner.MAX_WORK_UNITS)
        self.assertIn("part 8", plan.work_units[-2].focus)

    def test_blank_participants_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.planner.plan("anything", (" ",))


if __name__ == "__main__":
    unittest.main()
