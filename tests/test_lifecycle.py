from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from amca.lifecycle import (
    ProjectAlreadyExistsError,
    ProjectDefinition,
    ProjectLifecycleError,
    ProjectNotFoundError,
    create_project,
    list_projects,
    resume_project,
    start_project,
    stop_project,
    wait_for_approval_project,
)
from amca.domain import GoalContract, GoalCriterion
from amca.security import SensitiveDataError
from amca.storage import SQLiteStore


def definition_document(project_id: str = "project-one") -> dict[str, object]:
    return {
        "project_id": project_id,
        "objective": "Produce a verified answer and a saved report.",
        "success_criteria": [
            {
                "criterion_id": "answer-value",
                "description": "The computed answer equals 42.",
                "evaluator_kind": "exact_value",
                "expected_value": 42,
                "evidence_requirement": 2,
                "decision_class": "critical",
            },
            {
                "criterion_id": "report-exists",
                "description": "The report artifact exists.",
                "evaluator_kind": "file_exists",
                "required": True,
            },
        ],
        "constraints": [
            "Do not transmit source material.",
            "Cite every external claim.",
        ],
        "budget": {
            "iteration_cap": 8,
            "wall_clock_seconds": 120.5,
            "api_cost_usd": 0,
        },
        "privacy_mode": "offline",
        "compute_policy": "research",
    }


class ProjectDefinitionTests(unittest.TestCase):
    def test_parse_rich_fresh_goal_contract(self) -> None:
        definition = ProjectDefinition.from_mapping(definition_document())

        self.assertEqual(definition.project_id, "project-one")
        self.assertEqual(definition.goal_contract.goal, definition_document()["objective"])
        self.assertEqual(len(definition.goal_contract.criteria), 2)
        criterion = definition.goal_contract.criteria[0]
        self.assertEqual(criterion.id, "answer-value")
        self.assertEqual(criterion.expected_value, 42)
        self.assertEqual(criterion.evidence_requirement, 2)
        self.assertEqual(criterion.decision_class.value, "critical")
        self.assertEqual(definition.goal_contract.max_iterations, 8)
        self.assertEqual(definition.goal_contract.max_runtime_seconds, 120.5)
        self.assertEqual(definition.goal_contract.max_api_cost_usd, 0.0)
        self.assertEqual(definition.privacy_mode.value, "offline")
        self.assertEqual(definition.compute_policy.value, "research")
        self.assertEqual(len(definition.constraints), 2)

    def test_definition_rejects_unsafe_or_runtime_result_fields(self) -> None:
        unsafe = definition_document()
        unsafe["success_criteria"][0]["expected_value"] = {  # type: ignore[index]
            "clientSecret": "canary"
        }
        with self.assertRaises(SensitiveDataError):
            ProjectDefinition.from_mapping(unsafe)

        result_injection = definition_document()
        result_injection["success_criteria"][0]["status"] = "pass"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unsupported fields: status"):
            ProjectDefinition.from_mapping(result_injection)

        satisfied = GoalCriterion(description="Forged", satisfied=True)
        with self.assertRaisesRegex(ValueError, "runtime result fields"):
            ProjectDefinition(
                project_id="direct-constructor",
                goal_contract=GoalContract(goal="Bypass", criteria=[satisfied]),
            )

    def test_definition_rejects_ambiguous_or_nonfinite_contracts(self) -> None:
        conflicting = definition_document()
        conflicting["goal"] = "A different goal"
        with self.assertRaisesRegex(ValueError, "conflicts with alias"):
            ProjectDefinition.from_mapping(conflicting)

        nonfinite = definition_document()
        nonfinite["budget"]["wall_clock_seconds"] = math.inf  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "finite"):
            ProjectDefinition.from_mapping(nonfinite)

        no_required = definition_document()
        for criterion in no_required["success_criteria"]:  # type: ignore[union-attr]
            criterion["required"] = False
        with self.assertRaisesRegex(ValueError, "at least one criterion"):
            ProjectDefinition.from_mapping(no_required)

        duplicate_ids = definition_document()
        duplicate_ids["success_criteria"][1]["criterion_id"] = "answer-value"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "unique"):
            ProjectDefinition.from_mapping(duplicate_ids)

        zero_iterations = definition_document()
        zero_iterations["budget"] = {"max_iterations": 0}
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            ProjectDefinition.from_mapping(zero_iterations)


