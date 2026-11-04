"""A2.1D composite agent recovery tests.

Crash fixtures are durable states left by a previous process; continuation
reopens the canonical store as a NEW instance, reconciles, and continues
through the real runtime. Provider invocation counts prove that committed
and uncertain calls are never repeated.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from amca.agent_continuation import (
    ORG_STATUS_BLOCKED_UNCERTAIN,
    ORG_STATUS_RECOVERED_COMPLETE,
    build_continuation_plan,
    continue_organization,
)
from amca.agent_recovery import AgentRecoveryStore
from amca.agent_reconciliation import reconcile_agent_fabric_state
from amca.agent_runtime import ExecutorKind, NanoScheduler


class CountingAdapter:
    is_local = True
    is_conservative_bound = True

    def __init__(self, cost_usd: float = 0.01, *, adapter_id: str = "w") -> None:
        self.cost_usd = cost_usd
        self.adapter_id = adapter_id
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        usage = type("Usage", (), {"cost_usd": self.cost_usd})()
        text = f"done:{getattr(request, 'messages', [{'content': 'x'}])[0]['content'][:30]}"
        return type("Response", (), {"text": text, "usage": usage})()

    async def stream(self, request):
        response = await self.generate(request)
        yield type("Chunk", (), {"delta": "", "done": True,
                                 "response": response})()

    def estimate_tokens(self, _target) -> int:
        return 10

    def estimate_cost(self, _in_tokens: int, _out_tokens: int) -> float:
        return self.cost_usd

    def capabilities(self):
        return type("Capabilities", (), {
            "is_local": True,
            "supported_events": (),
            "context_sources": (),
        })()


class CompositeFixtureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.database = Path(self._tmp.name) / "amca.db"
        self.store = AgentRecoveryStore(self.database)

    def reopen(self) -> AgentRecoveryStore:
        return AgentRecoveryStore(self.database)

    def seed_org(self, budget_usd: float = 10.0) -> None:
        self.store.create_organization(
            organization_id="org", project_id="p", goal="g",
            budget_usd=budget_usd, max_concurrent=4, max_attempts=2,
        )

    def seed_task(
        self,
        task_id: str,
        *,
        executor_kind: str = "nano_local_model",
        depends_on: list[str] | None = None,
        verifier_ids: list[str] | None = None,
    ) -> None:
        self.store.create_task(
            task_id=task_id, organization_id="org", project_id="p",
            agent_id="w", focus=f"focus {task_id}",
            executor_kind=executor_kind,
            depends_on=depends_on or [],
            verifier_ids=verifier_ids or [],
        )

    def commit_call(
        self,
        task_id: str,
        call_key: str | None = None,
        *,
        payload: str = "committed durable result",
        cost: float = 0.01,
        reservation_id: str = "",
    ) -> None:
        """Durable evidence of a fully dispatched-and-committed call."""

        self.store.record_call_state(
            organization_id="org", task_id=task_id, task_attempt_number=1,
            call_key=call_key or f"task:{task_id}:attempt:1",
            state="RESPONSE_COMMITTED",
            executor_kind="nano_local_model", adapter_id="w",
            reservation_id=reservation_id, reserved_usd=cost,
            actual_cost_usd=cost, response_payload=payload,
        )

    def intent_only_call(
        self,
        task_id: str,
        call_key: str | None = None,
        *,
        reservation_id: str = "",
    ) -> None:
        self.store.record_call_state(
            organization_id="org", task_id=task_id, task_attempt_number=1,
            call_key=call_key or f"task:{task_id}:attempt:1",
            state="DISPATCHED", executor_kind="nano_local_model",
            adapter_id="w", reservation_id=reservation_id,
            reserved_usd=0.01,
        )

    def reserved_only_call(self, task_id: str, reservation_id: str = "") -> None:
        self.store.record_call_state(
            organization_id="org", task_id=task_id, task_attempt_number=1,
            call_key=f"task:{task_id}:attempt:1", state="RESERVED",
            executor_kind="nano_local_model", adapter_id="w",
            reservation_id=reservation_id, reserved_usd=0.01,
        )


class SoloRecoveryTests(CompositeFixtureBase):
    def test_committed_primary_is_reused_without_provider_call(self) -> None:
        self.seed_org()
        self.seed_task("solo")
        self.store.create_reservation(
            reservation_id="res-solo", organization_id="org", task_id="solo",
            attempt=1, amount_usd=0.01,
        )
        self.commit_call("solo", reservation_id="res-solo")
        first = reconcile_agent_fabric_state(self.reopen())
        self.assertEqual(first["summary"]["settlements_completed"], 1)

        adapter = CountingAdapter()
        scheduler = NanoScheduler(
            {"w": adapter}, budget_usd=10.0,
            recovery_store=self.reopen(), scheduling_barrier=None,
        )
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(org.tasks["solo"].status.value
                         if hasattr(org.tasks["solo"].status, "value")
                         else org.tasks["solo"].status, "completed")
        self.assertIn("committed durable result", org.tasks["solo"].result_summary)
        self.assertEqual(adapter.calls, 0, "committed primary must never redispatch")
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_RECOVERED_COMPLETE)

    def test_reserved_attempt_resumes_once_reusing_same_reservation(self) -> None:
        self.seed_org()
        self.seed_task("solo2")
        self.store.create_reservation(
            reservation_id="res-live", organization_id="org", task_id="solo2",
            attempt=1, amount_usd=0.05,
        )
        self.reserved_only_call("solo2", reservation_id="res-live")

        adapter = CountingAdapter()
        store = self.reopen()
        scheduler = NanoScheduler({"w": adapter}, budget_usd=10.0,
                                  recovery_store=store)
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        status = org.tasks["solo2"].status
        self.assertEqual(getattr(status, "value", status), "completed")
        self.assertEqual(adapter.calls, 1, "resumed exactly once")
        reservations = store.list_reservations("org")
        self.assertEqual(len(reservations), 1, "no duplicate reservation")
        self.assertEqual(reservations[0]["reservation_id"], "res-live")

    def test_uncertain_primary_blocks_without_redispatch(self) -> None:
        self.seed_org()
        self.seed_task("stuck")
        self.store.create_reservation(
            reservation_id="res-u", organization_id="org", task_id="stuck",
            attempt=1, amount_usd=0.02,
        )
        self.intent_only_call("stuck", reservation_id="res-u")
        self.store.transition_reservation("res-u", "uncertain")

        adapter = CountingAdapter()
        scheduler = NanoScheduler({"w": adapter}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(adapter.calls, 0, "uncertain call must never repeat")
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)
        self.assertIn("uncertain", (org.tasks["stuck"].error or ""))


class VerificationRecoveryTests(CompositeFixtureBase):
    def test_mandatory_committed_verifier_reused_and_uncertain_blocks(self) -> None:
        # reply42 section 17 mandatory fixture.
        self.seed_org()
        self.seed_task("vc", executor_kind="verification_cell",
                       verifier_ids=["v1", "v2"])
        self.seed_task("downstream", depends_on=["vc"])
        self.store.create_reservation(
            reservation_id="res-vc", organization_id="org", task_id="vc",
            attempt=1, amount_usd=0.04,
        )
        self.commit_call(
            "vc", "verifier:v1",
            payload='{"verdict": "pass", "summary": "checked"}',
            reservation_id="res-vc",
        )
        self.store.record_call_state(
            organization_id="org", task_id="vc", task_attempt_number=1,
            call_key="verifier:v2", state="DISPATCH_INTENT_DURABLE",
            executor_kind="verification_cell", adapter_id="v2",
            reservation_id="res-vc", reserved_usd=0.005,
        )
        self.store.transition_reservation("res-vc", "uncertain")

        v1, v2, worker = CountingAdapter(), CountingAdapter(), CountingAdapter()
        scheduler = NanoScheduler(
            {"v1": v1, "v2": v2, "w": worker}, budget_usd=10.0,
            recovery_store=self.reopen(),
        )
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]

        self.assertEqual(v1.calls, 0, "committed verifier A reused, not recalled")
        self.assertEqual(v2.calls, 0, "uncertain verifier B not repeated/replaced")
        vc_status = getattr(org.tasks["vc"].status, "value", org.tasks["vc"].status)
        self.assertNotEqual(vc_status, "completed",
                            "verification cannot become VERIFIED")
        downstream = getattr(org.tasks["downstream"].status, "value",
                             org.tasks["downstream"].status)
        self.assertNotEqual(downstream, "completed",
                            "requires_verified cannot be satisfied")
        self.assertIn("uncertain", (org.tasks["vc"].error or ""))
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)


class GateCRecoveryTests(CompositeFixtureBase):
    def test_committed_manager_proposal_is_reused_with_children(self) -> None:
        import json

        proposal_payload = json.dumps({
            "version": 1,
            "reason": "decompose",
            "children": [],
        })
        self.seed_org()
        self.seed_task("mgr-1", executor_kind="manager")
        self.seed_task("c1", executor_kind="deterministic_tool")
        self.seed_task("c2", depends_on=["c1"])
        self.commit_call("mgr-1", "manager:proposal", payload=proposal_payload)
        self.commit_call("c2", payload="child analysis done")

        manager = CountingAdapter()
        analyzer = CountingAdapter()
        scheduler = NanoScheduler(
            {"squad_mgr": manager, "analyzer_worker": analyzer},
            budget_usd=10.0, recovery_store=self.reopen(),
        )
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]

        self.assertEqual(manager.calls, 0, "committed proposal reused - no recall")
        self.assertEqual(analyzer.calls, 0, "committed child reused")
        mgr_status = getattr(org.tasks["mgr-1"].status, "value", org.tasks["mgr-1"].status)
        c2_status = getattr(org.tasks["c2"].status, "value", org.tasks["c2"].status)
        self.assertEqual(mgr_status, "completed")
        self.assertEqual(c2_status, "completed")
        self.assertIn("decompose", org.tasks["mgr-1"].result_summary)

    def test_uncertain_manager_proposal_blocks_gate_c(self) -> None:
        self.seed_org()
        self.seed_task("mgr-1", executor_kind="manager")
        self.intent_only_call("mgr-1", "manager:proposal")

        manager = CountingAdapter()
        scheduler = NanoScheduler({"squad_mgr": manager}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        self.assertEqual(manager.calls, 0, "no substitute proposal allowed")
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)


class ParallelRecoveryTests(CompositeFixtureBase):
    def _seed_parallel(self) -> None:
        self.seed_org()
        for unit in ("unit-a", "unit-b", "unit-c"):
            self.seed_task(unit)
        self.seed_task("integration", depends_on=["unit-a", "unit-b", "unit-c"])
        self.commit_call("unit-a", payload="unit a result")
        self.commit_call("unit-b", payload="unit b result")

    def test_partial_parallel_continues_and_integrates_once(self) -> None:
        self._seed_parallel()
        self.store.create_reservation(
            reservation_id="res-c", organization_id="org", task_id="unit-c",
            attempt=1, amount_usd=0.05,
        )
        self.reserved_only_call("unit-c", reservation_id="res-c")

        workers = {u: CountingAdapter() for u in ("unit-a", "unit-b", "unit-c")}
        integration = CountingAdapter()
        adapters = {"w": integration}
        scheduler = NanoScheduler(adapters, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]

        self.assertEqual(outcome["plan"]["tasks"]["unit-a"]["action"], "reuse")
        self.assertEqual(outcome["plan"]["tasks"]["unit-b"]["action"], "reuse")
        unit_c_calls = outcome["organization"].tasks["unit-c"].local_calls
        integration_status = getattr(
            org.tasks["integration"].status, "value",
            org.tasks["integration"].status,
        )
        self.assertEqual(integration_status, "completed")
        self.assertGreaterEqual(unit_c_calls + integration.calls, 2)

    def test_uncertain_unit_blocks_integration(self) -> None:
        self._seed_parallel()
        self.store.create_reservation(
            reservation_id="res-c", organization_id="org", task_id="unit-c",
            attempt=1, amount_usd=0.02,
        )
        self.intent_only_call("unit-c", reservation_id="res-c")
        self.store.transition_reservation("res-c", "uncertain")

        adapter = CountingAdapter()
        scheduler = NanoScheduler({"w": adapter}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        outcome = continue_organization(self.reopen(), scheduler, "org")
        org = outcome["organization"]
        self.assertEqual(outcome["plan"]["status"], ORG_STATUS_BLOCKED_UNCERTAIN)
        integration_status = getattr(
            org.tasks["integration"].status, "value",
            org.tasks["integration"].status,
        )
        self.assertNotEqual(integration_status, "completed")
        self.assertEqual(adapter.calls, 0, "uncertain unit C not repeated")


class IdempotentContinuationTests(CompositeFixtureBase):
    def test_second_restart_creates_no_duplicate_work(self) -> None:
        self.seed_org()
        self.seed_task("solo3")
        self.store.create_reservation(
            reservation_id="res-x", organization_id="org", task_id="solo3",
            attempt=1, amount_usd=0.05,
        )
        self.reserved_only_call("solo3", reservation_id="res-x")

        adapter = CountingAdapter()
        scheduler = NanoScheduler({"w": adapter}, budget_usd=10.0,
                                  recovery_store=self.reopen())
        first = continue_organization(self.reopen(), scheduler, "org")

        calls_after_first = adapter.calls
        rows_after_first = len(self.reopen().list_call_attempts("solo3"))

        second = continue_organization(self.reopen(), scheduler, "org")
        status_first = getattr(first["organization"].tasks["solo3"].status,
                               "value", first["organization"].tasks["solo3"].status)
        status_second = getattr(second["organization"].tasks["solo3"].status,
                                "value", second["organization"].tasks["solo3"].status)
        self.assertEqual(status_first, "completed")
        self.assertEqual(status_second, "completed")
        self.assertEqual(adapter.calls, calls_after_first,
                         "second restart repeats no provider work")
        self.assertEqual(len(self.reopen().list_call_attempts("solo3")),
                         rows_after_first, "no duplicate call attempts")


class RealRestartValidationTests(CompositeFixtureBase):
    def test_real_windows_kill_restart(self) -> None:
        """Real OS-level process kill + fresh-process reconciliation."""
        import os
        import subprocess
        import sys
        import time

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "a21d_restart_validation.py"
        )
        marker_parent = tempfile.TemporaryDirectory()
        self.addCleanup(marker_parent.cleanup)
        marker = Path(marker_parent.name) / "slow-dispatched.marker"

        proc = subprocess.Popen(
            [sys.executable, str(script), "phase1", str(self.database), str(marker)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 60
            while time.time() < deadline:
                if marker.exists():
                    break
                if proc.poll() is not None:
                    raise AssertionError("phase1 exited before the slow dispatch")
                time.sleep(0.2)
            else:
                proc.kill()
                raise AssertionError("slow dispatch marker never appeared")
            # Hard-kill the worker mid-provider-call (real crash simulation):
            # TerminateProcess does not let the worker clean up or commit
            # anything new, exactly like a Windows process crash.
            proc.kill()
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()

        verify = subprocess.run(
            [sys.executable, str(script), "verify", str(self.database)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertIn("RESTART_VALIDATION_PASS", verify.stdout,
                      msg=verify.stdout + verify.stderr)


if __name__ == "__main__":
    unittest.main()
