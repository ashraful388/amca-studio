"""M5.6.1 nano run-cancellation closeout tests.

Required coverage (manager work order
ZC-AMCA-V0.5.0-M5.6.1-NANO-RUN-CANCELLATION-CLOSEOUT-01):

1. run cancellation cancels multiple running children;
2. queued sibling never starts after cancellation;
3. existing adapter cancellation invoked for every applicable running child;
4. deterministic-tool child terminates/non-authoritative appropriately;
5. parent cancellation covers full descendant tree;
6. run cancellation covers multiple parents;
7. no new spawn after cancellation;
8. no retry after cancellation;
9. late result rejected;
10. completion-before-cancel remains truthful;
11. cancellation-before-completion wins;
12. simultaneous race resolves deterministically/safely;
13. both lifecycle ledgers agree;
14. gates remain OFF.

The closeout reuses the EXISTING scheduler (only the authorized surgical
cancellation surface was added to it) — no second engine, no redesign.
"""

import asyncio
import unittest

from amca.agent_fabric import AgentFabricSettings, ResourceEnvelope
from amca.agent_runtime import AgentTaskStatus, NanoScheduler
from amca.intelligence import (
    DEFAULT_GATES,
    AgentCompletionDisposition,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    NanoResultRecord,
    NanoSpawnProposal,
    Sensitivity,
)
from amca.models.base import (
    ModelAdapter,
    ModelCapabilities,
    ModelResponse,
    TokenUsage,
)

CREATED = "2026-08-27T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-m561",
        producer="m5.6.1-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def proposal(**overrides):
    fields = dict(
        meta=meta_factory(overrides.get("proposal_id", "prop-1")),
        proposal_id="prop-1",
        run_ref="run-1",
        parent_task_id="task:root",
        parent_depth=0,
        task_purpose="verify citation claims",
        task_type="verification",
        requested_child_count=1,
        capabilities=("citation_check",),
        permissions=("local_search",),
        expected_cost=0.0,
        correlation_id="corr-m561",
    )
    fields.update(overrides)
    return NanoSpawnProposal(**fields)


def registry(**kwargs):
    from amca.intelligence import NanoRunRegistry
    return NanoRunRegistry(
        meta_factory=meta_factory,
        run_ref="run-1",
        envelope=kwargs.pop("envelope", None),
        settings=kwargs.pop("settings", None),
        allowed_capabilities=kwargs.pop(
            "allowed_capabilities", ("citation_check", "summary")
        ),
        **kwargs,
    )


def nano_gates():
    return FeatureGateRegistry(nano_agents=True)


def spawn_one(reg, proposal_id="prop-1", parent_task_id="task:root",
              parent_depth=0, parent_permissions=("local_search",),
              capabilities=("citation_check",), permissions=("local_search",),
              task_purpose=None):
    fields = dict(
        proposal_id=proposal_id,
        parent_task_id=parent_task_id,
        parent_depth=parent_depth,
        capabilities=capabilities,
        permissions=permissions,
    )
    if task_purpose is not None:
        fields["task_purpose"] = task_purpose
    return reg.authorize_spawn(
        proposal(**fields),
        parent_permissions=parent_permissions,
        gates=nano_gates(),
    )


def make_scheduler(adapters=None, **kwargs):
    return NanoScheduler(
        dict(adapters or {}),
        budget_usd=kwargs.pop("budget_usd", 5.0),
        max_concurrent=kwargs.pop("max_concurrent", 4),
        max_attempts=kwargs.pop("max_attempts", 2),
        **kwargs,
    )


def make_coordinator(reg, scheduler, **kwargs):
    from amca.intelligence import NanoExecutionCoordinator
    return NanoExecutionCoordinator(
        registry=reg,
        scheduler=scheduler,
        meta_factory=meta_factory,
        known_parent_task_ids=kwargs.pop(
            "known_parent_task_ids", ("task:root",)
        ),
        **kwargs,
    )


class _SlowAdapter(ModelAdapter):
    """Real local adapter: counts generate calls and cancel signals."""

    def __init__(self, delay=5.0):
        super().__init__(
            adapter_id="slow-stub", provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )
        self._delay = delay
        self.generate_calls = 0
        self.cancel_invocations = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.generate_calls += 1
            await asyncio.sleep(self._delay)
            return ModelResponse(
                text="slow output",
                model="stub",
                provider="stub",
                usage=TokenUsage(),
                finish_reason="stop",
            )

    async def cancel(self, request_id=None):
        self.cancel_invocations += 1
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


