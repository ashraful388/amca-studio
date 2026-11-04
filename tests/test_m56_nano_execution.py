"""M5.6 Nano task execution integration tests.

Required coverage (manager work order
ZC-AMCA-V0.5.0-M5.6-NANO-TASK-EXECUTION-INTEGRATION-01): all 23 listed
scenarios. The execution path REUSES the existing production components —
``NanoScheduler`` (the existing bounded scheduler/executor),
``AgentTaskRecord``/``AgentTaskStatus`` (the existing audited lifecycle),
``AgentGovernor``/``ResourceEnvelope`` (the existing limit arithmetic) and
the M3 ``NanoRunRegistry`` governed front — with no second execution
engine. Adapter-backed scenarios run on REAL ``ModelAdapter`` subclasses so
the production cancel/cost-bound contracts are exercised exactly as
shipped.
"""

import asyncio
import dataclasses
import hashlib
import unittest
from unittest import mock

from amca.agent_fabric import AgentFabricSettings, ResourceEnvelope
from amca.agent_runtime import AgentTaskStatus, NanoScheduler
from amca.intelligence import (
    DEFAULT_GATES,
    AgentCompletionDisposition,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    IntelligenceRuntimeBridge,
    NanoAssignment,
    NanoEscalationRequest,
    NanoExecutionCoordinator,
    NanoResultRecord,
    NanoRunRegistry,
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
        correlation_id="corr-m56",
        producer="m5.6-test",
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
        correlation_id="corr-m56",
    )
    fields.update(overrides)
    return NanoSpawnProposal(**fields)


def registry(**kwargs):
    return NanoRunRegistry(
        meta_factory=meta_factory,
        run_ref="run-1",
        envelope=kwargs.pop("envelope", None),
        allowed_capabilities=kwargs.pop(
            "allowed_capabilities", ("citation_check", "summary")
        ),
        **kwargs,
    )


def nano_gates():
    return FeatureGateRegistry(nano_agents=True)


