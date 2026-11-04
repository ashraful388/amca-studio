"""A2.1B transactional dispatch checkpoint tests.

Covers the required failure-injection boundaries, atomic financial/call
boundaries, stable call identities (including manager:proposal), and Gate C
non-break proof for Manager journaling.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from amca.agent_fabric import ResourceEnvelope
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    DeterministicManagerAdapter,
    ExecutorKind,
    NanoScheduler,
)


def _usage(cost_usd: float) -> Any:
    return type("Usage", (), {"cost_usd": cost_usd})()


class FakeResponse:
    def __init__(self, text: str = "nano contribution", cost_usd: float = 0.01):
        self.text = text
        self.usage = _usage(cost_usd)


class FakeAdapter:
    is_local = True
    is_conservative_bound = True

    def __init__(
        self,
        *,
        cost_usd: float = 0.01,
        fail_first: int = 0,
        on_generate: Any = None,
    ) -> None:
        self.cost_usd = cost_usd
        self.fail_first = fail_first
        self.on_generate = on_generate
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise RuntimeError("simulated ambiguous provider failure")
        if self.on_generate is not None:
            self.on_generate()
        return FakeResponse(f"done:{request.messages[0]['content'][:40]}", cost_usd=self.cost_usd)


VERIFIER_PASS = json.dumps({"verdict": "pass", "summary": "checked", "evidence_refs": ["e1"]})
VERIFIER_FAIL = json.dumps({"verdict": "fail", "summary": "wrong result", "evidence_refs": ["e2"]})


class VerifierAdapter:
    is_local = True
    is_conservative_bound = True

    def __init__(self, payload: str, cost_usd: float = 0.005) -> None:
        self.payload = payload
        self.cost_usd = cost_usd
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return FakeResponse(self.payload, cost_usd=self.cost_usd)


class CheckpointTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database = Path(self._tmp.name) / "amca.db"
        self.store = AgentRecoveryStore(self.database)

    def run_org(self, scheduler: NanoScheduler, graph: list[dict[str, Any]], org_id: str):
        org = scheduler.schedule(graph, organization_id=org_id, project_id="p", goal="g")
        return asyncio.run(scheduler.run(org))

    def ensure_baseline(self, org_id: str = "org", task_id: str = "t1") -> None:
        """Mirror the FK baseline rows the runtime guarantees before journaling."""
        self.store.create_organization(
            organization_id=org_id,
            project_id="p",
            goal="g",
            budget_usd=1.0,
            max_concurrent=2,
            max_attempts=2,
        )
        self.store.create_task(
            task_id=task_id,
            organization_id=org_id,
            project_id="p",
            agent_id="w",
            focus="work",
            executor_kind="nano_local_model",
        )

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()


class StoreAtomicityTests(CheckpointTestBase):
    """Atomic admission/settlement boundaries at the durable-state level."""

    def test_admission_rollback_creates_neither_reservation_nor_call(self) -> None:
        self.ensure_baseline()
        original = self.store.record_call_state

        def failing(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("forced admission failure")

        self.store.record_call_state = failing  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                with self.store.transaction() as tx:
                    tx.create_reservation(
                        reservation_id="res-x",
                        organization_id="org",
                        task_id="t1",
                        attempt=1,
                        amount_usd=0.5,
                    )
                    tx.record_call_state(
                        organization_id="org",
                        task_id="t1",
                        task_attempt_number=1,
                        call_key="task:t1:attempt:1",
                        state="RESERVED",
                        executor_kind="nano_local_model",
                        adapter_id="w",
                        reservation_id="res-x",
                    )
        finally:
            self.store.record_call_state = original  # type: ignore[method-assign]

        self.assertIsNone(self.store.get_reservation("res-x"))
        self.assertIsNone(
            self.store.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        )

    def test_settlement_rollback_does_not_half_settle(self) -> None:
        self.ensure_baseline()
        self.store.create_reservation(
            reservation_id="res-s",
            organization_id="org",
            task_id="t1",
            attempt=1,
            amount_usd=0.4,
        )
        self.store.record_call_state(
            organization_id="org",
            task_id="t1",
            task_attempt_number=1,
            call_key="task:t1:attempt:1",
            state="RESERVED",
            executor_kind="nano_local_model",
            adapter_id="w",
            reservation_id="res-s",
        )

        original = self.store.settle_call_attempts

        def failing(**kwargs: Any) -> int:
            raise RuntimeError("forced settlement failure")

        self.store.settle_call_attempts = failing  # type: ignore[method-assign]
        try:
            with self.assertRaises(RuntimeError):
                with self.store.transaction() as tx:
                    tx.transition_reservation("res-s", "settled", settled_actual_usd=0.1)
                    tx.settle_call_attempts(
                        organization_id="org", task_id="t1", task_attempt_number=1
                    )
        finally:
            self.store.settle_call_attempts = original  # type: ignore[method-assign]

        reservation = self.store.get_reservation("res-s")
        assert reservation is not None
        self.assertEqual(reservation["status"], "active")
        row = self.store.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "RESERVED")

    def test_no_double_settlement_and_idempotent_close(self) -> None:
        self.ensure_baseline()
        self.store.create_reservation(
            reservation_id="res-d",
            organization_id="org",
            task_id="t1",
            attempt=1,
            amount_usd=0.3,
        )
        self.store.transition_reservation("res-d", "settled", settled_actual_usd=0.1)
        with self.assertRaises(ValueError):
            self.store.transition_reservation("res-d", "settled", settled_actual_usd=0.1)

        self.store.record_call_state(
            organization_id="org",
            task_id="t1",
            task_attempt_number=1,
            call_key="task:t1:attempt:1",
            state="RESPONSE_COMMITTED",
            executor_kind="nano_local_model",
            adapter_id="w",
            reservation_id="res-d",
            actual_cost_usd=0.1,
        )
        first = self.store.settle_call_attempts(
            organization_id="org", task_id="t1", task_attempt_number=1
        )
        second = self.store.settle_call_attempts(
            organization_id="org", task_id="t1", task_attempt_number=1
        )
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_crash_after_reservation_is_safe_to_resume(self) -> None:
        self.ensure_baseline()
        self.store.create_reservation(
            reservation_id="res-c",
            organization_id="org",
            task_id="t1",
            attempt=1,
            amount_usd=0.2,
        )
        self.store.record_call_state(
            organization_id="org",
            task_id="t1",
            task_attempt_number=1,
            call_key="manager:proposal",
            state="RESERVED",
            executor_kind="manager",
            adapter_id="mgr",
            reservation_id="res-c",
        )
        row = self.store.get_call_attempt("org", "t1", 1, "manager:proposal")
        assert row is not None
        self.assertEqual(row["state"], "RESERVED")
        self.assertIsNone(row["dispatch_intent_at"])
        self.assertEqual(row["recovery_disposition"], "SAFE_TO_RESUME")
        self.assertEqual(self.store.organization_budget_position("org")["active"], 0.2)

    def test_dispatch_intent_without_response_stays_unknown(self) -> None:
        self.ensure_baseline()
        self.store.record_call_state(
            organization_id="org",
            task_id="t1",
            task_attempt_number=1,
            call_key="task:t1:attempt:1",
            state="DISPATCH_INTENT_DURABLE",
            executor_kind="nano_local_model",
            adapter_id="w",
            reservation_id="",
        )
        row = self.store.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["recovery_disposition"], "UNKNOWN_EXTERNAL_OUTCOME")
        self.assertEqual(len(self.store.uncertain_call_attempts("org")), 1)

        closed = self.store.settle_call_attempts(
            organization_id="org", task_id="t1", task_attempt_number=1
        )
        self.assertEqual(closed, 0)
        row = self.store.get_call_attempt("org", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "DISPATCH_INTENT_DURABLE")

        forced = self.store.settle_call_attempts(
            organization_id="org",
            task_id="t1",
            task_attempt_number=1,
            include_uncommitted=True,
        )
        self.assertEqual(forced, 1)


class RuntimeCheckpointTests(CheckpointTestBase):
    """Dispatch checkpoints wired into the real NanoScheduler execution path."""

    def test_paid_task_reaches_full_checkpoint_sequence(self) -> None:
        adapter = FakeAdapter()
        scheduler = NanoScheduler(
            {"w": adapter}, budget_usd=1.0, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
            "org-full",
        )
        self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.COMPLETED)

        row = self.store.get_call_attempt("org-full", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "SETTLED")
        for stamp in ("dispatch_intent_at", "dispatched_at", "response_received_at",
                      "response_committed_at", "settled_at"):
            self.assertIsNotNone(row[stamp])
        self.assertTrue(row["response_payload"].startswith("done:"))
        self.assertAlmostEqual(row["actual_cost_usd"], 0.01)
        self.assertAlmostEqual(row["reserved_usd"], 0.01)

        reservations = self.query(
            "SELECT * FROM desktop_agent_reservations WHERE organization_id = 'org-full'"
        )
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0]["status"], "settled")
        self.assertAlmostEqual(reservations[0]["settled_actual_usd"], 0.01)
        self.assertEqual(reservations[0]["reservation_id"], row["reservation_id"])

    def test_adapter_observes_durable_intent_before_invocation(self) -> None:
        observed: dict[str, Any] = {}

        def probe() -> None:
            row = self.store.get_call_attempt("org-intent", "t1", 1, "task:t1:attempt:1")
            observed["state"] = None if row is None else row["state"]
            observed["intent"] = None if row is None else row["dispatch_intent_at"]

        adapter = FakeAdapter(on_generate=probe)
        scheduler = NanoScheduler(
            {"w": adapter}, budget_usd=1.0, recovery_store=self.store
        )
        self.run_org(
            scheduler,
            [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
            "org-intent",
        )
        # At adapter invocation the journal already recorded the dispatch
        # intent (and its DISPATCHED stamp): intent was durable BEFORE the
        # call crossed into the provider.
        self.assertEqual(observed["state"], "DISPATCHED")
        self.assertIsNotNone(observed["intent"])

    def test_ambiguous_adapter_failure_keeps_uncertain_exposure(self) -> None:
        adapter = FakeAdapter(fail_first=1)
        scheduler = NanoScheduler(
            {"w": adapter}, budget_usd=1.0, max_attempts=1, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
            "org-ambig",
        )
        self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.FAILED)

        row = self.store.get_call_attempt("org-ambig", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "DISPATCHED")
        self.assertEqual(row["recovery_disposition"], "UNKNOWN_EXTERNAL_OUTCOME")
        self.assertIsNone(row["response_committed_at"])
        self.assertEqual(len(self.store.uncertain_call_attempts("org-ambig")), 1)

        reservations = self.query(
            "SELECT status FROM desktop_agent_reservations WHERE organization_id = 'org-ambig'"
        )
        self.assertEqual(reservations[0]["status"], "uncertain")
        position = self.store.organization_budget_position("org-ambig")
        self.assertAlmostEqual(position["uncertain"], 0.01)
        self.assertEqual(adapter.calls, 1)

    def test_response_commit_failure_leaves_no_committed_result(self) -> None:
        original = self.store.record_call_state

        def failing(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("state") == "RESPONSE_COMMITTED":
                raise RuntimeError("forced commit failure")
            return original(*args, **kwargs)

        self.store.record_call_state = failing  # type: ignore[method-assign]
        try:
            adapter = FakeAdapter()
            scheduler = NanoScheduler(
                {"w": adapter}, budget_usd=1.0, max_attempts=1, recovery_store=self.store
            )
            org = self.run_org(
                scheduler,
                [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
                "org-commitfail",
            )
            self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.FAILED)
        finally:
            self.store.record_call_state = original  # type: ignore[method-assign]

        row = self.store.get_call_attempt("org-commitfail", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "RESPONSE_RECEIVED")
        self.assertIsNone(row["response_committed_at"])
        self.assertIsNone(row["response_payload"])
        reservations = self.query(
            "SELECT status FROM desktop_agent_reservations WHERE organization_id = 'org-commitfail'"
        )
        self.assertEqual(reservations[0]["status"], "uncertain")

    def test_committed_response_survives_post_commit_failure(self) -> None:
        original_transition = AgentTaskRecord.transition

        def broken_transition(self: AgentTaskRecord, new_status: str):
            if new_status == AgentTaskStatus.VERIFYING:
                raise RuntimeError("simulated crash after response commit")
            return original_transition(self, new_status)

        AgentTaskRecord.transition = broken_transition  # type: ignore[method-assign]
        try:
            adapter = FakeAdapter()
            scheduler = NanoScheduler(
                {"w": adapter}, budget_usd=1.0, max_attempts=1, recovery_store=self.store
            )
            org = self.run_org(
                scheduler,
                [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
                "org-survive",
            )
            self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.FAILED)
        finally:
            AgentTaskRecord.transition = original_transition  # type: ignore[method-assign]

        row = self.store.get_call_attempt("org-survive", "t1", 1, "task:t1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "SETTLED")
        self.assertIsNotNone(row["response_committed_at"])
        self.assertTrue(row["response_payload"].startswith("done:"))
        reservations = self.query(
            "SELECT status, settled_actual_usd FROM desktop_agent_reservations "
            "WHERE organization_id = 'org-survive'"
        )
        self.assertEqual(reservations[0]["status"], "settled")
        self.assertAlmostEqual(reservations[0]["settled_actual_usd"], 0.01)
        self.assertEqual(adapter.calls, 1)

    def test_retry_scopes_stable_identity_per_attempt(self) -> None:
        adapter = FakeAdapter(fail_first=1)
        scheduler = NanoScheduler(
            {"w": adapter}, budget_usd=1.0, max_attempts=2, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [{"id": "t1", "agent_id": "w", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
            "org-retry",
        )
        self.assertEqual(org.tasks["t1"].status, AgentTaskStatus.COMPLETED)
        rows = self.store.list_call_attempts("t1")
        by_attempt = {row["task_attempt_number"]: row for row in rows}
        self.assertEqual(set(by_attempt), {1, 2})
        self.assertEqual(by_attempt[1]["call_key"], "task:t1:attempt:1")
        self.assertEqual(by_attempt[2]["call_key"], "task:t1:attempt:2")
        self.assertEqual(by_attempt[1]["state"], "DISPATCHED")
        self.assertEqual(by_attempt[2]["state"], "SETTLED")
        self.assertEqual(adapter.calls, 2)


class ManagerCheckpointTests(CheckpointTestBase):
    """Manager proposal journaling must not break Gate C semantics."""

    def manager_graph(self) -> list[dict[str, Any]]:
        return [
            {"id": "mgr-1", "agent_id": "squad_mgr", "executor_kind": ExecutorKind.MANAGER, "focus": "plan"},
        ]

    def test_gate_c_success_path_journals_manager_proposal_once(self) -> None:
        manager = DeterministicManagerAdapter(cost_usd=0.01)
        analyzer = FakeAdapter()
        scheduler = NanoScheduler(
            {"squad_mgr": manager, "analyzer_worker": analyzer},
            budget_usd=1.0,
            envelope=ResourceEnvelope(max_logical_agents=8, max_concurrent_agents=4),
            recovery_store=self.store,
        )
        org = self.run_org(scheduler, self.manager_graph(), "org-mgr-ok")

        self.assertEqual(org.tasks["mgr-1"].status, AgentTaskStatus.COMPLETED)
        self.assertIn("c2", org.tasks)
        self.assertEqual(org.tasks["c2"].status, AgentTaskStatus.COMPLETED)

        proposal = self.store.get_call_attempt("org-mgr-ok", "mgr-1", 1, "manager:proposal")
        assert proposal is not None
        self.assertEqual(proposal["state"], "SETTLED")
        self.assertEqual(proposal["executor_kind"], "manager")
        self.assertIn("children", proposal["response_payload"])
        self.assertAlmostEqual(proposal["actual_cost_usd"], 0.01)

        child_rows = self.store.list_call_attempts("c1")
        self.assertEqual(child_rows, [], "deterministic Governor/tool steps are not external calls")

        child2 = self.store.get_call_attempt("org-mgr-ok", "c2", 1, "task:c2:attempt:1")
        assert child2 is not None
        self.assertEqual(child2["state"], "SETTLED")
        self.assertEqual(manager.calls, 1, "no duplicate Manager dispatch")

    def test_manager_proposal_identity_stable_across_attempts(self) -> None:
        class FlakyManager(DeterministicManagerAdapter):
            async def generate(self, request):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient manager outage")
                return await super().generate(request)

        manager = FlakyManager(cost_usd=0.01)
        analyzer = FakeAdapter()
        scheduler = NanoScheduler(
            {"squad_mgr": manager, "analyzer_worker": analyzer},
            budget_usd=1.0,
            max_attempts=2,
            envelope=ResourceEnvelope(max_logical_agents=8, max_concurrent_agents=4),
            recovery_store=self.store,
        )
        org = self.run_org(scheduler, self.manager_graph(), "org-mgr-flaky")

        self.assertEqual(org.tasks["mgr-1"].status, AgentTaskStatus.COMPLETED)
        rows = self.store.list_call_attempts("mgr-1")
        keys = {(row["task_attempt_number"], row["call_key"]) for row in rows}
        self.assertEqual(keys, {(1, "manager:proposal"), (2, "manager:proposal")})
        by_attempt = {row["task_attempt_number"]: row for row in rows}
        self.assertEqual(by_attempt[1]["state"], "DISPATCHED")
        self.assertEqual(by_attempt[2]["state"], "SETTLED")


class VerificationAndBudgetTests(CheckpointTestBase):
    def test_verification_cell_rows_are_committed_and_settled(self) -> None:
        v1, v2 = VerifierAdapter(VERIFIER_PASS), VerifierAdapter(VERIFIER_PASS)
        scheduler = NanoScheduler(
            {"v1": v1, "v2": v2}, budget_usd=1.0, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [{
                "id": "vc", "agent_id": "v1", "executor_kind": ExecutorKind.VERIFICATION_CELL,
                "verifier_ids": ("v1", "v2"), "focus": "verify the claim",
            }],
            "org-vc-ok",
        )
        self.assertEqual(org.tasks["vc"].verification_result["outcome"], "verified")
        for verifier_id in ("v1", "v2"):
            row = self.store.get_call_attempt("org-vc-ok", "vc", 1, f"verifier:{verifier_id}")
            assert row is not None
            self.assertEqual(row["state"], "SETTLED")
            self.assertIn('"verdict": "pass"', row["response_payload"])
            self.assertAlmostEqual(row["actual_cost_usd"], 0.005)

    def test_verification_dispute_blocks_downstream_without_rows(self) -> None:
        v1, v2 = VerifierAdapter(VERIFIER_FAIL), VerifierAdapter(VERIFIER_PASS)
        worker = FakeAdapter()
        scheduler = NanoScheduler(
            {"v1": v1, "v2": v2, "w": worker}, budget_usd=1.0, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [
                {
                    "id": "vc", "agent_id": "v1", "executor_kind": ExecutorKind.VERIFICATION_CELL,
                    "verifier_ids": ("v1", "v2"), "focus": "verify the claim",
                },
                {
                    "id": "downstream", "agent_id": "w",
                    "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                    "focus": "use verified result", "depends_on": ["vc"],
                },
            ],
            "org-vc-block",
        )
        self.assertEqual(org.tasks["vc"].verification_result["outcome"], "disputed")
        self.assertEqual(org.tasks["downstream"].status, AgentTaskStatus.CANCELLED)
        self.assertEqual(self.store.list_call_attempts("downstream"), [])
        self.assertEqual(worker.calls, 0)

    def test_hard_budget_refusal_occurs_before_dispatch(self) -> None:
        w1, w2 = FakeAdapter(), FakeAdapter()
        scheduler = NanoScheduler(
            {"w1": w1, "w2": w2}, budget_usd=0.015, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [
                {"id": "paid-1", "agent_id": "w1", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "one"},
                {"id": "paid-2", "agent_id": "w2", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "two"},
            ],
            "org-budget",
        )
        self.assertEqual(org.tasks["paid-1"].status, AgentTaskStatus.COMPLETED)
        self.assertEqual(org.tasks["paid-2"].status, AgentTaskStatus.CANCELLED)
        self.assertEqual(w2.calls, 0)
        self.assertEqual(self.store.list_call_attempts("paid-2"), [])

    def test_zero_cost_execution_creates_no_paid_reservation(self) -> None:
        free_worker = FakeAdapter(cost_usd=0.0)
        scheduler = NanoScheduler(
            {"free": free_worker}, budget_usd=1.0, recovery_store=self.store
        )
        org = self.run_org(
            scheduler,
            [{"id": "z1", "agent_id": "free", "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "cheap"}],
            "org-free",
        )
        self.assertEqual(org.tasks["z1"].status, AgentTaskStatus.COMPLETED)
        reservations = self.query(
            "SELECT * FROM desktop_agent_reservations WHERE organization_id = 'org-free'"
        )
        self.assertEqual(reservations, [], "ZERO_COST work must not acquire a paid reservation")
        row = self.store.get_call_attempt("org-free", "z1", 1, "task:z1:attempt:1")
        assert row is not None
        self.assertEqual(row["state"], "SETTLED")
        self.assertAlmostEqual(row["reserved_usd"], 0.0)
        self.assertAlmostEqual(row["actual_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
