"""A3.3 Agent Economy telemetry tests.

EconomyRecords derive from authoritative amca.db tables, so restart and
reconciliation are idempotent by construction; these tests prove the
truthfulness rules (distinct counters, cost truth, token provenance,
exposure visibility) against real runtime execution where practical.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from amca.agent_economy import build_organization_economy
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_runtime import ExecutorKind, NanoScheduler

from test_agent_runtime_batching import BatchAdapter


def status(task) -> str:
    return getattr(task.status, "value", task.status)


class EconomyFixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database = Path(self._tmp.name) / "amca.db"
        self.store = AgentRecoveryStore(self.database)

    def reopen(self) -> AgentRecoveryStore:
        return AgentRecoveryStore(self.database)


class EconomyRuntimeTests(EconomyFixtureBase):
    def test_solo_inference_counts_are_distinct(self) -> None:
        adapter = BatchAdapter()  # batch-capable but a single task cannot batch
        scheduler = NanoScheduler({adapter.adapter_id: adapter}, budget_usd=1.0,
                                  recovery_store=self.store)
        org = scheduler.schedule(
            [{"id": "solo", "agent_id": adapter.adapter_id,
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "work"}],
            organization_id="org", project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        economy = build_organization_economy(self.reopen(), "org")
        self.assertEqual(economy["logical_task_count"], 1)
        self.assertEqual(economy["model_inference_count"], 1)
        self.assertEqual(economy["transport_request_count"], 1)

    def test_four_item_native_batch_keeps_counts_distinct(self) -> None:
        adapter = BatchAdapter()
        scheduler = NanoScheduler({adapter.adapter_id: adapter},
                                  budget_usd=10.0, max_concurrent=4,
                                  recovery_store=self.store)
        graph = [{"id": f"task-{i}", "agent_id": adapter.adapter_id,
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"work {i}"} for i in range(4)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        economy = build_organization_economy(self.reopen(), "org")
        self.assertEqual(economy["logical_task_count"], 4)
        self.assertEqual(economy["model_inference_count"], 4)
        self.assertEqual(economy["transport_request_count"], 1)
        self.assertEqual(economy["batch"]["batch_transport_count"], 1)
        ratio = economy["ratios"]["transport_reduction_ratio"]
        self.assertAlmostEqual(ratio, 0.75)

    def test_zero_cost_is_authoritative_zero_with_no_reservation(self) -> None:
        free = BatchAdapter(cost_usd=0.0)
        scheduler = NanoScheduler({"batcher": free}, budget_usd=1.0,
                                  recovery_store=self.store)
        graph = [{"id": f"task-{i}", "agent_id": "batcher",
                  "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
                  "focus": f"work {i}"} for i in range(2)]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        economy = build_organization_economy(self.reopen(), "org")
        self.assertAlmostEqual(economy["cost"]["actual_cost_usd"], 0.0)
        self.assertAlmostEqual(economy["cost"]["reserved_cost_usd"], 0.0)
        self.assertIsNotNone(economy["model_inference_count"])

    def test_tokens_carry_provider_reported_provenance(self) -> None:
        adapter = BatchAdapter()
        scheduler = NanoScheduler({adapter.adapter_id: adapter},
                                  budget_usd=1.0, recovery_store=self.store)
        org = scheduler.schedule(
            [{"id": "t", "agent_id": adapter.adapter_id,
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "w"}],
            organization_id="org", project_id="p", goal="g")
        asyncio.run(scheduler.run(org))
        economy = build_organization_economy(self.reopen(), "org")
        self.assertIn("provider_reported", economy["tokens"]["sources"])

    def test_restart_and_recovery_do_not_double_count(self) -> None:
        from test_agent_composite_recovery import CountingAdapter

        class Ambiguous(CountingAdapter):
            async def generate(self, request):
                self.calls += 1
                raise RuntimeError("ambiguous provider failure")

        good, stuck = CountingAdapter(adapter_id="good"), Ambiguous(adapter_id="bad")
        scheduler = NanoScheduler(
            {"good": good, "bad": stuck}, budget_usd=1.0,
            max_attempts=1, recovery_store=self.store,
        )
        graph = [
            {"id": "done-task", "agent_id": "good",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "a"},
            {"id": "stuck-task", "agent_id": "bad",
             "executor_kind": ExecutorKind.NANO_LOCAL_MODEL, "focus": "b"},
        ]
        org = scheduler.schedule(graph, organization_id="org",
                                 project_id="p", goal="g")
        asyncio.run(scheduler.run(org))

        from amca.agent_reconciliation import reconcile_agent_fabric_state
        first = build_organization_economy(self.reopen(), "org")
        reconcile_agent_fabric_state(self.reopen())
        second = build_organization_economy(self.reopen(), "org")

        self.assertEqual(first["model_inference_count"],
                         second["model_inference_count"])
        self.assertGreater(second["cost"]["uncertain_exposure_usd"], 0.0,
                           "uncertain exposure must stay visible after restart")
        self.assertGreaterEqual(second["cost"]["settled_cost_usd"], 0.0)
        # Reused/reconciled evidence adds no new inference items.
        self.assertEqual(first["transport_request_count"],
                         second["transport_request_count"])

class VerificationEconomyTests(EconomyFixtureBase):
    def test_gate_c_aggregation_separates_verifier_work(self) -> None:
        self.store.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=2, max_attempts=2,
        )
        self.store.create_task(task_id="solo", organization_id="org",
                               project_id="p", agent_id="w", focus="plain",
                               executor_kind="nano_local_model")
        self.store.record_call_state(
            organization_id="org", task_id="solo", task_attempt_number=1,
            call_key="task:solo:attempt:1", state="RESPONSE_COMMITTED",
            executor_kind="nano_local_model", adapter_id="w",
            reservation_id="", actual_cost_usd=0.01,
            response_payload="done", input_tokens=7, output_tokens=3,
            token_source="provider_reported")
        self.store.create_task(task_id="vc", organization_id="org",
                               project_id="p", agent_id="v", focus="verify",
                               executor_kind="verification_cell")
        for verifier in ("va", "vb"):
            self.store.record_call_state(
                organization_id="org", task_id="vc", task_attempt_number=1,
                call_key=f"verifier:{verifier}", state="RESPONSE_COMMITTED",
                executor_kind="verification_cell", adapter_id=verifier,
                reservation_id="", actual_cost_usd=0.004,
                response_payload='{"verdict": "pass"}')

        economy = build_organization_economy(self.reopen(), "org")
        self.assertAlmostEqual(economy["verification"]["actual_cost_usd"], 0.008)
        # Verifier spend is visible separately from the generic task total.
        self.assertAlmostEqual(economy["cost"]["actual_cost_usd"], 0.018)
        self.assertEqual(economy["verification"]["inference_count"], 2)


class RatioGuardTests(EconomyFixtureBase):
    def test_divide_by_zero_ratio_is_safe_and_roi_stays_absent(self) -> None:
        self.seed_org = None  # unused; keep fixture minimal
        self.store.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=1, max_attempts=2,
        )
        economy = build_organization_economy(self.reopen(), "org")
        self.assertIsNone(economy["ratios"]["transport_reduction_ratio"],
                          "no inferences => ratio must be null, not zero-divide")
        self.assertNotIn("roi_score", economy)
        self.assertIsNone(economy["roi"])


if __name__ == "__main__":
    unittest.main()


class ReuseCategoryTests(EconomyFixtureBase):
    def test_optimization_reuse_hits_and_recovery_stay_separate(self) -> None:
        from test_agent_runtime import (  # type: ignore[attr-defined]
            FakeAdapter,
        )
        from amca.agent_runtime import (
            AgentTaskRecord, DuplicateResultReuse,
        )

        reuse = DuplicateResultReuse()
        digest_source = AgentTaskRecord(
            id="seed", organization_id="o", project_id="p",
            agent_id="w", executor_kind=ExecutorKind.NANO_LOCAL_MODEL,
            focus="identical focus",
        )
        adapters = {"w": FakeAdapter()}
        reuse.register(
            digest_source.execution_identity("v2", adapters=adapters),
            "cached verified summary", version="v2")
        scheduler = NanoScheduler(adapters, budget_usd=5.0,
                                  reuse=reuse, recovery_store=self.store)
        # A pure schedule-time cache hit never reaches dispatch, so the
        # organization baseline must exist for the economy projection.
        self.store.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=5.0, max_concurrent=4, max_attempts=2)
        self.store.create_task(task_id="dup", organization_id="org",
                               project_id="p", agent_id="w",
                               focus="identical focus",
                               executor_kind="nano_local_model")
        org = scheduler.schedule(
            [{"id": "dup", "agent_id": "w",
              "executor_kind": ExecutorKind.NANO_LOCAL_MODEL,
              "focus": "identical focus"}],
            organization_id="org", project_id="p", goal="g")
        asyncio.run(scheduler.run(org))

        # Optimization hit: zero inference / zero transport / logical kept.
        self.assertEqual(scheduler.optimization_reuse_hits, 1)
        economy = build_organization_economy(
            self.reopen(), "org",
            optimization_reuse_hits=scheduler.optimization_reuse_hits,
            recovery_reuse_count=0)
        self.assertEqual(economy["optimization_reuse_hit_count"], 1)
        self.assertEqual(economy["logical_task_count"], 1)
        self.assertEqual(economy["model_inference_count"], 0)
        self.assertEqual(economy["transport_request_count"], 0)

    def test_architecture_aggregation_uses_named_identity(self) -> None:
        self.store.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=1.0, max_concurrent=2, max_attempts=2,
        )
        self.store.create_task(task_id="j", organization_id="org",
                               project_id="p", agent_id="w", focus="x",
                               executor_kind="collaboration",
                               participant_ids=["w"],
                               collaboration_architecture="joint")
        self.store.record_call_state(
            organization_id="org", task_id="j", task_attempt_number=1,
            call_key="joint:phase:1:round:1:participant:w",
            state="RESPONSE_COMMITTED", executor_kind="collaboration",
            adapter_id="w", reservation_id="", actual_cost_usd=0.02,
            response_payload="{}")
        economy = build_organization_economy(self.reopen(), "org")
        arch = economy["architectures"]["joint"]
        self.assertEqual(arch["logical_tasks"], 1)
        self.assertEqual(arch["model_inference_count"], 1)
        self.assertAlmostEqual(arch["actual_cost_usd"], 0.02)