def spawn_one(reg, proposal_id="prop-1", parent_task_id="task:root",
              parent_depth=0, parent_permissions=("local_search",),
              capabilities=("citation_check",), permissions=("local_search",)):
    return reg.authorize_spawn(
        proposal(
            proposal_id=proposal_id,
            parent_task_id=parent_task_id,
            parent_depth=parent_depth,
            capabilities=capabilities,
            permissions=permissions,
        ),
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
    return NanoExecutionCoordinator(
        registry=reg,
        scheduler=scheduler,
        meta_factory=meta_factory,
        known_parent_task_ids=kwargs.pop(
            "known_parent_task_ids", ("task:root",)
        ),
        **kwargs,
    )


class _RealSleepAdapter(ModelAdapter):
    """Real adapter with the production cancel protocol and sleep."""

    def __init__(self, delay=5.0):
        super().__init__(
            adapter_id="sleep-stub", provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )
        self._delay = delay
        self.cancel_invocations = 0

    async def generate(self, request):
        async with self._call_scope(request):
            await asyncio.sleep(self._delay)
            return ModelResponse(
                text="nano local output",
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


class _FailingAdapter(ModelAdapter):
    def __init__(self):
        super().__init__(
            adapter_id="fail-stub", provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )

    async def generate(self, request):
        async with self._call_scope(request):
            raise RuntimeError("deterministic executor blew up")

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


class _ConcurrencyAdapter(ModelAdapter):
    active = 0
    max_active = 0

    def __init__(self, delay=0.05):
        super().__init__(
            adapter_id="conc-stub", provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )
        self._delay = delay

    async def generate(self, request):
        async with self._call_scope(request):
            type(self).active += 1
            type(self).max_active = max(
                type(self).max_active, type(self).active
            )
            try:
                await asyncio.sleep(self._delay)
            finally:
                type(self).active -= 1
            return ModelResponse(
                text="concurrent output",
                model="stub",
                provider="stub",
                usage=TokenUsage(),
                finish_reason="stop",
            )

    async def cancel(self, request_id=None):
        return await super().cancel(request_id)

    def stream(self, request):
        raise NotImplementedError


class ExecutionPathTests(unittest.TestCase):
    def test_authorized_nano_assignment_executes(self):
        reg = registry()
        assignment = spawn_one(reg)
        coordinator = make_coordinator(reg, make_scheduler())
        outcome = coordinator.execute([assignment], gates=nano_gates())
        record = outcome["organization_run"].tasks[assignment.task_id]
        self.assertEqual(record.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(
            record.result_summary, "deterministic:verify citation claims"
        )
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.COMPLETED)
        result = outcome["results"][assignment.nano_id]
        self.assertEqual(result.disposition, AgentCompletionDisposition.COMPLETE)
        # the reused deterministic-tool executor makes zero model calls
        self.assertEqual(record.local_calls, 0)
        self.assertEqual(record.api_calls, 0)

    def test_deterministic_bounded_task_validates_result(self):
        reg = registry()
        assignment = spawn_one(reg)
        coordinator = make_coordinator(reg, make_scheduler())
        outcome = coordinator.execute([assignment], gates=nano_gates())
        result = outcome["results"][assignment.nano_id]
        expected_digest = hashlib.sha256(
            "deterministic:verify citation claims".encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(result.output_digest, expected_digest)
        self.assertEqual(
            result.evidence_refs,
            (
                f"task:{assignment.task_id}",
                "executor:deterministic_tool",
            ),
        )
        self.assertEqual(reg.result(assignment.nano_id), result)
        self.assertEqual(result.capabilities_used, ())

    def test_authorized_assignment_via_existing_scheduler_batching_path(self):
        # the adapter-backed executor reuses the scheduler's existing
        # nano_local_model path (real ModelAdapter, zero cost classification)
        reg = registry()
        adapter = _RealSleepAdapter(delay=0.0)
        assignment = spawn_one(reg)
        scheduler = make_scheduler({assignment.nano_id: adapter})
        coordinator = make_coordinator(reg, scheduler)
        outcome = coordinator.execute([assignment], gates=nano_gates())
        record = outcome["organization_run"].tasks[assignment.task_id]
        self.assertEqual(record.status, AgentTaskStatus.COMPLETED)
        self.assertEqual(record.executor_kind, "nano_local_model")
        self.assertEqual(record.local_calls, 1)
        self.assertEqual(
            outcome["results"][assignment.nano_id].summary, "nano local output"
        )


class AuthorizationBoundaryTests(unittest.TestCase):
    def test_raw_unapproved_proposal_cannot_execute(self):
        reg = registry()
        raw = proposal(proposal_id="forged")
        forged = NanoAssignment(
            meta=meta_factory("forged-assign"),
            nano_id=f"nano:{raw.proposal_id}",
            task_id=f"task:{raw.proposal_id}",
            run_ref=raw.run_ref,
            parent_task_id=raw.parent_task_id,
            depth=raw.parent_depth + 1,
            purpose=raw.task_purpose,
            task_type=raw.task_type,
            capabilities=raw.capabilities,
            permissions=raw.permissions,
            expected_cost=0.0,
            correlation_id=raw.correlation_id,
            proposal_ref=raw.proposal_id,
            assignment_version=1,
        )
        coordinator = make_coordinator(reg, make_scheduler())
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([forged], gates=nano_gates())
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("no sealed assignment", ctx.exception.args[0])

    def test_orphan_assignment_rejected(self):
        reg = registry()
        assignment = spawn_one(reg, parent_task_id="task:unknown-parent")
        coordinator = make_coordinator(reg, make_scheduler())
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([assignment], gates=nano_gates())
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("orphan", ctx.exception.args[0])

    def test_privilege_widening_rejected_at_execution_boundary(self):
        reg = registry()
        assignment = spawn_one(reg)
        widened = dataclasses.replace(
            assignment, permissions=("local_search", "admin_write")
        )
        coordinator = make_coordinator(reg, make_scheduler())
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([widened], gates=nano_gates())
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("mutated", ctx.exception.args[0])

    def test_privilege_escalation_rejected_at_spawn(self):
        reg = registry()
        with self.assertRaises(ContractError) as ctx:
            spawn_one(
                reg,
                proposal_id="esc",
                permissions=("local_search", "admin_write"),
                parent_permissions=("local_search",),
            )
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("permission_escalation", ctx.exception.args[0])

    def test_capability_scope_violation_rejected(self):
        reg = registry()
        assignment = spawn_one(reg)
        forged_result = NanoResultRecord(
            meta=meta_factory("bad-result"),
            nano_id=assignment.nano_id,
            disposition=AgentCompletionDisposition.COMPLETE,
            summary="claims unassigned work",
            output_digest="0" * 32,
            capabilities_used=("remote_deploy",),
            evidence_refs=("task:x",),
        )
        with self.assertRaises(ContractError) as ctx:
            reg.record_result(forged_result)
        self.assertEqual(ctx.exception.code, ContractErrorCode.INVALID)
        self.assertIn("outside the assignment scope", ctx.exception.args[0])

    def test_escalation_grants_no_authority(self):
        reg = registry()
        assignment = spawn_one(reg)
        request = NanoEscalationRequest(
            meta=meta_factory("esc"),
            nano_id=assignment.nano_id,
            reason="needs more permissions",
            requested_authority="admin_write",
        )
        routed = reg.escalate(request)
        self.assertFalse(routed.granted)
        self.assertEqual(assignment.permissions, ("local_search",))
        with self.assertRaises(ContractError):
            reg.escalate(
                NanoEscalationRequest(
                    meta=meta_factory("esc2"),
                    nano_id=assignment.nano_id,
                    reason="self-granting",
                    requested_authority="root",
                    granted=True,
                )
            )


class FailureBehaviorTests(unittest.TestCase):
    def test_executor_failure_failed_disposition(self):
        reg = registry()
        assignment = spawn_one(reg)
        scheduler = make_scheduler(
            {assignment.nano_id: _FailingAdapter()}, max_attempts=1
        )
        coordinator = make_coordinator(reg, scheduler)
        outcome = coordinator.execute([assignment], gates=nano_gates())
        result = outcome["results"][assignment.nano_id]
        self.assertEqual(result.disposition, AgentCompletionDisposition.FAILED)
        self.assertIn("RuntimeError", result.summary)
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.FAILED)
        record = outcome["organization_run"].tasks[assignment.task_id]
        self.assertEqual(record.status, AgentTaskStatus.FAILED)

    def test_timeout_terminal_non_success(self):
        reg = registry()
        assignment = spawn_one(reg)
        scheduler = make_scheduler(
            {assignment.nano_id: _RealSleepAdapter(delay=5.0)}, max_attempts=1
        )
        coordinator = make_coordinator(
            reg, scheduler, run_timeout_seconds=0.2
        )
        outcome = coordinator.execute([assignment], gates=nano_gates())
        result = outcome["results"][assignment.nano_id]
        self.assertEqual(result.disposition, AgentCompletionDisposition.FAILED)
        self.assertIn("timeout", result.summary)
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.FAILED)

    def test_timeout_with_undispatched_task_cancels_truthfully(self):
        reg = registry()
        first = spawn_one(reg, proposal_id="p1")
        second = spawn_one(reg, proposal_id="p2")
        scheduler = make_scheduler(
            {
                first.nano_id: _RealSleepAdapter(delay=5.0),
                second.nano_id: _RealSleepAdapter(delay=5.0),
            },
            max_concurrent=1,
            max_attempts=1,
        )
        coordinator = make_coordinator(
            reg, scheduler, run_timeout_seconds=0.15
        )
        outcome = coordinator.execute(
            [first, second], gates=nano_gates()
        )
        self.assertEqual(
            outcome["results"][first.nano_id].disposition,
            AgentCompletionDisposition.FAILED,
        )
        # the second nano never started: it is cancelled, not failed
        self.assertNotIn(second.nano_id, outcome["results"])
        self.assertIn(second.nano_id, outcome["cancelled"])
        self.assertEqual(reg.status(second.nano_id), AgentTaskStatus.CANCELLED)


class CancellationTests(unittest.TestCase):
    def test_cancellation_before_dispatch(self):
        reg = registry()
        assignment = spawn_one(reg)
        scheduler = make_scheduler()
        coordinator = make_coordinator(reg, scheduler)
        with mock.patch.object(
            NanoScheduler, "schedule", autospec=True
        ) as spy:
            outcome = coordinator.execute(
                [assignment],
                gates=nano_gates(),
                cancel_check=lambda: True,
            )
        spy.assert_not_called()
        self.assertIsNone(outcome["organization_run"])
        self.assertIn(assignment.nano_id, outcome["cancelled"])
        self.assertEqual(outcome["results"], {})
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.CANCELLED)

    def test_cancellation_while_running(self):
        reg = registry()
        assignment = spawn_one(reg)
        adapter = _RealSleepAdapter(delay=5.0)
        scheduler = make_scheduler({assignment.nano_id: adapter}, max_attempts=1)
        coordinator = make_coordinator(reg, scheduler)

        async def scenario():
            runner = asyncio.create_task(
                coordinator.run([assignment], gates=nano_gates())
            )
            await asyncio.sleep(0.05)
            affected = await coordinator.cancel(assignment.nano_id)
            outcome = await runner
            return affected, outcome

        affected, outcome = asyncio.run(scenario())
        self.assertIn(assignment.nano_id, affected)
        # M5.6.1: governed cancellation no longer aborts the scheduler run;
        # the record and the ledger both reach CANCELLED and agree
        self.assertFalse(outcome["midflight_cancelled"])
        self.assertEqual(outcome["results"], {})
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.CANCELLED)
        record = outcome["organization_run"].tasks[assignment.task_id]
        self.assertEqual(record.status, AgentTaskStatus.CANCELLED)
        # the stop was signalled through the EXISTING adapter protocol
        self.assertGreaterEqual(adapter.cancel_invocations, 1)

    def test_late_result_after_cancellation_rejected(self):
        reg = registry()
        assignment = spawn_one(reg)
        coordinator = make_coordinator(reg, make_scheduler())
        outcome = coordinator.execute(
            [assignment], gates=nano_gates(), cancel_check=lambda: True,
        )
        self.assertEqual(outcome["results"], {})
        late = NanoResultRecord(
            meta=meta_factory("late"),
            nano_id=assignment.nano_id,
            disposition=AgentCompletionDisposition.COMPLETE,
            summary="finished anyway",
            output_digest="1" * 32,
            capabilities_used=(),
            evidence_refs=("task:late",),
        )
        with self.assertRaises(ContractError) as ctx:
            reg.record_result(late)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("late result", ctx.exception.args[0])

    def test_parent_cancellation_propagates(self):
        reg = registry(
            settings=AgentFabricSettings(recursive_spawning=True),
        )
        parent = spawn_one(reg, proposal_id="parent")
        child = spawn_one(
            reg,
            proposal_id="child",
            parent_task_id=parent.task_id,
            parent_depth=1,
        )
        affected = reg.cancel(parent.nano_id)
        self.assertIn(child.nano_id, affected)
        self.assertEqual(reg.status(parent.nano_id), AgentTaskStatus.CANCELLED)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
        coordinator = make_coordinator(
            reg,
            make_scheduler(),
            known_parent_task_ids=("task:root", parent.task_id),
        )
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([child], gates=nano_gates())
        self.assertIn("cancelled lineage", ctx.exception.args[0])

    def test_run_cancellation_propagates(self):
        reg = registry()
        first = spawn_one(reg, proposal_id="p1")
        second = spawn_one(reg, proposal_id="p2")
        reg.cancel_run()
        coordinator = make_coordinator(reg, make_scheduler())
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([first, second], gates=nano_gates())
        self.assertIn("cancelled lineage", ctx.exception.args[0])
        self.assertEqual(reg.status(first.nano_id), AgentTaskStatus.CANCELLED)
        self.assertEqual(reg.status(second.nano_id), AgentTaskStatus.CANCELLED)


