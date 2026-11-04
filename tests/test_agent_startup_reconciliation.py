"""A2.1C startup reconciliation tests.

Fixtures simulate durable state left behind by a previous process, then open
a NEW AgentRecoveryStore instance (fresh process/startup) and reconcile.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from amca.agent_reconciliation import (
    AgentFabricSchedulingBlocked,
    SchedulingBarrier,
    TASK_RECOVERY_BLOCKED_UNCERTAIN,
    TASK_RECOVERY_RECOVERED_COMMITTED,
    TASK_RECOVERY_RESUMABLE,
    TASK_RECOVERY_TERMINAL,
    append_reconciliation_summary,
    reconcile_agent_fabric_state,
    reconstruct_committed_result,
)
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import ExecutorKind, NanoScheduler


class ReconciliationFixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.database = self.root / "amca.db"
        self.store = AgentRecoveryStore(self.database)

    def reopen(self) -> AgentRecoveryStore:
        """Simulate a new process opening the canonical database."""

        return AgentRecoveryStore(self.database)

    def seed_org(self, store: AgentRecoveryStore, org_id: str = "org", budget_usd: float = 1.0) -> None:
        store.create_organization(
            organization_id=org_id,
            project_id="p",
            goal="g",
            budget_usd=budget_usd,
            max_concurrent=2,
            max_attempts=2,
        )

    def seed_task(
        self,
        store: AgentRecoveryStore,
        task_id: str = "t1",
        org_id: str = "org",
        executor_kind: str = "nano_local_model",
    ) -> None:
        store.create_task(
            task_id=task_id,
            organization_id=org_id,
            project_id="p",
            agent_id="w",
            focus="work",
            executor_kind=executor_kind,
        )

    def journal(
        self,
        store: AgentRecoveryStore,
        state: str,
        *,
        task_id: str = "t1",
        attempt: int = 1,
        call_key: str | None = None,
        org_id: str = "org",
        adapter_id: str = "w",
        reservation_id: str = "",
        reserved_usd: float = 0.0,
        actual_cost_usd: float = 0.0,
        response_payload: str | None = None,
    ) -> dict[str, Any]:
        return store.record_call_state(
            organization_id=org_id,
            task_id=task_id,
            task_attempt_number=attempt,
            call_key=call_key or f"task:{task_id}:attempt:{attempt}",
            state=state,
            executor_kind="nano_local_model",
            adapter_id=adapter_id,
            reservation_id=reservation_id,
            reserved_usd=reserved_usd,
            actual_cost_usd=actual_cost_usd,
            response_payload=response_payload,
        )

    def reserve(
        self,
        store: AgentRecoveryStore,
        *,
        amount_usd: float = 0.05,
        task_id: str = "t1",
        attempt: int = 1,
        org_id: str = "org",
        reservation_id: str = "res-1",
    ) -> dict[str, Any]:
        store.create_reservation(
            reservation_id=reservation_id,
            organization_id=org_id,
            task_id=task_id,
            attempt=attempt,
            amount_usd=amount_usd,
        )
        record = store.get_reservation(reservation_id)
        assert record is not None
        return record

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def snapshot(self) -> dict[str, list[tuple]]:
        connection = sqlite3.connect(self.database)
        try:
            tables = [
                "desktop_agent_organizations",
                "desktop_agent_tasks",
                "desktop_agent_call_attempts",
                "desktop_agent_reservations",
            ]
            return {
                table: connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in tables
            }
        finally:
            connection.close()


class BarrierUnitTests(ReconciliationFixtureBase):
    def test_barrier_starts_reconciling_and_fails_closed(self) -> None:
        barrier = SchedulingBarrier()
        with self.assertRaises(AgentFabricSchedulingBlocked):
            barrier.ensure_schedulable()
        scheduler = NanoScheduler({}, budget_usd=1.0, scheduling_barrier=barrier)
        org = scheduler.schedule(
            [{"id": "t", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "focus": "f"}],
            organization_id="org-b", project_id="p", goal="g",
        )
        with self.assertRaises(AgentFabricSchedulingBlocked):
            asyncio.run(scheduler.run(org))

    def test_barrier_ready_allows_scheduling_and_close_is_terminal(self) -> None:
        barrier = SchedulingBarrier()
        barrier.mark_ready()
        scheduler = NanoScheduler(
            {}, budget_usd=1.0, scheduling_barrier=barrier
        )
        org = scheduler.schedule(
            [{"id": "t", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL, "focus": "f"}],
            organization_id="org-r", project_id="p", goal="g",
        )
        asyncio.run(scheduler.run(org))
        self.assertEqual(org.tasks["t"].status.value if hasattr(org.tasks["t"].status, "value") else org.tasks["t"].status, "completed")

        closed = SchedulingBarrier()
        closed.mark_closed("boom")
        with self.assertRaises(AgentFabricSchedulingBlocked):
            closed.ensure_schedulable()
        with self.assertRaises(ValueError):
            closed.mark_ready()
        with self.assertRaises(ValueError):
            barrier.mark_ready()


class MatrixReconciliationTests(ReconciliationFixtureBase):
    def reconcile(self, store: AgentRecoveryStore | None = None) -> dict[str, Any]:
        return reconcile_agent_fabric_state(store or self.reopen())

    def test_planned_is_classified_safely(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.journal(self.store, "PLANNED")
        result = self.reconcile()
        self.assertEqual(result["summary"]["integrity_errors"], 0)
        task = self.reopen().get_task("t1")
        self.assertEqual(task["recovery_disposition"], TASK_RECOVERY_RESUMABLE)

    def test_reserved_without_intent_is_safe_to_resume_without_new_reservation(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(self.store, "RESERVED", reservation_id="res-1", reserved_usd=0.05)
        result = self.reconcile()
        self.assertEqual(result["summary"]["integrity_errors"], 0)
        fresh = self.reopen()
        task = fresh.get_task("t1")
        self.assertEqual(task["recovery_disposition"], TASK_RECOVERY_RESUMABLE)
        row = fresh.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        self.assertEqual(row["recovery_disposition"], "SAFE_TO_RESUME")
        reservations = fresh.list_reservations("org")
        self.assertEqual(len(reservations), 1, "must not create a second reservation")
        self.assertEqual(reservations[0]["status"], "active")

    def test_unknown_states_block_with_uncertain_exposure(self) -> None:
        states = ("DISPATCH_INTENT_DURABLE", "DISPATCHED", "RESPONSE_RECEIVED")
        for index, state in enumerate(states, start=1):
            org_id = f"org-{index}"
            self.seed_org(self.store, org_id, budget_usd=1.0)
            self.seed_task(self.store, f"task-{index}", org_id)
            self.reserve(self.store, org_id=org_id, task_id=f"task-{index}",
                         reservation_id=f"res-{index}", amount_usd=0.02)
            self.journal(
                self.store, state, org_id=org_id, task_id=f"task-{index}",
                call_key=f"task:task-{index}:attempt:1",
                reservation_id=f"res-{index}",
            )
        result = self.reconcile()
        self.assertEqual(result["summary"]["unknown_external_outcome"], 3)
        self.assertEqual(result["summary"]["uncertain_exposures"], 3)
        fresh = self.reopen()
        for index, state in enumerate(states, start=1):
            org_id = f"org-{index}"
            task_id = f"task-{index}"
            row = fresh.get_call_attempt(org_id, task_id, 1,
                                         f"task:{task_id}:attempt:1")
            self.assertIsNotNone(row, state)
            self.assertEqual(row["recovery_disposition"],
                             "UNKNOWN_EXTERNAL_OUTCOME", state)
            reservations = [
                r for r in fresh.list_reservations(org_id) if r["task_id"] == task_id
            ]
            self.assertEqual(reservations[0]["status"], "uncertain", state)
            position = fresh.organization_budget_position(org_id)
            self.assertAlmostEqual(position["uncertain"], 0.02, msg=state)
            task = fresh.get_task(task_id)
            self.assertEqual(task["external_outcome_uncertain"], 1, state)

    def test_unknown_is_never_auto_retried_or_released(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(self.store, "DISPATCHED", reservation_id="res-1")
        before_rows = len(self.query("SELECT * FROM desktop_agent_call_attempts"))
        first = self.reconcile()
        second = self.reconcile()
        self.assertEqual(first["summary"]["unknown_external_outcome"], 1)
        self.assertEqual(second["summary"]["unknown_external_outcome"], 1)
        self.assertEqual(
            len(self.query("SELECT * FROM desktop_agent_call_attempts")), before_rows
        )
        fresh = self.reopen()
        self.assertEqual(fresh.list_reservations("org")[0]["status"], "uncertain")

    def test_committed_result_reconstructs_without_provider_call(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(
            self.store,
            "RESPONSE_COMMITTED",
            reservation_id="res-1",
            actual_cost_usd=0.01,
            response_payload="done:durable answer",
        )
        result = self.reconcile()
        self.assertEqual(result["summary"]["committed_recovered"], 1)
        fresh = self.reopen()
        results = reconstruct_committed_result(fresh, task_id="t1", task_attempt_number=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["response_payload"], "done:durable answer")
        self.assertEqual(results[0]["adapter_id"], "w")
        self.assertAlmostEqual(results[0]["actual_cost_usd"], 0.01)
        self.assertIsNotNone(results[0]["call_attempt_id"])
        task = fresh.get_task("t1")
        self.assertEqual(task["recovery_disposition"], TASK_RECOVERY_RECOVERED_COMMITTED)

    def test_committed_before_settlement_finishes_settlement_authoritatively(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(
            self.store,
            "RESPONSE_COMMITTED",
            reservation_id="res-1",
            actual_cost_usd=0.01,
            response_payload="done",
        )
        first = self.reconcile()
        self.assertEqual(first["summary"]["settlements_completed"], 1)
        fresh = self.reopen()
        self.assertEqual(fresh.list_reservations("org")[0]["status"], "settled")
        self.assertAlmostEqual(
            float(fresh.list_reservations("org")[0]["settled_actual_usd"]), 0.01
        )
        row = fresh.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        self.assertEqual(row["state"], "SETTLED")

        # Settlement retry is idempotent and never re-closes anything.
        second = self.reconcile()
        self.assertEqual(second["summary"]["settlements_completed"], 0)
        still = self.reopen().list_reservations("org")[0]
        self.assertEqual(still["status"], "settled")

    def test_mixed_committed_and_uncertain_attempt_keeps_exposure(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store, "vc", executor_kind="verification_cell")
        self.reserve(self.store, amount_usd=0.04, task_id="vc")
        self.journal(
            self.store, "RESPONSE_COMMITTED", task_id="vc", call_key="verifier:v1",
            actual_cost_usd=0.005, response_payload='{"verdict": "pass"}',
        )
        self.journal(
            self.store, "DISPATCHED", task_id="vc", call_key="verifier:v2",
        )
        result = self.reconcile()
        # Independent settlement: the committed verifier closes its cost even
        # though its uncertain sibling keeps the reservation UNCERTAIN.
        self.assertEqual(result["summary"]["settlements_completed"], 1)
        fresh = self.reopen()
        reservation = fresh.list_reservations("org")[0]
        self.assertEqual(reservation["status"], "uncertain")
        self.assertAlmostEqual(float(reservation["settled_actual_usd"]), 0.005)
        v1 = fresh.get_call_attempt("org", "vc", 1, "verifier:v1")
        self.assertEqual(v1["state"], "SETTLED")
        v2 = fresh.get_call_attempt("org", "vc", 1, "verifier:v2")
        self.assertEqual(v2["state"], "DISPATCHED")
        position = fresh.organization_budget_position("org")
        self.assertAlmostEqual(position["uncertain"], 0.04)
        task = fresh.get_task("vc")
        self.assertEqual(task["recovery_disposition"], TASK_RECOVERY_BLOCKED_UNCERTAIN)
        # The committed verifier result remains available for continuation.
        results = reconstruct_committed_result(fresh, task_id="vc", task_attempt_number=1)
        self.assertEqual([r["call_key"] for r in results], ["verifier:v1"])

    def test_settled_stays_settled_across_restarts(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(self.store, "SETTLED", reservation_id="res-1", actual_cost_usd=0.004)
        self.store.transition_reservation("res-1", "settled", settled_actual_usd=0.004)
        first = self.reconcile()
        second = self.reconcile()
        for result in (first, second):
            self.assertEqual(result["summary"]["integrity_errors"], 0)
            self.assertEqual(result["summary"]["settlements_completed"], 0)
        task = self.reopen().get_task("t1")
        self.assertEqual(task["recovery_disposition"], TASK_RECOVERY_TERMINAL)


class IntegrityReconciliationTests(ReconciliationFixtureBase):
    def reconcile(self) -> dict[str, Any]:
        return reconcile_agent_fabric_state(self.reopen())

    def test_settled_calls_with_active_reservation_complete_settlement(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(self.store, "SETTLED", actual_cost_usd=0.007)
        result = self.reconcile()
        self.assertEqual(result["summary"]["settlements_completed"], 1)
        self.assertEqual(result["summary"]["integrity_errors"], 0)
        self.assertEqual(self.reopen().list_reservations("org")[0]["status"], "settled")

    def test_multiple_active_reservations_detected(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store, reservation_id="res-a")
        self.store.create_reservation(
            reservation_id="res-b", organization_id="org", task_id="t1",
            attempt=1, amount_usd=0.05,
        )
        result = self.reconcile()
        kinds = [e["kind"] for e in result["integrity_errors"]]
        self.assertIn("multiple_active_reservations", kinds)

    def test_committed_response_without_payload_detected(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.journal(self.store, "RESPONSE_COMMITTED")
        result = self.reconcile()
        kinds = [e["kind"] for e in result["integrity_errors"]]
        self.assertIn("committed_without_payload", kinds)
        task = self.reopen().get_task("t1")
        self.assertEqual(task["recovery_disposition"], "RECOVERY_INTEGRITY_ERROR")

    def test_released_reservation_with_unresolved_dispatch_detected(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.store.transition_reservation("res-1", "released")
        self.journal(self.store, "DISPATCHED")
        result = self.reconcile()
        kinds = [e["kind"] for e in result["integrity_errors"]]
        self.assertIn("released_with_unresolved_dispatch", kinds)

    def test_budget_invariant_violation_detected(self) -> None:
        self.seed_org(self.store, budget_usd=0.01)
        self.seed_task(self.store)
        self.reserve(self.store, amount_usd=0.50)
        self.journal(self.store, "DISPATCH_INTENT_DURABLE", reservation_id="res-1")
        result = self.reconcile()
        kinds = [e["kind"] for e in result["integrity_errors"]]
        self.assertIn("budget_invariant_violation", kinds)


class IdempotencyAndRestartTests(ReconciliationFixtureBase):
    def test_double_reconciliation_is_identity_on_mixed_fixture(self) -> None:
        # A representative crash-mixed durable world.
        self.seed_org(self.store)
        self.seed_task(self.store, "ok")
        self.reserve(self.store, task_id="ok")
        self.journal(self.store, "SETTLED", task_id="ok", reservation_id="res-1",
                     actual_cost_usd=0.002)
        self.seed_task(self.store, "stuck")
        self.reserve(self.store, task_id="stuck", reservation_id="res-2", amount_usd=0.03)
        self.journal(self.store, "DISPATCHED", task_id="stuck", reservation_id="res-2")
        self.seed_task(self.store, "committed")
        self.reserve(self.store, task_id="committed", reservation_id="res-3")
        self.journal(self.store, "RESPONSE_COMMITTED", task_id="committed",
                     reservation_id="res-3", actual_cost_usd=0.01,
                     response_payload="done")

        before = self.snapshot()
        first = reconcile_agent_fabric_state(self.reopen())
        middle = self.snapshot()
        second = reconcile_agent_fabric_state(self.reopen())
        after = self.snapshot()
        third = reconcile_agent_fabric_state(self.reopen())
        final = self.snapshot()

        # reconcile(reconcile(state)) == reconcile(state): once the first
        # pass has converged derived state, further passes change nothing.
        self.assertEqual(after, final, "third pass must be a perfect no-op")
        self.assertEqual(third["summary"]["settlements_completed"], 0)
        self.assertEqual(second["summary"]["settlements_completed"], 0)
        self.assertEqual(second["summary"]["settlements_completed"], 0)
        self.assertEqual(second["summary"]["uncertain_exposures"],
                         first["summary"]["uncertain_exposures"],
                         "exposure transitions happen exactly once")
        self.assertNotEqual(before, middle, "first pass performs reconciliation work")
        self.assertEqual(second["summary"]["integrity_errors"], 0)

    def test_real_runtime_restart_reconciles_representative_database(self) -> None:
        # Build durable evidence through the REAL runtime, then restart.
        class Adapter:
            is_local = True
            is_conservative_bound = True
            cost_usd = 0.01

            def __init__(self) -> None:
                self.calls = 0

            async def generate(self, request):
                self.calls += 1
                usage = type("Usage", (), {"cost_usd": 0.01})()
                return type("Response", (), {"text": "done:restart", "usage": usage})()

        good = Adapter()

        class AmbiguousAdapter(Adapter):
            async def generate(self, request):
                self.calls += 1
                raise RuntimeError("ambiguous provider failure")

        bad = AmbiguousAdapter()
        scheduler = NanoScheduler(
            {"good": good, "bad": bad},
            budget_usd=1.0,
            max_attempts=1,
            recovery_store=self.store,
        )
        org = scheduler.schedule(
            [
                {"id": "done-task", "agent_id": "good",
                 "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "a"},
                {"id": "stuck-task", "agent_id": "bad",
                 "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "b"},
            ],
            organization_id="org-live", project_id="p", goal="g",
        )
        org = asyncio.run(scheduler.run(org))

        fresh = self.reopen()
        result = reconcile_agent_fabric_state(fresh)
        self.assertEqual(result["summary"]["integrity_errors"], 0)
        self.assertGreaterEqual(result["summary"]["unknown_external_outcome"], 1)
        done_task = fresh.get_task("done-task")
        self.assertEqual(done_task["recovery_disposition"], TASK_RECOVERY_TERMINAL)
        stuck_task = fresh.get_task("stuck-task")
        self.assertEqual(
            stuck_task["recovery_disposition"], TASK_RECOVERY_BLOCKED_UNCERTAIN
        )
        position = fresh.organization_budget_position("org-live")
        self.assertGreater(position["uncertain"], 0.0)
        self.assertGreaterEqual(good.calls + bad.calls, 2)

    def test_existing_completed_organizations_are_unaffected(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.reserve(self.store)
        self.journal(self.store, "SETTLED", reservation_id="res-1", actual_cost_usd=0.001)
        self.store.transition_reservation("res-1", "settled", settled_actual_usd=0.001)
        # First startup normalizes derived classifications...
        first = reconcile_agent_fabric_state(self.reopen())
        self.assertEqual(first["summary"]["integrity_errors"], 0)
        # ...every later restart is a no-op on unchanged durable state.
        before = self.snapshot()
        second = reconcile_agent_fabric_state(self.reopen())
        self.assertEqual(second["summary"]["integrity_errors"], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(second["summary"]["settlements_completed"], 0)

    def test_zero_provider_calls_by_construction(self) -> None:
        self.seed_org(self.store)
        self.seed_task(self.store)
        self.journal(self.store, "DISPATCHED")
        # Reconciliation is pure durable-state classification: its API takes
        # only a store (no adapters/providers) and returns counters.
        store_instance = self.reopen()
        result = reconcile_agent_fabric_state(store_instance)
        self.assertEqual(
            set(result), {"summary", "integrity_errors", "reconciled_at"}
        )
        self.assertIn("unknown_external_outcome", result["summary"])


class SummaryPersistenceTests(ReconciliationFixtureBase):
    def test_summary_line_is_bounded_and_truthful(self) -> None:
        path = self.root / "agent-reconciliation.jsonl"
        for index in range(250):
            append_reconciliation_summary(path, {"at": index, "organizations_scanned": 1})
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 200)
        payload = json.loads(lines[-1])
        self.assertEqual(payload["organizations_scanned"], 1)

        self.seed_org(self.store)
        self.seed_task(self.store)
        self.journal(self.store, "DISPATCHED")
        result = reconcile_agent_fabric_state(self.reopen())
        append_reconciliation_summary(path, {"barrier": "ready", **result["summary"]})
        last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["unknown_external_outcome"], 1)
        self.assertEqual(last["tasks_scanned"], 1)


class ServiceStartupIntegrationTests(unittest.TestCase):
    """A2.1C startup hook inside the REAL ServiceContext boot path."""

    def _build_context(self, database: Path):
        from amca.service import ServiceContext

        context = ServiceContext(
            root=database.parent,
            database=database,
            benchmarks=database.parent,
        )
        self.addCleanup(context.close)
        return context

    def test_startup_reconciles_then_opens_barrier(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "state.sqlite3"

        # A previous process left interrupted paid work behind.
        seed = AgentRecoveryStore(database)
        seed.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=2, max_attempts=2,
        )
        seed.create_task(
            task_id="stuck", organization_id="org", project_id="p",
            agent_id="w", focus="work", executor_kind="nano_local_model",
        )
        seed.record_call_state(
            organization_id="org", task_id="stuck", task_attempt_number=1,
            call_key="task:stuck:attempt:1", state="DISPATCHED",
            executor_kind="nano_local_model", adapter_id="w",
            reservation_id="", reserved_usd=0.01,
        )

        context = self._build_context(database)

        self.assertEqual(context.agent_fabric_barrier.state, "ready")
        self.assertEqual(context.agent_recovery_store.database, database.resolve())
        states = [entry[0] for entry in context.agent_fabric_barrier.transitions]
        self.assertEqual(states, ["reconciling", "ready"])
        self.assertGreaterEqual(
            context.agent_reconciliation["summary"]["unknown_external_outcome"], 1
        )
        fresh = AgentRecoveryStore(database)
        task = fresh.get_task("stuck")
        self.assertEqual(task["external_outcome_uncertain"], 1)

        summary_path = database.parent / "agent-reconciliation.jsonl"
        self.assertTrue(summary_path.exists())
        last = json.loads(summary_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["barrier"], "ready")
        self.assertIn("organizations_scanned", last["summary"])

    def test_canonical_database_only(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "state.sqlite3"
        self._build_context(database)
        created = {p.name for p in database.parent.iterdir()}
        recovery_dbs = [n for n in created if "recovery" in n.lower()]
        self.assertEqual(recovery_dbs, [], "must never create a competing DB")

    def test_integrity_failure_fails_scheduler_closed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "state.sqlite3"

        seed = AgentRecoveryStore(database)
        seed.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=2, max_attempts=2,
        )
        seed.create_task(
            task_id="corrupt", organization_id="org", project_id="p",
            agent_id="w", focus="work", executor_kind="nano_local_model",
        )
        seed.record_call_state(
            organization_id="org", task_id="corrupt", task_attempt_number=1,
            call_key="task:corrupt:attempt:1", state="RESPONSE_COMMITTED",
            executor_kind="nano_local_model", adapter_id="w",
            reservation_id="", response_payload=None,
        )

        context = self._build_context(database)

        self.assertEqual(context.agent_fabric_barrier.state, "closed")
        self.assertEqual(context.agent_fabric_barrier.reason, "RECOVERY_INTEGRITY_ERROR")
        with self.assertRaises(AgentFabricSchedulingBlocked):
            context.agent_fabric_barrier.ensure_schedulable()



if __name__ == "__main__":
    unittest.main()