class ProjectLifecycleTests(unittest.TestCase):
    def test_create_and_list_persist_contract_policy_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "amca.sqlite3"
            definition = ProjectDefinition.from_mapping(definition_document())
            with SQLiteStore(database) as store:
                state = create_project(store, definition)
                summaries = list_projects(store)

                self.assertEqual(state.status, "created")
                self.assertEqual(state.version, 0)
                self.assertEqual(state.current_focus, state.goal)
                self.assertEqual(state.user_constraints, list(definition.constraints))
                self.assertEqual(store.snapshot_versions(state.project_id), [0])
                self.assertEqual(len(summaries), 1)
                summary = summaries[0].to_dict()
                self.assertEqual(summary["project_id"], "project-one")
                self.assertEqual(summary["status"], "created")
                self.assertEqual(summary["required_criteria"], 2)
                self.assertEqual(summary["budget"]["max_iterations"], 8)

                with self.assertRaises(ProjectAlreadyExistsError):
                    create_project(store, definition)

            with SQLiteStore(database) as reopened:
                restored = reopened.load_state("project-one")
                self.assertIsNotNone(restored)
                self.assertEqual(restored.goal_contract.max_api_cost_usd, 0.0)  # type: ignore[union-attr]

    def test_start_and_stop_are_durable_auditable_transitions(self) -> None:
        with SQLiteStore(":memory:") as store:
            create_project(store, ProjectDefinition.from_mapping(definition_document()))
            started = start_project(store, "project-one", reason="Begin bounded run")
            stopped = stop_project(store, "project-one", reason="User requested pause")
            repeated = stop_project(store, "project-one", reason="User requested pause")

            self.assertTrue(started.changed)
            self.assertEqual(started.from_status, "created")
            self.assertEqual(started.to_status, "active")
            self.assertEqual(started.state_version, 1)
            self.assertTrue(stopped.changed)
            self.assertEqual(stopped.state_version, 2)
            self.assertEqual(stopped.to_status, "stopped")
            self.assertFalse(repeated.changed)
            self.assertEqual(repeated.state_version, 2)
            self.assertIsNone(repeated.event_id)
            self.assertEqual(store.snapshot_versions("project-one"), [0, 1, 2])

            events = store.load_events("project-one")
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].payload["lifecycle"]["action"], "start")
            self.assertEqual(
                events[1].payload["lifecycle"]["reason"], "User requested pause"
            )
            restored = store.load_state("project-one")
            self.assertEqual(restored.status, "stopped")  # type: ignore[union-attr]

    def test_invalid_transitions_and_missing_projects_fail_closed(self) -> None:
        with SQLiteStore(":memory:") as store:
            with self.assertRaises(ProjectNotFoundError):
                stop_project(store, "missing")
            create_project(store, ProjectDefinition.from_mapping(definition_document()))
            start_project(store, "project-one")
            self.assertFalse(start_project(store, "project-one").changed)
            stop_project(store, "project-one")
            with self.assertRaises(ProjectLifecycleError):
                start_project(store, "project-one")

    def test_status_filter_does_not_expose_full_state(self) -> None:
        with SQLiteStore(":memory:") as store:
            create_project(store, ProjectDefinition.from_mapping(definition_document()))
            create_project(
                store,
                ProjectDefinition.from_mapping(definition_document("project-two")),
            )
            stop_project(store, "project-two")
            created = list_projects(store, status="created")
            stopped = list_projects(store, status="stopped")

        self.assertEqual([item.project_id for item in created], ["project-one"])
        self.assertEqual([item.project_id for item in stopped], ["project-two"])

    def test_resume_api_has_the_same_no_external_replay_contract(self) -> None:
        with SQLiteStore(":memory:") as store:
            create_project(store, ProjectDefinition.from_mapping(definition_document()))
            report = resume_project(store, "project-one", dry_run=True)
            self.assertTrue(report.completed)
            self.assertTrue(report.dry_run)
            self.assertEqual(report.pending_events_found, 0)
            with self.assertRaises(ProjectNotFoundError):
                resume_project(store, "missing")

    def test_approval_wait_and_start_are_explicit_transitions(self) -> None:
        with SQLiteStore(":memory:") as store:
            create_project(store, ProjectDefinition.from_mapping(definition_document()))
            waiting = wait_for_approval_project(store, "project-one")
            repeated = wait_for_approval_project(store, "project-one")
            started = start_project(store, "project-one")

            self.assertTrue(waiting.changed)
            self.assertEqual(waiting.to_status, "waiting_for_approval")
            self.assertFalse(repeated.changed)
            self.assertTrue(started.changed)
            self.assertEqual(started.from_status, "waiting_for_approval")
            self.assertEqual(started.to_status, "active")
            self.assertEqual(store.snapshot_versions("project-one"), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