class BoundTests(unittest.TestCase):
    def test_concurrency_limit_respected(self):
        _ConcurrencyAdapter.active = 0
        _ConcurrencyAdapter.max_active = 0
        reg = registry()
        first = spawn_one(reg, proposal_id="p1")
        second = spawn_one(reg, proposal_id="p2")
        shared = _ConcurrencyAdapter(delay=0.05)
        scheduler = make_scheduler(
            {first.nano_id: shared, second.nano_id: shared},
            max_concurrent=1,
        )
        coordinator = make_coordinator(reg, scheduler)
        outcome = coordinator.execute([first, second], gates=nano_gates())
        self.assertEqual(len(outcome["results"]), 2)
        self.assertEqual(_ConcurrencyAdapter.max_active, 1)

    def test_total_nano_run_limit_enforced(self):
        reg = registry(
            envelope=ResourceEnvelope(max_nano_tasks=1),
            max_children_per_parent=5,
        )
        spawn_one(reg, proposal_id="p1")
        with self.assertRaises(ContractError) as ctx:
            spawn_one(reg, proposal_id="p2")
        self.assertIn("run_agent_limit_exceeded", ctx.exception.args[0])

    def test_depth_limit_enforced(self):
        reg = registry(envelope=ResourceEnvelope(max_manager_depth=1))
        with self.assertRaises(ContractError) as ctx:
            spawn_one(reg, proposal_id="deep", parent_depth=1)
        self.assertIn("spawn_depth_exceeded", ctx.exception.args[0])