def assert_ledgers_agree(case, registry_, organization_run, assignments):
    """Requirement 13: both lifecycle ledgers agree on terminal state."""

    for assignment in assignments:
        ledger = registry_.status(assignment.nano_id)
        record = organization_run.tasks.get(assignment.task_id)
        case.assertIsNotNone(record)
        case.assertEqual(
            record.status,
            ledger,
            f"ledger divergence for {assignment.nano_id}: "
            f"runtime={record.status!r} registry={ledger!r}",
        )
        case.assertIn(
            ledger,
            (
                AgentTaskStatus.COMPLETED,
                AgentTaskStatus.FAILED,
                AgentTaskStatus.CANCELLED,
            ),
        )


class RunCancellationTests(unittest.TestCase):
    def test_run_cancellation_cancels_multiple_running_children(self):
        reg = registry()
        children = [
            spawn_one(reg, proposal_id=f"p{index}") for index in range(1, 4)
        ]
        adapters = {c.nano_id: _SlowAdapter(delay=5.0) for c in children}
        scheduler = make_scheduler(adapters, max_concurrent=3, max_attempts=1)
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            runner = asyncio.create_task(
                coordinator.run(children, gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            cancelled = await coordinator.cancel_run()
            return cancelled, await runner

        cancelled, outcome = asyncio.run(scenario())
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(outcome["results"], {})
        for child in children:
            self.assertEqual(
                reg.status(child.nano_id), AgentTaskStatus.CANCELLED
            )
        assert_ledgers_agree(self, reg, outcome["organization_run"], children)

    def test_queued_sibling_never_starts_after_cancellation(self):
        reg = registry()
        runner_child = spawn_one(reg, proposal_id="a-runner")
        queued_child = spawn_one(reg, proposal_id="z-queued")
        runner_adapter = _SlowAdapter(delay=5.0)
        queued_adapter = _SlowAdapter(delay=0.0)
        scheduler = make_scheduler(
            {runner_child.nano_id: runner_adapter,
             queued_child.nano_id: queued_adapter},
            max_concurrent=1,
            max_attempts=1,
        )
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            task = asyncio.create_task(
                coordinator.run([runner_child, queued_child], gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            await coordinator.cancel_run()
            return await task

        outcome = asyncio.run(scenario())
        # the queued sibling was never dispatched (its adapter never ran)
        self.assertEqual(queued_adapter.generate_calls, 0)
        record = outcome["organization_run"].tasks[queued_child.task_id]
        self.assertEqual(record.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(
            reg.status(queued_child.nano_id), AgentTaskStatus.CANCELLED
        )
        self.assertNotIn(queued_child.nano_id, outcome["results"])

    def test_adapter_cancellation_invoked_for_every_running_child(self):
        reg = registry()
        first = spawn_one(reg, proposal_id="p1")
        second = spawn_one(reg, proposal_id="p2")
        adapters = {
            first.nano_id: _SlowAdapter(delay=5.0),
            second.nano_id: _SlowAdapter(delay=5.0),
        }
        scheduler = make_scheduler(adapters, max_concurrent=2, max_attempts=1)
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            task = asyncio.create_task(
                coordinator.run([first, second], gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            await coordinator.cancel_run()
            return await task

        outcome = asyncio.run(scenario())
        for child in (first, second):
            self.assertGreaterEqual(
                adapters[child.nano_id].cancel_invocations, 1
            )
        assert_ledgers_agree(self, reg, outcome["organization_run"], [first, second])

    def test_deterministic_child_terminated_non_authoritative(self):
        reg = registry()
        model_child = spawn_one(reg, proposal_id="a-modelchild")
        deterministic_child = spawn_one(
            reg, proposal_id="z-detchild",
            task_purpose="deterministic bounded step",
        )
        scheduler = make_scheduler(
            {model_child.nano_id: _SlowAdapter(delay=5.0)},
            max_concurrent=1,
            max_attempts=1,
        )
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            task = asyncio.create_task(
                coordinator.run(
                    [model_child, deterministic_child], gates=nano_gates()
                )
            )
            await asyncio.sleep(0.05)
            await coordinator.cancel_run()
            return await task

        outcome = asyncio.run(scenario())
        # the deterministic child stayed queued and was cancelled: it never
        # produced an authoritative result
        record = outcome["organization_run"].tasks[deterministic_child.task_id]
        self.assertEqual(record.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(
            reg.status(deterministic_child.nano_id), AgentTaskStatus.CANCELLED
        )
        self.assertNotIn(deterministic_child.nano_id, outcome["results"])

    def test_no_retry_after_cancellation(self):
        reg = registry()
        child = spawn_one(reg, proposal_id="retrychild")
        scheduler = make_scheduler(
            {child.nano_id: _SlowAdapter(delay=5.0)}, max_attempts=2
        )
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            task = asyncio.create_task(
                coordinator.run([child], gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            await coordinator.cancel(child.nano_id)
            return await task

        outcome = asyncio.run(scenario())
        record = outcome["organization_run"].tasks[child.task_id]
        # the cancelled execution never re-entered the retry queue
        self.assertEqual(record.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(record.attempts, 1)
        self.assertEqual(
            reg.status(child.nano_id), AgentTaskStatus.CANCELLED
        )

    def test_scheduler_cancellation_surface_basics(self):
        scheduler = make_scheduler()
        self.assertEqual(scheduler.active_task_ids(), ())
        self.assertFalse(asyncio.run(scheduler.cancel_active_execution("nope")))


class LineageCancellationTests(unittest.TestCase):
    def test_parent_cancellation_covers_full_descendant_tree(self):
        deep = ResourceEnvelope(max_manager_depth=3)
        reg = registry(
            envelope=deep,
            settings=AgentFabricSettings(envelope=deep, recursive_spawning=True),
        )
        parent = spawn_one(reg, proposal_id="parent")
        child = spawn_one(
            reg, proposal_id="child",
            parent_task_id=parent.task_id, parent_depth=1,
        )
        grandchild = spawn_one(
            reg, proposal_id="grandchild",
            parent_task_id=child.task_id, parent_depth=2,
        )
        coordinator = make_coordinator(
            reg, make_scheduler(),
            known_parent_task_ids=(
                "task:root", parent.task_id, child.task_id,
            ),
        )
        cancelled = asyncio.run(coordinator.cancel(parent.nano_id))
        self.assertIn(parent.nano_id, cancelled)
        self.assertIn(child.nano_id, cancelled)
        self.assertIn(grandchild.nano_id, cancelled)
        for member in (parent, child, grandchild):
            self.assertEqual(
                reg.status(member.nano_id), AgentTaskStatus.CANCELLED
            )

    def test_run_cancellation_covers_multiple_parents(self):
        reg = registry(settings=AgentFabricSettings(recursive_spawning=True))
        parent_a = spawn_one(reg, proposal_id="pa")
        child_a = spawn_one(
            reg, proposal_id="ca",
            parent_task_id=parent_a.task_id, parent_depth=1,
        )
        parent_b = spawn_one(reg, proposal_id="pb")
        child_b = spawn_one(
            reg, proposal_id="cb",
            parent_task_id=parent_b.task_id, parent_depth=1,
        )
        coordinator = make_coordinator(
            reg, make_scheduler(),
            known_parent_task_ids=("task:root", parent_a.task_id, parent_b.task_id),
        )
        cancelled = asyncio.run(coordinator.cancel_run())
        for member in (parent_a, child_a, parent_b, child_b):
            self.assertIn(member.nano_id, cancelled)
            self.assertEqual(
                reg.status(member.nano_id), AgentTaskStatus.CANCELLED
            )

    def test_no_new_spawn_after_cancellation(self):
        reg = registry()
        parent = spawn_one(reg, proposal_id="parent")
        coordinator = make_coordinator(reg, make_scheduler())
        asyncio.run(coordinator.cancel(parent.nano_id))
        with self.assertRaises(ContractError) as ctx:
            spawn_one(
                reg, proposal_id="late-child",
                parent_task_id=parent.task_id, parent_depth=1,
            )
        self.assertIn("parent_cancelled", ctx.exception.args[0])
        decision = reg.propose_spawn(
            proposal(
                proposal_id="late-proposal",
                parent_task_id=parent.task_id,
                parent_depth=1,
            ),
            parent_permissions=("local_search",),
            gates=nano_gates(),
        )
        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason_code, "parent_cancelled")

    def test_late_result_after_cancellation_rejected(self):
        reg = registry()
        child = spawn_one(reg, proposal_id="latechild")
        coordinator = make_coordinator(reg, make_scheduler())
        asyncio.run(coordinator.cancel(child.nano_id))
        late = NanoResultRecord(
            meta=meta_factory("late"),
            nano_id=child.nano_id,
            disposition=AgentCompletionDisposition.COMPLETE,
            summary="arrived after cancellation",
            output_digest="2" * 32,
            capabilities_used=(),
            evidence_refs=("task:late",),
        )
        with self.assertRaises(ContractError) as ctx:
            reg.record_result(late)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)


class CancellationRaceTests(unittest.TestCase):
    def test_completion_before_cancel_remains_truthful(self):
        reg = registry()
        child = spawn_one(reg, proposal_id="fast")
        scheduler = make_scheduler()
        coordinator = make_coordinator(reg, scheduler)
        outcome = coordinator.execute([child], gates=nano_gates())
        sealed = outcome["results"][child.nano_id]
        self.assertEqual(sealed.disposition, AgentCompletionDisposition.COMPLETE)
        # cancelling afterwards must NOT rewrite completed history
        cancelled = asyncio.run(coordinator.cancel(child.nano_id))
        self.assertEqual(cancelled, ())
        self.assertEqual(
            reg.status(child.nano_id), AgentTaskStatus.COMPLETED
        )
        self.assertIs(reg.result(child.nano_id), sealed)
        assert_ledgers_agree(self, reg, outcome["organization_run"], [child])

    def test_cancellation_before_completion_wins(self):
        reg = registry()
        child = spawn_one(reg, proposal_id="slowwin")
        scheduler = make_scheduler(
            {child.nano_id: _SlowAdapter(delay=5.0)}, max_attempts=1
        )
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            task = asyncio.create_task(
                coordinator.run([child], gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            await coordinator.cancel(child.nano_id)
            return await task

        outcome = asyncio.run(scenario())
        # the governed response was aborted before it could commit: the
        # result can never change the lineage back to success
        self.assertNotIn(child.nano_id, outcome["results"])
        self.assertEqual(
            reg.status(child.nano_id), AgentTaskStatus.CANCELLED
        )
        self.assertIsNone(reg.result(child.nano_id))
        assert_ledgers_agree(self, reg, outcome["organization_run"], [child])

    def test_simultaneous_race_resolves_deterministically(self):
        for cancel_delay, adapter_delay, expect_cancelled in (
            (0.0, 0.0, True),     # cancel before dispatch
            (0.005, 0.05, True),  # cancel while executing
            (0.10, 0.01, False),  # completion committed first
        ):
            with self.subTest(cancel_delay=cancel_delay):
                reg = registry()
                child = spawn_one(reg, proposal_id="race")
                scheduler = make_scheduler(
                    {child.nano_id: _SlowAdapter(delay=adapter_delay)},
                    max_attempts=1,
                )
                coordinator = make_coordinator(reg, scheduler)

                async def scenario():
                    task = asyncio.create_task(
                        coordinator.run([child], gates=nano_gates())
                    )
                    await asyncio.sleep(cancel_delay)
                    await coordinator.cancel(child.nano_id)
                    return await task

                outcome = asyncio.run(scenario())
                record = outcome["organization_run"].tasks[child.task_id]
                ledger = reg.status(child.nano_id)
                # the two ledgers always agree, whichever side won
                self.assertEqual(record.status, ledger)
                if expect_cancelled:
                    self.assertEqual(ledger, AgentTaskStatus.CANCELLED)
                    self.assertNotIn(child.nano_id, outcome["results"])
                else:
                    self.assertEqual(ledger, AgentTaskStatus.COMPLETED)
                    self.assertIn(child.nano_id, outcome["results"])


class GateTests(unittest.TestCase):
    def test_all_four_gates_remain_off(self):
        self.assertEqual(
            DEFAULT_GATES.to_dict(),
            {
                "adaptive_planner": False,
                "hybrid_execution": False,
                "nano_agents": False,
                "model_router": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
