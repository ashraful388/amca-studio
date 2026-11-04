"""A3.2B native-batch runtime integration tests.

Covers transport-level behavior the planner tests cannot prove: durable
intents visible inside generate_batch, one transport for N logical calls,
per-item reservations/outcomes, partial success, batch-wide failure,
ZERO_COST truthfulness, and single-call fallback.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import ExecutorKind, NanoScheduler


def _usage(cost_usd: float) -> Any:
    return type("Usage", (), {"cost_usd": cost_usd,
                              "input_tokens": 11,
                              "output_tokens": 7})()


class BatchResponse:
    def __init__(self, text: str, cost_usd: float = 0.01) -> None:
        self.text = text
        self.usage = _usage(cost_usd)
        self.finish_reason = "stop"


class BatchAdapter:
    """Native-batch capable fake adapter with invocation accounting."""

    is_local = True
    is_conservative_bound = True
    supports_batch_generation = True

    def __init__(
        self,
        *,
        adapter_id: str = "batcher",
        model: str = "fake-model",
        provider: str = "fake-provider",
        cost_usd: float = 0.01,
        fail_batch: bool = False,
        fail_positions: tuple[int, ...] = (),
        probe=None,
    ) -> None:
        self.adapter_id = adapter_id
        self.model = model
        self.provider = provider
        self.cost_usd = cost_usd
        self.fail_batch = fail_batch
        self.fail_positions = fail_positions
        self.probe = probe
        self.generate_calls = 0
        self.batch_calls = 0
        self.batch_sizes = []

    def capabilities(self):
        return type("Capabilities", (), {"is_local": True})()

    def estimate_tokens(self, _target) -> int:
        return 10

    def estimate_cost(self, _in_tokens: int, _out_tokens: int) -> float:
        return self.cost_usd

    async def generate(self, request):
        self.generate_calls += 1
        return BatchResponse(f"single:{getattr(request, 'messages', [{'content': 'x'}])[0]['content'][:20]}",
                             cost_usd=self.cost_usd)

    async def stream(self, request):
        response = await self.generate(request)
        yield type("Chunk", (), {"delta": "", "done": True, "response": response})()

    async def generate_batch(self, requests):
        self.batch_calls += 1
        self.batch_sizes.append(len(requests))
        if self.probe is not None:
            self.probe()
        if self.fail_batch:
            raise RuntimeError("native batch transport failure")
        responses = []
        for position, request in enumerate(requests):
            if position in self.fail_positions:
                responses.append(None)
                continue
            content = request.messages[0]["content"][:20]
            responses.append(BatchResponse(f"batch:{content}",
                                           cost_usd=self.cost_usd))
        return responses


class PlainAdapter(BatchAdapter):
    supports_batch_generation = False

    def __init__(self, **kwargs):
        kwargs.setdefault("adapter_id", "plain")
        super().__init__(**kwargs)


class BatchingFixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database = Path(self._tmp.name) / "amca.db"
        self.store = AgentRecoveryStore(self.database)

    def reopen(self) -> AgentRecoveryStore:
        return AgentRecoveryStore(self.database)

    def query(self, sql: str, params: tuple = ()):
        import sqlite3

        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()


class NativeBatchRuntimeTests(BatchingFixtureBase):
    def _run_four(self, adapter: BatchAdapter):
        scheduler = NanoScheduler(
            {adapter.adapter_id: adapter}, budget_usd=10.0,
            max_concurrent=4, recovery_store=self.store,
        )
        graph = [
            {"id": f"task-{i}", "agent_id": adapter.adapter_id,
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
             "focus": f"work {i}"}
            for i in range(4)
        ]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))
        return scheduler, org

    def test_four_logical_calls_one_native_transport(self) -> None:
        adapter = BatchAdapter()
        scheduler, org = self._run_four(adapter)

        for i in range(4):
            status = getattr(org.tasks[f"task-{i}"].status, "value",
                             org.tasks[f"task-{i}"].status)
            self.assertEqual(status, "completed")
        # 4 logical tasks -> 4 inference items -> exactly 1 transport.
        self.assertEqual(adapter.batch_calls, 1)
        self.assertEqual(adapter.batch_sizes, [4])
        self.assertEqual(adapter.generate_calls, 0)
        report = scheduler.batch_reports[-1]
        self.assertEqual(report["batched_item_count"], 4)
        self.assertEqual(report["transport_plans"], 1)
        rows = self.query(
            "SELECT call_key FROM desktop_agent_call_attempts "
            "WHERE organization_id = 'org' AND executor_kind = 'nano_local_model'"
        )
        keys = {row["call_key"] for row in rows}
        self.assertEqual(len(keys), 4, "four independent durable identities")

    def test_probe_observes_durable_intents_inside_generate_batch(self) -> None:
        observed = {}

        def probe():
            rows = self.query(
                "SELECT call_key, state FROM desktop_agent_call_attempts "
                "WHERE organization_id = 'org'"
            )
            observed["states"] = {r["call_key"]: r["state"] for r in rows}

        adapter = BatchAdapter(probe=probe)
        self._run_four(adapter)
        self.assertIn("states", observed)
        states = set(observed["states"].values())
        self.assertNotIn("RESPONSE_COMMITTED", states,
                         "no response may exist before transport dispatch")
        self.assertTrue(all(
            state in ("DISPATCH_INTENT_DURABLE", "DISPATCHED")
            for state in states
        ))

    def test_per_item_reservations_and_costs_survive(self) -> None:
        adapter = BatchAdapter()
        _, org = self._run_four(adapter)
        reservations = self.query(
            "SELECT reservation_id, status, settled_actual_usd "
            "FROM desktop_agent_reservations WHERE organization_id = 'org'"
        )
        self.assertEqual(len(reservations), 4, "one per item, never collapsed")
        for row in reservations:
            self.assertEqual(row["status"], "settled")
            self.assertAlmostEqual(row["settled_actual_usd"], 0.01)
        total_cost = sum(t.cost_usd for t in org.tasks.values())
        self.assertAlmostEqual(total_cost, 0.04)

    def test_single_task_falls_back_to_single_call(self) -> None:
        adapter = BatchAdapter()
        scheduler = NanoScheduler({adapter.adapter_id: adapter},
                                  budget_usd=1.0, recovery_store=self.store)
        org = scheduler.schedule(
            [{"id": "only", "agent_id": adapter.adapter_id,
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "one"}],
            organization_id="org", project_id="p", goal="g",
        )
        asyncio.run(scheduler.run(org))
        self.assertEqual(adapter.generate_calls, 1)
        self.assertEqual(adapter.batch_calls, 0)
        status = getattr(org.tasks["only"].status, "value", org.tasks["only"].status)
        self.assertEqual(status, "completed")

    def test_non_batch_adapter_uses_ordinary_dispatch(self) -> None:
        plain = PlainAdapter()
        scheduler = NanoScheduler({plain.adapter_id: plain}, budget_usd=1.0,
                                  recovery_store=self.store)
        graph = [{"id": f"t{i}", "agent_id": "plain",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"w{i}"} for i in range(3)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        self.assertEqual(plain.batch_calls, 0)
        self.assertEqual(plain.generate_calls, 3)
        self.assertEqual(scheduler.batch_reports, [])

    def test_batch_wide_failure_leaves_all_items_uncertain(self) -> None:
        adapter = BatchAdapter(fail_batch=True)
        scheduler = NanoScheduler({adapter.adapter_id: adapter},
                                  budget_usd=10.0, max_concurrent=4,
                                  recovery_store=self.store)
        graph = [{"id": f"task-{i}", "agent_id": adapter.adapter_id,
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"work {i}"} for i in range(4)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        for i in range(4):
            status = getattr(org.tasks[f"task-{i}"].status, "value",
                             org.tasks[f"task-{i}"].status)
            self.assertNotEqual(status, "completed")
        uncertain = self.query(
            "SELECT COUNT(*) c FROM desktop_agent_call_attempts "
            "WHERE recovery_disposition = 'UNKNOWN_EXTERNAL_OUTCOME'"
        )[0]["c"]
        self.assertGreaterEqual(uncertain, 4)
        reservations = self.query(
            "SELECT DISTINCT status FROM desktop_agent_reservations"
        )
        self.assertEqual([r["status"] for r in reservations], ["uncertain"])

    def test_partial_outcomes_commit_independently(self) -> None:
        adapter = BatchAdapter(fail_positions=(2, 3))
        scheduler = NanoScheduler({adapter.adapter_id: adapter},
                                  budget_usd=10.0, max_concurrent=4,
                                  recovery_store=self.store)
        graph = [{"id": f"task-{i}", "agent_id": adapter.adapter_id,
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"work {i}"} for i in range(4)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        org = asyncio.run(scheduler.run(org))

        for i in range(2):
            status = getattr(org.tasks[f"task-{i}"].status, "value",
                             org.tasks[f"task-{i}"].status)
            self.assertEqual(status, "completed",
                             "authoritative members must commit independently")
        for i in (2, 3):
            status = getattr(org.tasks[f"task-{i}"].status, "value",
                             org.tasks[f"task-{i}"].status)
            self.assertNotEqual(status, "completed")
        settled = self.query(
            "SELECT COUNT(*) c FROM desktop_agent_call_attempts "
            "WHERE state = 'SETTLED'"
        )[0]["c"]
        uncertain = self.query(
            "SELECT COUNT(*) c FROM desktop_agent_call_attempts "
            "WHERE recovery_disposition = 'UNKNOWN_EXTERNAL_OUTCOME'"
        )[0]["c"]
        self.assertEqual(settled, 2)
        self.assertEqual(uncertain, 2)

    def test_zero_cost_batch_creates_no_paid_reservation(self) -> None:
        free = BatchAdapter(cost_usd=0.0)
        scheduler = NanoScheduler({"batcher": free}, budget_usd=10.0,
                                  max_concurrent=4, recovery_store=self.store)
        graph = [{"id": f"task-{i}", "agent_id": "batcher",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"work {i}"} for i in range(4)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        reservations = self.query(
            "SELECT * FROM desktop_agent_reservations "
            "WHERE organization_id = 'org'"
        )
        self.assertEqual(reservations, [],
                         "zero-cost batching must not fabricate reservations")
        self.assertEqual(free.batch_calls, 1)


if __name__ == "__main__":
    unittest.main()
