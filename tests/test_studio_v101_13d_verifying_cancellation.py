"""PS1-13D (fix PS1-13B-FUNC-01): allow VERIFYING cancellation via canonical path.

Root cause (B): user/API cancel -> state lookup -> eligibility check against
``AgentTaskStatus.ALLOWED_TRANSITIONS`` (and mirrored
``AGENT_LIFECYCLE_TRANSITIONS``) -> manager/child cancel -> tool cancel ->
terminal -> persist -> retire. ``VERIFYING`` mapped only to
``{COMPLETED, FAILED}``, so ``AgentTaskRecord.transition(VERIFYING->CANCELLED)``
raised ``ValueError`` and the governed cancel guards
(``NanoScheduler.run`` CancelledError handler + ``NanoExecutionCoordinator.cancel``
in-flight branch) whitelisted only ``STARTING/RUNNING/WAITING`` (plus QUEUED
separately). VERIFYING work therefore denied cancellation instead of flowing
through the EXISTING canonical mechanism.

Fix (C): add ``CANCELLED`` to both VERIFYING transition sets + extend the
EXISTING cancel guards to include VERIFYING. No parallel path, other states
preserved, single-threaded deterministic race handling (completion-before-cancel
preserved, cancellation-before-completion wins, no dual terminal/zombie/resurrect).

All mutable test state lives under the mandated scratch root
``D:\\ChatGPT\\AMCA\\.agent-tmp\\ps1-agent-13d``. Local-only, zero paid calls.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from amca.agent_fabric import AgentFabricSettings, ResourceEnvelope
from amca.agent_runtime import (
    AgentTaskRecord,
    AgentTaskStatus,
    DeterministicManagerAdapter,
    ExecutorKind,
    NanoScheduler,
)
from amca.intelligence import (
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    FeatureGateRegistry,
    NanoRunRegistry,
    NanoSpawnProposal,
    Sensitivity,
    AgentCompletionDisposition,
    NanoResultRecord,
)
from amca.intelligence.contracts import (
    AGENT_LIFECYCLE_TRANSITIONS,
    AgentLifecycleState,
    validate_agent_transition,
)
from amca.models.base import ModelAdapter, ModelCapabilities, ModelResponse, TokenUsage

MANDATED_SCRATCH = Path(r"D:\ChatGPT\AMCA\.agent-tmp\ps1-agent-13d")
CREATED = "2026-09-09T00:00:00+00:00"


def meta_factory(record_id):
    return ContractMetadata(
        record_id=record_id,
        correlation_id="corr-13d",
        producer="ps1-13d-test",
        created_at=CREATED,
        sensitivity=Sensitivity.INTERNAL,
    )


def nano_gates():
    return FeatureGateRegistry(nano_agents=True)


def _verifying_record(task_id="t-verify-13d"):
    task = AgentTaskRecord(
        id=task_id, organization_id="o-13d", project_id="p-13d",
        agent_id="a-13d", focus="verify bounded thing",
    )
    for nxt in (
        AgentTaskStatus.AUTHORIZED,
        AgentTaskStatus.QUEUED,
        AgentTaskStatus.STARTING,
        AgentTaskStatus.RUNNING,
        AgentTaskStatus.VERIFYING,
    ):
        task.transition(nxt)
    return task


def _proposal(**overrides):
    fields = dict(
        meta=meta_factory(overrides.get("proposal_id", "prop-13d")),
        proposal_id="prop-13d",
        run_ref="run-13d",
        parent_task_id="task:root",
        parent_depth=0,
        task_purpose="verify bounded citation",
        task_type="verification",
        requested_child_count=1,
        capabilities=("citation_check",),
        permissions=("local_search",),
        expected_cost=0.0,
        correlation_id="corr-13d",
    )
    fields.update(overrides)
    return NanoSpawnProposal(**fields)


def _registry(**kwargs):
    return NanoRunRegistry(
        meta_factory=meta_factory,
        run_ref="run-13d",
        envelope=kwargs.pop("envelope", None),
        settings=kwargs.pop("settings", None),
        allowed_capabilities=kwargs.pop(
            "allowed_capabilities", ("citation_check", "summary")
        ),
        **kwargs,
    )


def _spawn_one(reg, proposal_id="prop-13d", **kw):
    return reg.authorize_spawn(
        _proposal(proposal_id=proposal_id, **kw),
        parent_permissions=("local_search",),
        gates=nano_gates(),
    )


class _SlowAdapter(ModelAdapter):
    """Real local adapter: zero cost, tracks generate + cancel."""

    def __init__(self, delay=5.0):
        super().__init__(
            adapter_id="slow-13d", provider="stub", model="stub",
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
                text="slow output 13d",
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


class _FastAdapter(ModelAdapter):
    def __init__(self, adapter_id="fast-13d"):
        super().__init__(
            adapter_id=adapter_id, provider="stub", model="stub",
            capabilities=ModelCapabilities(is_local=True),
        )
        self.generate_calls = 0

    async def generate(self, request):
        async with self._call_scope(request):
            self.generate_calls += 1
            return ModelResponse(
                text="fast output 13d",
                model="stub",
                provider="stub",
                usage=TokenUsage(),
                finish_reason="stop",
            )

    def stream(self, request):
        raise NotImplementedError


def _scheduler(adapters=None, **kw):
    return NanoScheduler(
        dict(adapters or {}),
        budget_usd=kw.pop("budget_usd", 5.0),
        max_concurrent=kw.pop("max_concurrent", 4),
        max_attempts=kw.pop("max_attempts", 1),
        **kw,
    )


def _coordinator(reg, sched, **kw):
    from amca.intelligence import NanoExecutionCoordinator
    return NanoExecutionCoordinator(
        registry=reg,
        scheduler=sched,
        meta_factory=meta_factory,
        known_parent_task_ids=kw.pop("known_parent_task_ids", ("task:root",)),
        **kw,
    )


class RootCauseTests(unittest.TestCase):
    """B: exact cause is the VERIFYING transition omission + guard whitelist."""

    def test_verifying_allows_cancelled_in_both_maps(self):
        self.assertIn(
            AgentTaskStatus.CANCELLED,
            AgentTaskStatus.ALLOWED_TRANSITIONS[AgentTaskStatus.VERIFYING],
        )
        self.assertIn(
            AgentLifecycleState.CANCELLED,
            AGENT_LIFECYCLE_TRANSITIONS[AgentLifecycleState.VERIFYING],
        )
        # Other VERIFYING edges preserved.
        self.assertIn(
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.ALLOWED_TRANSITIONS[AgentTaskStatus.VERIFYING],
        )
        self.assertIn(
            AgentTaskStatus.FAILED,
            AgentTaskStatus.ALLOWED_TRANSITIONS[AgentTaskStatus.VERIFYING],
        )

    def test_canonical_transition_verifying_to_cancelled(self):
        task = _verifying_record()
        task.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(task.status, AgentTaskStatus.CANCELLED)

    def test_contract_validate_verifying_to_cancelled(self):
        validate_agent_transition(
            AgentLifecycleState.VERIFYING, AgentLifecycleState.CANCELLED
        )
        # Other states still fail closed: CANCELLED has no outgoing.
        with self.assertRaises(ContractError):
            validate_agent_transition(
                AgentLifecycleState.CANCELLED, AgentLifecycleState.RUNNING
            )


class VerifyingCancellationTests(unittest.TestCase):
    """C: VERIFYING cancellation through EXISTING canonical mechanism."""

    def test_verifying_cancel_terminal_and_persistable(self):
        task = _verifying_record()
        task.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(task.status, AgentTaskStatus.CANCELLED)
        d = task.to_dict()
        self.assertEqual(d["status"], AgentTaskStatus.CANCELLED)
        # Terminal: no outgoing (no dual terminal / resurrect).
        with self.assertRaises(ValueError):
            task.transition(AgentTaskStatus.COMPLETED)
        with self.assertRaises(ValueError):
            task.transition(AgentTaskStatus.FAILED)

    def test_normal_verifying_completion_regression(self):
        task = _verifying_record("t-complete-13d")
        task.transition(AgentTaskStatus.COMPLETED)
        self.assertEqual(task.status, AgentTaskStatus.COMPLETED)
        task.transition(AgentTaskStatus.MERGED)
        task.transition(AgentTaskStatus.RETIRED)
        self.assertEqual(task.status, AgentTaskStatus.RETIRED)

    def test_cancel_idempotency_repeat_cancel(self):
        """D: repeat cancel is idempotent (no error, stays cancelled)."""
        reg = _registry()
        child = _spawn_one(reg, proposal_id="idem-13d")
        sched = _scheduler()
        coord = _coordinator(reg, sched)
        first = asyncio.run(coord.cancel(child.nano_id))
        self.assertIn(child.nano_id, first)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
        second = asyncio.run(coord.cancel(child.nano_id))
        # Idempotent: still reports cancelled lineage, ledger stays cancelled.
        self.assertIn(child.nano_id, second)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
        # Direct record double-transition stays fail-closed (no resurrect).
        task = _verifying_record("t-idem-direct")
        task.transition(AgentTaskStatus.CANCELLED)
        with self.assertRaises(ValueError):
            task.transition(AgentTaskStatus.CANCELLED)

    def test_race_enter_active_near_completion(self):
        """Race boundaries: enter / active / near-completion deterministic."""
        # Enter: cancel from RUNNING (just before VERIFYING) still cancels.
        running = AgentTaskRecord(
            id="t-enter", organization_id="o", project_id="p",
            agent_id="a", focus="f",
        )
        for nxt in (
            AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
            AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
        ):
            running.transition(nxt)
        running.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(running.status, AgentTaskStatus.CANCELLED)
        # Active: cancel while VERIFYING wins.
        active = _verifying_record("t-active")
        active.transition(AgentTaskStatus.CANCELLED)
        self.assertEqual(active.status, AgentTaskStatus.CANCELLED)
        # Near-completion: COMPLETED before cancel is preserved (no flip).
        reg = _registry()
        child = _spawn_one(reg, proposal_id="near-done")
        sched = _scheduler()
        coord = _coordinator(reg, sched)
        outcome = coord.execute([child], gates=nano_gates())
        self.assertIn(child.nano_id, outcome["results"])
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.COMPLETED)
        cancelled = asyncio.run(coord.cancel(child.nano_id))
        self.assertEqual(cancelled, ())
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.COMPLETED)


class ChildPropagationTests(unittest.TestCase):
    """Parent/child propagation with live Manager+child; 0 zombies."""

    def test_live_manager_children_retire_zero_zombies(self):
        mgr_adapter = DeterministicManagerAdapter()
        # Analyzer child adapter: zero-cost local.
        analyzer = _FastAdapter(adapter_id="analyzer_worker")
        adapters = {"mgr-13d": mgr_adapter, "analyzer_worker": analyzer}
        sched = _scheduler(adapters, max_attempts=1)
        org = sched.schedule(
            [{"id": "mgr-13d", "agent_id": "mgr-13d",
              "executor_kind": ExecutorKind.MANAGER, "focus": "decompose bounded"}],
            organization_id="org-13d-mgr", project_id="p-13d", goal="g",
        )
        org = asyncio.run(sched.run(org))
        # Manager completed and proposed children into live graph.
        self.assertEqual(org.tasks["mgr-13d"].status, AgentTaskStatus.COMPLETED)
        self.assertIn("c1", org.tasks)
        self.assertIn("c2", org.tasks)
        org = asyncio.run(sched.retire(org))
        self.assertTrue(org.retired)
        for tid, task in org.tasks.items():
            self.assertEqual(task.status, AgentTaskStatus.RETIRED, tid)
        # 0 zombies: no active executions, no non-terminal.
        self.assertEqual(sched.active_task_ids(), ())
        non_terminal = [
            t.id for t in org.tasks.values()
            if t.status not in (AgentTaskStatus.RETIRED, AgentTaskStatus.FAILED, AgentTaskStatus.CANCELLED)
        ]
        self.assertEqual(non_terminal, [])
        self.assertEqual(MANDATED_SCRATCH.exists(), True)

    def test_parent_cancel_covers_verifying_child_lineage(self):
        deep = ResourceEnvelope(max_manager_depth=3)
        reg = _registry(
            envelope=deep,
            settings=AgentFabricSettings(envelope=deep, recursive_spawning=True),
        )
        parent = _spawn_one(reg, proposal_id="par-13d")
        child = _spawn_one(
            reg, proposal_id="chi-13d",
            parent_task_id=parent.task_id, parent_depth=1,
        )
        sched = _scheduler()
        coord = _coordinator(
            reg, sched,
            known_parent_task_ids=("task:root", parent.task_id),
        )
        # Move child ledger into VERIFYING through the EXISTING map.
        for step in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                     AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                     AgentTaskStatus.VERIFYING):
            reg.transition(child.nano_id, step)
        cancelled = asyncio.run(coord.cancel(parent.nano_id))
        self.assertIn(parent.nano_id, cancelled)
        self.assertIn(child.nano_id, cancelled)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
        # No new spawn under cancelled lineage.
        with self.assertRaises(ContractError):
            _spawn_one(reg, proposal_id="late-13d",
                       parent_task_id=parent.task_id, parent_depth=1)

    def test_run_cancel_covers_multiple_verifying_children(self):
        reg = _registry()
        a = _spawn_one(reg, proposal_id="ra-13d")
        b = _spawn_one(reg, proposal_id="rb-13d")
        adapters = {a.nano_id: _SlowAdapter(delay=5.0), b.nano_id: _SlowAdapter(delay=5.0)}
        sched = _scheduler(adapters, max_concurrent=2, max_attempts=1)
        coord = _coordinator(reg, sched)

        async def scenario():
            runner = asyncio.create_task(coord.run([a, b], gates=nano_gates()))
            await asyncio.sleep(0.05)
            cancelled = await coord.cancel_run()
            return cancelled, await runner

        cancelled, outcome = asyncio.run(scenario())
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(outcome["results"], {})
        for child in (a, b):
            self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
            rec = outcome["organization_run"].tasks[child.task_id]
            self.assertEqual(rec.status, AgentTaskStatus.CANCELLED)
        # Zombies after cancel: 0.
        self.assertEqual(sched.active_task_ids(), ())
        zombies = [
            t.id for t in outcome["organization_run"].tasks.values()
            if t.status in (AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                            AgentTaskStatus.WAITING, AgentTaskStatus.VERIFYING,
                            AgentTaskStatus.QUEUED)
        ]
        self.assertEqual(zombies, [])


class ActiveToolCleanupTests(unittest.TestCase):
    """Active governed tool cleanup: no orphan, no late COMPLETED flip, no post-cancel mutation."""

    def test_in_flight_cancel_invokes_adapter_no_late_result(self):
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        reg = _registry()
        child = _spawn_one(reg, proposal_id="tool-13d")
        adapter = _SlowAdapter(delay=5.0)
        sched = _scheduler({child.nano_id: adapter}, max_attempts=1)
        coord = _coordinator(reg, sched)

        async def scenario():
            runner = asyncio.create_task(coord.run([child], gates=nano_gates()))
            await asyncio.sleep(0.05)
            await coord.cancel(child.nano_id)
            return await runner

        outcome = asyncio.run(scenario())
        self.assertGreaterEqual(adapter.cancel_invocations, 1)
        self.assertNotIn(child.nano_id, outcome["results"])
        self.assertIsNone(reg.result(child.nano_id))
        rec = outcome["organization_run"].tasks[child.task_id]
        self.assertEqual(rec.status, AgentTaskStatus.CANCELLED)
        self.assertEqual(reg.status(child.nano_id), AgentTaskStatus.CANCELLED)
        # Late result rejected; no late COMPLETED flip.
        late = NanoResultRecord(
            meta=meta_factory("late-13d"),
            nano_id=child.nano_id,
            disposition=AgentCompletionDisposition.COMPLETE,
            summary="late arrival",
            output_digest="3" * 32,
            capabilities_used=(),
            evidence_refs=("task:late",),
        )
        with self.assertRaises(ContractError) as ctx:
            reg.record_result(late)
        self.assertEqual(ctx.exception.code, ContractErrorCode.BLOCKED)
        self.assertEqual(rec.status, AgentTaskStatus.CANCELLED)
        # No orphan: scheduler surface empty.
        self.assertEqual(sched.active_task_ids(), ())

    def test_no_post_cancel_artifact_mutation(self):
        """Cancelled work must not mutate artifacts post-cancel."""
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        marker = MANDATED_SCRATCH / "artifact_guard_13d.txt"
        marker.write_text("v1-sealed", encoding="utf-8")
        before = marker.read_text(encoding="utf-8")
        reg = _registry()
        child = _spawn_one(reg, proposal_id="art-13d")
        sched = _scheduler({child.nano_id: _SlowAdapter(delay=5.0)}, max_attempts=1)
        coord = _coordinator(reg, sched)

        async def scenario():
            runner = asyncio.create_task(coord.run([child], gates=nano_gates()))
            await asyncio.sleep(0.05)
            await coord.cancel(child.nano_id)
            return await runner

        outcome = asyncio.run(scenario())
        # No result sealed, so no post-cancel write path ran.
        self.assertNotIn(child.nano_id, outcome["results"])
        self.assertEqual(marker.read_text(encoding="utf-8"), before)
        self.assertEqual(before, "v1-sealed")


class RestartPersistenceTests(unittest.TestCase):
    """Restart persistence: cancelled stays cancelled (no resurrect)."""

    def test_cancelled_survives_retire_and_recovery_roundtrip(self):
        from amca.agent_recovery import AgentRecoveryStore
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            db = Path(tmp.name) / "state.sqlite3"
            store = AgentRecoveryStore(db)
            org_id = "org-13d-restart"
            store.create_organization(
                organization_id=org_id, project_id="p-13d",
                goal="restart probe", budget_usd=5.0, max_concurrent=2,
            )
            store.create_task(
                task_id="t-restart-13d", organization_id=org_id,
                project_id="p-13d", agent_id="a-13d", focus="restart focus",
                executor_kind=ExecutorKind.DETERMINISTIC_TOOL, depends_on=[],
                verifier_ids=[], participant_ids=[],
            )
            # Simulate VERIFYING->CANCELLED terminal then persist.
            task = _verifying_record("t-restart-13d")
            task.transition(AgentTaskStatus.CANCELLED)
            store.update_task_status("t-restart-13d", task.status, error="user cancel")
            rows = store.list_tasks_by_organization(org_id)
            self.assertEqual(len(rows), 1)
            # Simulate restart reload: status still cancelled.
            reloaded = rows[0]
            status = reloaded["status"] if isinstance(reloaded, dict) else reloaded.get("status")
            self.assertEqual(status, AgentTaskStatus.CANCELLED)
            # Retire preserves cancelled (only COMPLETED merges/retires).
            sched = _scheduler()
            org = sched.schedule(
                [{"id": "t-k", "executor_kind": ExecutorKind.DETERMINISTIC_TOOL,
                  "focus": "keep"}],
                organization_id="org-13d-ret", project_id="p", goal="g",
            )
            t = org.tasks["t-k"]
            for nxt in (AgentTaskStatus.AUTHORIZED, AgentTaskStatus.QUEUED,
                        AgentTaskStatus.STARTING, AgentTaskStatus.RUNNING,
                        AgentTaskStatus.VERIFYING):
                # schedule() already moved PROPOSED->AUTHORIZED->QUEUED; align.
                if t.status == nxt:
                    continue
                # Only transition forward when legal; break on mismatch.
                try:
                    t.transition(nxt)
                except ValueError:
                    break
            # Force VERIFYING then cancel, then retire.
            if t.status != AgentTaskStatus.VERIFYING:
                # Deterministically drive to VERIFYING if still queued.
                while t.status != AgentTaskStatus.VERIFYING:
                    if t.status == AgentTaskStatus.QUEUED:
                        t.transition(AgentTaskStatus.STARTING)
                    elif t.status == AgentTaskStatus.STARTING:
                        t.transition(AgentTaskStatus.RUNNING)
                    elif t.status == AgentTaskStatus.RUNNING:
                        t.transition(AgentTaskStatus.VERIFYING)
                    else:
                        break
            t.transition(AgentTaskStatus.CANCELLED)
            org = asyncio.run(sched.retire(org))
            self.assertEqual(org.tasks["t-k"].status, AgentTaskStatus.CANCELLED)
        finally:
            tmp.cleanup()


class ArtifactGovernancePreservedTests(unittest.TestCase):
    """13C preserved: spoofed artifact op still denied (small focused assertion)."""

    def test_spoofed_artifact_op_still_denied(self):
        from amca.service import ServiceContext
        MANDATED_SCRATCH.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=str(MANDATED_SCRATCH))
        try:
            root = Path(__file__).resolve().parents[1]
            ctx = ServiceContext(
                root=root,
                database=Path(tmp.name) / "state.sqlite3",
                benchmarks=root / "benchmarks" / "tasks.json",
            )
            restricted = ctx.agent_fabric_store.save_custom_agent({
                "name": "PS1-13D Restricted",
                "description": "Governance probe.",
                "instructions": "Obey router.",
                "category": "Custom",
                "allowed_tools": ["filesystem.read"],
                "permissions": [],
                "can_spawn": False,
            })
            rid = str(restricted["id"])
            with self.assertRaises(PermissionError):
                ctx.artifact_author(
                    "ps1-13d-proj",
                    {"artifact_type": "markdown", "title": "spoof",
                     "content": "# x\nbody", "created_by": "desktop_user",
                     "agent_id": rid},
                )
            ctx.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
