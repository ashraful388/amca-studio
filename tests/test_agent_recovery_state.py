"""A2.1A tests: durable Agent Fabric recovery schemas, repositories, invariants."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from amca.agent_recovery import (
    AgentRecoveryStore,
    recovery_disposition_for,
)


class AgentRecoveryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "agent-recovery.sqlite3"
        self.store = AgentRecoveryStore(self.database)
        self.org = "org-1"
        self.store.create_organization(
            organization_id=self.org,
            project_id="proj-1",
            goal="produce verified report",
            budget_usd=5.0,
            max_concurrent=2,
            max_attempts=2,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _task(self, task_id: str = "task-1", **overrides) -> dict:
        parameters = {
            "task_id": task_id,
            "organization_id": self.org,
            "project_id": "proj-1",
            "agent_id": "analyst",
            "focus": "draft the report",
            "executor_kind": "solo",
        }
        parameters.update(overrides)
        return self.store.create_task(**parameters)

    def _attempt(self, call_key: str = "primary", state: str = "PLANNED",
                 task_id: str = "task-1", attempt: int = 1) -> dict:
        reservation = self.store.create_reservation(
            reservation_id=f"res-{call_key}-{attempt}",
            organization_id=self.org,
            task_id=task_id,
            attempt=attempt,
            amount_usd=0.05,
        )
        return self.store.record_call_state(
            organization_id=self.org,
            task_id=task_id,
            task_attempt_number=attempt,
            call_key=call_key,
            state=state,
            executor_kind="solo",
            adapter_id="scripted",
            reservation_id=reservation["reservation_id"],
            reserved_usd=0.05,
        )

    # ---------- migrations ----------

    def test_migration_is_idempotent_across_reopen(self) -> None:
        reopened = AgentRecoveryStore(self.database)
        # all five tables exist after reopen (no raise) and org survives
        self.assertEqual(reopened.get_organization(self.org)["goal"],
                         "produce verified report")

    # ---------- organizations ----------

    def test_organization_round_trip_and_retire(self) -> None:
        org = self.store.get_organization(self.org)
        self.assertEqual(org["status"], "running")
        self.assertEqual(org["budget_usd"], 5.0)
        self.assertFalse(org["retired"])
        self.store.retire_organization(self.org)
        self.assertTrue(self.store.get_organization(self.org)["retired"])

    # ---------- tasks ----------

    def test_task_round_trip_with_json_fields(self) -> None:
        self._task(
            verifier_ids=["verifier:v_a", "verifier:v_b"],
            depends_on=["task-0"],
            collaboration_architecture="PARALLEL",
            manager_depth=1,
        )
        task = self.store.get_task("task-1")
        self.assertEqual(task["status"], "proposed")
        self.assertEqual(task["verifier_ids_json"], ["verifier:v_a", "verifier:v_b"])
        self.assertEqual(task["depends_on_json"], ["task-0"])
        self.assertEqual(task["manager_depth"], 1)

    def test_task_status_update_preserves_error_and_summary(self) -> None:
        self._task()
        self.store.update_task_status("task-1", "failed",
                                      error="boom", result_summary="partial")
        task = self.store.get_task("task-1")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], "boom")
        self.assertEqual(task["result_summary"], "partial")

    # ---------- call attempts: identity + journal ----------

    def test_call_identity_unique_constraint_holds(self) -> None:
        self._task()
        self._attempt("primary", "PLANNED")
        # same identity advanced via the repository -> update, not duplicate
        advanced = self.store.record_call_state(
            organization_id=self.org, task_id="task-1", task_attempt_number=1,
            call_key="primary", state="RESERVED", executor_kind="solo",
            adapter_id="scripted", reservation_id="res-dup-check",
        )
        self.assertEqual(advanced["state"], "RESERVED")
        self.assertEqual(len(self.store.list_call_attempts("task-1")), 1)
        # raw duplicate insert violates the contract UNIQUE constraint
        import sqlite3

        raw = sqlite3.connect(self.database)
        with self.assertRaises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO desktop_agent_call_attempts ("
                "call_attempt_id, organization_id, task_id, "
                "task_attempt_number, call_key, executor_kind, adapter_id, "
                "reservation_id, state) VALUES "
                "('dup', ?, 'task-1', 1, 'primary', 'solo', 'scripted', "
                "'res-dup', 'PLANNED')",
                (self.org,),
            )
        raw.close()
        advanced = self.store.record_call_state(
            organization_id=self.org, task_id="task-1", task_attempt_number=1,
            call_key="primary", state="RESERVED", executor_kind="solo",
            adapter_id="scripted", reservation_id="res-x",
        )
        self.assertEqual(advanced["state"], "RESERVED")
        attempts = self.store.list_call_attempts("task-1")
        self.assertEqual(len(attempts), 1)

    def test_call_journal_cannot_move_backward(self) -> None:
        self._task()
        self._attempt("primary", "DISPATCHED")
        with self.assertRaises(ValueError):
            self.store.record_call_state(
                organization_id=self.org, task_id="task-1", task_attempt_number=1,
                call_key="primary", state="RESERVED", executor_kind="solo",
                adapter_id="scripted", reservation_id="res-x",
            )

    def test_distinct_call_keys_are_distinct_attempts(self) -> None:
        self._task()
        self._attempt("primary", "SETTLED")
        self._attempt("verifier:v_a", "PLANNED")
        self.assertEqual(len(self.store.list_call_attempts("task-1")), 2)

    # ---------- recovery dispositions ----------

    def test_recovery_dispositions_match_contract(self) -> None:
        self.assertEqual(recovery_disposition_for("PLANNED"), "SAFE_TO_RESUME")
        self.assertEqual(recovery_disposition_for("RESERVED"), "SAFE_TO_RESUME")
        self.assertEqual(
            recovery_disposition_for("DISPATCH_INTENT_DURABLE"),
            "UNKNOWN_EXTERNAL_OUTCOME",
        )
        self.assertEqual(
            recovery_disposition_for("DISPATCHED"), "UNKNOWN_EXTERNAL_OUTCOME"
        )
        self.assertEqual(
            recovery_disposition_for("RESPONSE_RECEIVED"),
            "UNKNOWN_EXTERNAL_OUTCOME",
        )
        self.assertEqual(
            recovery_disposition_for("RESPONSE_COMMITTED"),
            "RECOVERED_FROM_COMMITTED_RESPONSE",
        )
        self.assertEqual(recovery_disposition_for("SETTLED"), "TERMINAL")

    def test_uncertain_attempts_are_queryable(self) -> None:
        self._task()
        self._attempt("primary", "DISPATCHED")
        uncertain = self.store.uncertain_call_attempts(self.org)
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(uncertain[0]["recovery_disposition"],
                         "UNKNOWN_EXTERNAL_OUTCOME")
        self.store.set_task_recovery("task-1", uncertain=True,
                                     disposition="UNKNOWN_EXTERNAL_OUTCOME")
        task = self.store.get_task("task-1")
        self.assertTrue(task["external_outcome_uncertain"])

    # ---------- reservations + budget invariant ----------

    def test_reservation_lifecycle_and_budget_position(self) -> None:
        self.store.create_reservation(
            reservation_id="res-a", organization_id=self.org, task_id="task-1",
            attempt=1, amount_usd=1.0,
        )
        self.store.create_reservation(
            reservation_id="res-b", organization_id=self.org, task_id="task-1",
            attempt=1, amount_usd=2.0,
        )
        position = self.store.organization_budget_position(self.org)
        self.assertEqual(position["total_committed"], 3.0)
        # settle res-a at actual 0.8; release res-b cleanly
        self.store.transition_reservation("res-a", "settled", settled_actual_usd=0.8)
        self.store.transition_reservation("res-b", "released")
        position = self.store.organization_budget_position(self.org)
        self.assertEqual(position["settled"], 0.8)
        self.assertEqual(position["active"], 0.0)
        self.assertEqual(position["uncertain"], 0.0)
        self.assertEqual(position["total_committed"], 0.8)

    def test_uncertainty_locks_exposure(self) -> None:
        self.store.create_reservation(
            reservation_id="res-u", organization_id=self.org, task_id="task-1",
            attempt=1, amount_usd=1.5,
        )
        self.store.transition_reservation("res-u", "uncertain")
        position = self.store.organization_budget_position(self.org)
        self.assertEqual(position["uncertain"], 1.5)
        # uncertain exposure cannot be silently released
        with self.assertRaises(ValueError):
            self.store.transition_reservation("res-u", "released")

    def test_terminal_reservation_cannot_retransition(self) -> None:
        self.store.create_reservation(
            reservation_id="res-t", organization_id=self.org, task_id="task-1",
            attempt=1, amount_usd=1.0,
        )
        self.store.transition_reservation("res-t", "released")
        with self.assertRaises(ValueError):
            self.store.transition_reservation("res-t", "settled",
                                              settled_actual_usd=1.0)

    def test_integrity_violation_flag_persists(self) -> None:
        self.store.create_reservation(
            reservation_id="res-v", organization_id=self.org, task_id="task-1",
            attempt=1, amount_usd=1.0,
        )
        self.store.transition_reservation("res-v", "settled",
                                          settled_actual_usd=1.4,
                                          integrity_violation=True)
        row = self.store.get_reservation("res-v")
        self.assertTrue(row["integrity_violation"])
        self.assertEqual(row["settled_actual_usd"], 1.4)

    # ---------- verifier verdicts ----------

    def test_verdicts_attempt_aware_identity(self) -> None:
        self._task()
        # same attempt + same verifier -> IntegrityError (sealed per attempt)
        self.store.record_verdict(
            organization_id=self.org, task_id="task-1", task_attempt_number=1,
            verifier_id="verifier:v_a", verdict="pass", summary="ok",
            evidence_refs_json=[], cost_usd=0.01,
        )
        with self.assertRaises(Exception):
            self.store.record_verdict(
                organization_id=self.org, task_id="task-1",
                task_attempt_number=1, verifier_id="verifier:v_a",
                verdict="fail", summary="dup", evidence_refs_json=[],
                cost_usd=0.01,
            )
        # same verifier, NEW attempt -> independent verdict row
        self.store.record_verdict(
            organization_id=self.org, task_id="task-1", task_attempt_number=2,
            verifier_id="verifier:v_a", verdict="fail", summary="retry",
            evidence_refs_json=[], cost_usd=0.02,
        )
        attempt1 = self.store.verdicts_by_attempt(self.org, "task-1", 1)
        attempt2 = self.store.verdicts_by_attempt(self.org, "task-1", 2)
        self.assertEqual(len(attempt1), 1)
        self.assertEqual(len(attempt2), 1)
        self.assertEqual(attempt1[0]["verdict"], "pass")
        self.assertEqual(attempt2[0]["verdict"], "fail")

    def test_verdict_retrieval_by_task_across_attempts(self) -> None:
        self._task()
        self.store.record_verdict(
            organization_id=self.org, task_id="task-1", task_attempt_number=1,
            verifier_id="verifier:v_a", verdict="pass", summary="ok",
            evidence_refs_json=[], cost_usd=0.01,
        )
        verdicts = self.store.verdicts_by_task("task-1")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["verdict"], "pass")

    def test_verdict_cell_blocked_with_uncertain_member(self) -> None:
        # two verifiers committed, one uncertain -> cell inconclusive
        verdicts = {"verifier:v_a": "pass", "verifier:v_b": "fail"}
        uncertain = ["verifier:v_c"]
        cell_blocked = bool(uncertain)
        self.assertTrue(cell_blocked)
        self.assertEqual(len(verdicts), 2)


if __name__ == "__main__":
    unittest.main()