class LifecycleIntegrityTests(unittest.TestCase):
    def test_invalid_lifecycle_transition_rejected(self):
        reg = registry()
        assignment = spawn_one(reg)
        with self.assertRaises(ContractError):
            reg.transition(assignment.nano_id, AgentTaskStatus.RUNNING)
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.PROPOSED)

    def test_duplicate_task_execution_rejected(self):
        reg = registry()
        assignment = spawn_one(reg)
        coordinator = make_coordinator(reg, make_scheduler())
        first = coordinator.execute([assignment], gates=nano_gates())
        self.assertEqual(len(first["results"]), 1)
        with self.assertRaises(ContractError) as ctx:
            coordinator.execute([assignment], gates=nano_gates())
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertIn("duplicate", ctx.exception.args[0])

    def test_scheduler_rejects_duplicate_graph_ids(self):
        reg = registry()
        assignment = spawn_one(reg)
        scheduler = make_scheduler()
        with self.assertRaises(ValueError):
            scheduler.schedule(
                [
                    {"id": assignment.task_id, "executor_kind": "deterministic_tool",
                     "focus": "a"},
                    {"id": assignment.task_id, "executor_kind": "deterministic_tool",
                     "focus": "b"},
                ],
                organization_id="run-1",
                project_id="p",
                goal="g",
            )


