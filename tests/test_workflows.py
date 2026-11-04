from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from amca.benchmarks import (
    CriterionStatus,
    DeterministicFixtureEvaluator,
    ExternalModelEvaluationUnsupported,
    load_benchmark_suite,
    require_evaluator,
    run_benchmark_suite,
)
from amca.cli import main
from amca.domain import CognitiveEvent, EventType, ProjectState
from amca.exporting import audit_log_view, build_audit_export, redact_secrets
from amca.recovery import ProjectNotFoundError, recover_project
from amca.storage import SQLiteStore


FIXTURES = Path(__file__).resolve().parents[1] / "benchmarks" / "tasks.json"


class BenchmarkWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_deterministic_evaluator_records_declared_rubric_evidence(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        task = suite.task("math-crt-001")
        self.assertIsNotNone(task)
        evaluation = DeterministicFixtureEvaluator().evaluate(
            task,  # type: ignore[arg-type]
            {
                "answer": 13148,
                "modulus": 17017,
                "residue_verification": {"7": 2, "11": 3, "13": 5, "17": 7},
                "proof_summary": (
                    "The four moduli are pairwise coprime; therefore the Chinese "
                    "Remainder Theorem applies, and all solutions are congruent "
                    "modulo 17017."
                ),
            },
        )

        self.assertEqual(evaluation.status, "passed")
        self.assertEqual(evaluation.score, 100.0)
        by_id = {outcome.criterion_id: outcome for outcome in evaluation.outcomes}
        self.assertIs(by_id["answer"].status, CriterionStatus.PASS)
        self.assertIs(by_id["modulus"].status, CriterionStatus.PASS)
        self.assertIs(by_id["residue_check"].status, CriterionStatus.PASS)
        self.assertIs(by_id["uniqueness_proof"].status, CriterionStatus.PASS)

    async def test_runner_executes_all_eight_fixtures_without_quality_claim(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "benchmarks.sqlite3") as store:
                manifest = await run_benchmark_suite(
                    suite,
                    store,
                    conditions=("solo",),
                    run_id="offline-test",
                )
                projects = store.list_projects()

        self.assertEqual(manifest["fixture_count"], 8)
        self.assertEqual(manifest["run_count"], 8)
        self.assertEqual(len(projects), 8)
        self.assertEqual(manifest["summary"]["external_model_calls"], 0)
        self.assertEqual(manifest["summary"]["external_judge_calls"], 0)
        self.assertEqual(
            manifest["summary"]["evaluation_status_counts"],
            {"not_evaluated": 8},
        )
        self.assertTrue(
            all(
                result["execution"]["kind"]
                == "offline_scripted_orchestration_probe"
                for result in manifest["results"]
            )
        )
        unsigned = dict(manifest)
        manifest_digest = unsigned.pop("manifest_sha256")
        rendered = json.dumps(
            unsigned,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertEqual(
            manifest_digest,
            hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        )

    async def test_response_artifact_passes_flow_through_kernel_decisions(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        candidate = {
            "answer": 13148,
            "modulus": 17017,
            "residue_verification": {"7": 2, "11": 3, "13": 5, "17": 7},
            "proof_summary": (
                "The four moduli are pairwise coprime. The Chinese Remainder "
                "Theorem applies. All solutions are congruent modulo 17017."
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteStore(Path(directory) / "benchmarks.sqlite3") as store:
                manifest = await run_benchmark_suite(
                    suite,
                    store,
                    conditions=("solo",),
                    task_ids=("math-crt-001",),
                    responses={"math-crt-001": candidate},
                    run_id="response-test",
                )
                state = store.load_state(
                    "benchmark-response-test-solo-math-crt-001"
                )

        result = manifest["results"][0]
        self.assertEqual(result["evaluation"]["status"], "passed")
        self.assertEqual(result["execution"]["verified_criterion_count"], 4)
        self.assertEqual(result["execution"]["decision_record_count"], 4)
        self.assertTrue(result["execution"]["kernel_goal_satisfied"])
        self.assertIsNotNone(state)
        self.assertEqual(len(state.decision_records), 4)  # type: ignore[union-attr]

    def test_external_model_evaluator_is_explicitly_unsupported(self) -> None:
        with self.assertRaisesRegex(
            ExternalModelEvaluationUnsupported, "not implemented or validated"
        ):
            require_evaluator("external-model")

    def test_coding_fixture_runs_inside_bounded_sandbox(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        task = suite.task("coding-dag-001")
        self.assertIsNotNone(task)
        source = r'''
import heapq

def analyze_dag(durations, dependencies):
    if any(not isinstance(key, str) or not isinstance(value, int) or value < 0 for key, value in durations.items()):
        raise ValueError("invalid duration")
    successors = {key: [] for key in durations}
    predecessors = {key: [] for key in durations}
    indegree = {key: 0 for key in durations}
    for predecessor, successor in dependencies:
        if predecessor not in durations or successor not in durations:
            raise KeyError("unknown endpoint")
        successors[predecessor].append(successor)
        predecessors[successor].append(predecessor)
        indegree[successor] += 1
    available = [key for key, degree in indegree.items() if degree == 0]
    heapq.heapify(available)
    order = []
    earliest_start = {}
    earliest_finish = {}
    critical_chain = {}
    while available:
        node = heapq.heappop(available)
        order.append(node)
        if predecessors[node]:
            start = max(earliest_finish[parent] for parent in predecessors[node])
            critical_parents = [parent for parent in predecessors[node] if earliest_finish[parent] == start]
            parent = min(critical_parents, key=lambda item: critical_chain[item])
            critical_chain[node] = critical_chain[parent] + [node]
        else:
            start = 0
            critical_chain[node] = [node]
        earliest_start[node] = start
        earliest_finish[node] = start + durations[node]
        for successor in successors[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heapq.heappush(available, successor)
    if len(order) != len(durations):
        raise ValueError("cycle")
    makespan = max(earliest_finish.values(), default=0)
    ending = [node for node in order if earliest_finish[node] == makespan]
    path = min((critical_chain[node] for node in ending), default=[])
    return {
        "topological_order": order,
        "earliest_start": earliest_start,
        "earliest_finish": earliest_finish,
        "makespan": makespan,
        "critical_path": path,
    }
'''
        candidate = {
            "source": source,
            "rubric_evidence": {
                "complexity": [
                    "topological processing is O((V + E) log V) or better apart from output construction",
                    "no enumeration of all topological orders or all source-to-sink paths",
                ],
                "code_quality": [
                    "deterministic tie handling",
                    "input validation",
                    "no mutation of caller inputs",
                ],
            },
        }

        evaluation = DeterministicFixtureEvaluator().evaluate(
            task, candidate  # type: ignore[arg-type]
        )

        self.assertEqual(evaluation.status, "passed", evaluation.to_dict())
        self.assertEqual(evaluation.score, 100.0)
        self.assertTrue(all(outcome.status is CriterionStatus.PASS for outcome in evaluation.outcomes))

    def test_all_fixture_criterion_kinds_produce_machine_results(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        evaluator = DeterministicFixtureEvaluator()
        for task in suite.tasks:
            candidate: object = (
                {"source": f"def {task.response_contract['entrypoint']}(*args, **kwargs):\n    return None\n"}
                if task.response_contract.get("kind") == "python_function"
                else {}
            )
            with self.subTest(task=task.task_id):
                evaluation = evaluator.evaluate(task, candidate)
                self.assertNotIn(
                    CriterionStatus.UNSUPPORTED,
                    {outcome.status for outcome in evaluation.outcomes},
                )
                self.assertTrue(all(outcome.evidence for outcome in evaluation.outcomes))

    def test_structured_sensor_contract_passes_declared_constraints(self) -> None:
        suite = load_benchmark_suite(FIXTURES)
        task = suite.task("research-sensor-validation-002")
        self.assertIsNotNone(task)
        matrix = [
            {
                "matrix": medium,
                "temperature_c": temperature,
                "controls": (
                    "reference-instrument paired measurements; interferent challenges; "
                    "repeatability; between-unit variation; one-week drift"
                ),
            }
            for medium in ("tap", "river", "saline")
            for temperature in (10, 25, 40)
        ]
        candidate = {
            "objectives": "Validate accuracy and drift.",
            "sensor_allocation": {"calibration": 24, "validation": 30},
            "experimental_matrix": matrix,
            "calibration_validation_split": (
                "Calibration and held-out validation use independent source batches; "
                "validation data are not used to refit calibration."
            ),
            "blinding": "Sample labels are blinded during measurement and primary analysis.",
            "schedule": "Six-week schedule with decision gates.",
            "budget": {"total_usd": 24000},
            "analysis": "Uncertainty intervals and matrix-by-temperature effects.",
            "acceptance_rule": {
                "absolute_bias_percent_full_scale_max": 5,
                "rmse_percent_full_scale_max": 8,
                "drift_percent_full_scale_per_week_max": 2,
                "valid_reading_rate_percent_min": 95,
                "require_all": True,
            },
            "failure_handling": "Predefined missing and failed-unit handling.",
            "safety": "Chain of custody and appropriate water-sample handling.",
            "risks": "Contingency without inventing or replacing unfavorable results.",
        }

        evaluation = DeterministicFixtureEvaluator().evaluate(
            task, candidate  # type: ignore[arg-type]
        )

        self.assertEqual(evaluation.status, "passed", evaluation.to_dict())
        self.assertEqual(evaluation.score, 100.0)


class RecoveryAndExportWorkflowTests(unittest.TestCase):
    def _database_with_pending_event(self, root: Path) -> tuple[Path, CognitiveEvent]:
        database = root / "state.sqlite3"
        event = CognitiveEvent(
            id="durable-pending-event",
            event_type=EventType.EVIDENCE,
            source="offline-worker",
            payload={
                "evidence": {
                    "fact": "recoverable",
                    "provenance": {"locator": "offline-fixture"},
                }
            },
            state_version=0,
        )
        with SQLiteStore(database) as store:
            store.save_state(ProjectState(project_id="recoverable", goal="Recover"))
            store.append_event("recoverable", event)
        return database, event

    def test_recovery_replays_pending_events_once_and_never_restarts_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _ = self._database_with_pending_event(Path(directory))
            with SQLiteStore(database) as store:
                report = recover_project(store, "recoverable")
                repeated = recover_project(store, "recoverable")

        self.assertTrue(report.completed)
        self.assertEqual(report.start_version, 0)
        self.assertEqual(report.end_version, 1)
        self.assertEqual(report.events_applied, 1)
        self.assertEqual(report.to_dict()["model_calls_restarted"], False)
        self.assertEqual(repeated.pending_events_found, 0)
        self.assertEqual(repeated.events_applied, 0)

    def test_recovery_reports_missing_projects(self) -> None:
        with SQLiteStore(":memory:") as store:
            with self.assertRaises(ProjectNotFoundError):
                recover_project(store, "missing")

    def test_export_is_stable_and_hash_chained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, event = self._database_with_pending_event(Path(directory))
            with SQLiteStore(database) as store:
                recover_project(store, "recoverable")
                first = build_audit_export(store, "recoverable")
                second = build_audit_export(store, "recoverable")

        self.assertEqual(first, second)
        serialized = json.dumps(first, sort_keys=True)
        self.assertEqual(
            first["event_log"][0]["event"]["payload"]["evidence"]["provenance"],
            {"locator": "offline-fixture"},
        )
        self.assertEqual(first["audit"]["event_count"], 1)
        self.assertEqual(first["audit"]["pending_event_count"], 0)
        self.assertEqual(first["audit"]["event_digest_mismatch_count"], 0)
        self.assertTrue(first["event_log"][0]["digest_matches_state"])
        self.assertEqual(
            first["event_log"][0]["event_sha256"],
            hashlib.sha256(event.to_json().encode("utf-8")).hexdigest(),
        )
        compact = audit_log_view(first)
        self.assertNotIn("latest_state", compact)
        self.assertEqual(compact["event_log"], first["event_log"])

    def test_export_redaction_remains_defense_in_depth(self) -> None:
        redacted = redact_secrets(
            {"safe": "kept", "nested": [{"api_key": "in-memory-canary"}]}
        )
        self.assertEqual(redacted["safe"], "kept")
        self.assertEqual(redacted["nested"][0]["api_key"], "[REDACTED]")
        self.assertNotIn("in-memory-canary", json.dumps(redacted))

    def test_cli_resume_export_and_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _ = self._database_with_pending_event(root)
            recovery_output = root / "recovery.json"
            export_output = root / "export.json"
            audit_output = root / "audit.json"
            with contextlib.redirect_stdout(io.StringIO()):
                resume_code = main(
                    [
                        "resume",
                        "--database",
                        str(database),
                        "--project-id",
                        "recoverable",
                        "--output",
                        str(recovery_output),
                    ]
                )
                export_code = main(
                    [
                        "export",
                        "--database",
                        str(database),
                        "--project-id",
                        "recoverable",
                        "--output",
                        str(export_output),
                    ]
                )
                audit_code = main(
                    [
                        "audit-log",
                        "--database",
                        str(database),
                        "--project-id",
                        "recoverable",
                        "--output",
                        str(audit_output),
                    ]
                )

            recovery = json.loads(recovery_output.read_text(encoding="utf-8"))
            exported = json.loads(export_output.read_text(encoding="utf-8"))
            audit = json.loads(audit_output.read_text(encoding="utf-8"))

        self.assertEqual((resume_code, export_code, audit_code), (0, 0, 0))
        self.assertEqual(recovery["events_applied"], 1)
        self.assertNotIn("state", recovery)
        self.assertNotIn("must-not-be-exported", json.dumps(recovery))
        self.assertEqual(exported["kind"], "amca_project_audit_export")
        self.assertEqual(audit["kind"], "amca_auditable_collaboration_log")
        self.assertNotIn("snapshots", audit)


class BenchmarkCLITests(unittest.TestCase):
    def test_cli_runs_selected_fixture_and_refuses_external_judge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "benchmarks",
                        "run",
                        "--file",
                        str(FIXTURES),
                        "--condition",
                        "solo",
                        "--task",
                        "math-crt-001",
                        "--run-id",
                        "cli-test",
                        "--database",
                        str(root / "benchmarks.sqlite3"),
                    ]
                )
            manifest = json.loads(stdout.getvalue())
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                unsupported_code = main(
                    [
                        "benchmarks",
                        "run",
                        "--file",
                        str(FIXTURES),
                        "--evaluator",
                        "external-model",
                        "--database",
                        str(root / "unused.sqlite3"),
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(manifest["fixture_count"], 1)
        self.assertEqual(manifest["summary"]["external_model_calls"], 0)
        self.assertEqual(unsupported_code, 2)
        self.assertIn("not implemented or validated", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