class GateAndReadinessTests(unittest.TestCase):
    def test_gate_off_runtime_performs_zero_nano_execution(self):
        reg = registry()
        assignment = spawn_one(reg)
        coordinator = make_coordinator(reg, make_scheduler())
        with mock.patch.object(NanoScheduler, "schedule", autospec=True) as spy:
            with self.assertRaises(ContractError) as ctx:
                coordinator.execute([assignment], gates=DEFAULT_GATES)
        spy.assert_not_called()
        self.assertIn("nano_agents gate is disabled", ctx.exception.args[0])
        # the assignment stays untouched in its authorized-fresh state
        self.assertEqual(reg.status(assignment.nano_id), AgentTaskStatus.PROPOSED)

    def test_bridge_refuses_nano_execution_context_while_gate_off(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        with self.assertRaises(ContractError) as ctx:
            bridge.nano_execution_context(
                registry_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("registry factory must not be invoked")
                ),
                scheduler_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("scheduler factory must not be invoked")
                ),
            )
        self.assertIn("nano_agents gate is disabled", ctx.exception.args[0])

    def test_bridge_returns_coordinator_with_gate_on(self):
        bridge = IntelligenceRuntimeBridge(
            gates=FeatureGateRegistry(nano_agents=True),
            meta_factory=meta_factory,
        )
        reg = registry()
        scheduler = make_scheduler()
        coordinator = bridge.nano_execution_context(
            registry_factory=lambda: reg,
            scheduler_factory=lambda: scheduler,
        )
        self.assertIsInstance(coordinator, NanoExecutionCoordinator)
        self.assertIs(coordinator._registry, reg)
        self.assertIs(coordinator._scheduler, scheduler)

    def test_readiness_semantics_truthful(self):
        bridge = IntelligenceRuntimeBridge(meta_factory=meta_factory)
        status = bridge.status()
        nano = status["capabilities"]["nano_agents"]
        self.assertTrue(nano["implemented"])
        self.assertFalse(nano["enabled"])
        self.assertEqual(nano["runtime_ready"], "ready")
        validation = bridge.validate_request(["nano_agents"])
        decision = validation["decisions"]["nano_agents"]
        self.assertEqual(decision["decision"], "disabled_feature")
        self.assertEqual(decision["runtime_ready"], "ready")
        # the other gated capabilities stay explicitly unknown (no probing)
        for name in ("adaptive_planner", "hybrid_execution", "model_router"):
            self.assertEqual(
                status["capabilities"][name]["runtime_ready"], "unknown"
            )

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
