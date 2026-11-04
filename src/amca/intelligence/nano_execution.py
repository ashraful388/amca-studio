"""M5.6 governed Nano task execution (bridge from M3 assignments to the
existing Agent Fabric scheduler).

Additive module introduced by the M5.6 Nano Task Execution Integration
milestone (zcode/amca-v0.5.0-m5-6-nano-task-execution-integration-01).

Architecture rule honored: this module EXECUTES THROUGH the existing
v0.4.x Agent Fabric runtime instead of building a competing engine. There
is no second scheduler, no second task lifecycle, and no new execution
primitive here:

REUSED verbatim (imported, files untouched):
- ``amca.agent_runtime.NanoScheduler``        — the existing bounded
  scheduler/executor (admission concurrency, conservative cost
  reservations, per-attempt retries, the 300s model-call wait_for bound,
  deterministic-tool executor, retire/merge with the reuse registry);
- ``amca.agent_runtime.AgentTaskRecord``      — the existing task record
  and its audited ``ALLOWED_TRANSITIONS`` lifecycle;
- ``amca.intelligence.nano_foundation``       — the M3 governed front:
  sealed ``NanoAssignment`` envelopes, the ``NanoRunRegistry`` ledger
  (governor bounds, hierarchy, cancellation with descendant propagation,
  scope-checked ``NanoResultRecord`` results).

ADAPTED here: the missing execution link. The coordinator takes only
SEALED M3 assignments, mirrors the governed lifecycle into the ledger,
hands the work to the existing scheduler as a normal organization run,
reconciles the terminal record states back into the ledger, and seals
scope-checked results. Cancellation of in-flight Nano work is signalled
through the existing adapter cancel protocol (the same
``ModelAdapter.cancel(...)`` mechanism the rest of AMCA uses) — no
competing cancellation protocol.

Import-isolated as in M0-M5.5: nothing in the pre-existing application
imports this package; the nano_agents gate remains FALSE and a gate-OFF
runtime constructs/executes zero Nano tasks.
"""

import asyncio
import hashlib
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple

from ..agent_runtime import (
    AgentTaskStatus,
    ExecutorKind,
    NanoScheduler,
)
from .contracts import (
    AgentCompletionDisposition,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
)
from .gates import FeatureGateRegistry
from .nano_foundation import (
    NanoAssignment,
    NanoResultRecord,
    NanoRunRegistry,
    require_nano_gate,
)


class NanoExecutionCoordinator:
    """Executes sealed M3 Nano assignments through the existing scheduler.

    The coordinator holds no execution authority of its own: every task is
    dispatched by the caller-supplied existing ``NanoScheduler``, every
    lifecycle change is validated by the existing audited transition map
    (via both the task record and the M3 registry ledger), and every result
    is scope-checked by the M3 registry before it becomes readable.
    """

    def __init__(
        self,
        *,
        registry: NanoRunRegistry,
        scheduler: NanoScheduler,
        meta_factory: Callable[[str], ContractMetadata],
        known_parent_task_ids: Tuple[str, ...] = (),
        run_timeout_seconds: Optional[float] = None,
    ) -> None:
        if not isinstance(registry, NanoRunRegistry):
            raise ContractError(
                ContractErrorCode.INVALID,
                "nano execution requires the M3 NanoRunRegistry ledger",
            )
        if not isinstance(scheduler, NanoScheduler):
            raise ContractError(
                ContractErrorCode.INVALID,
                "nano execution must reuse the existing NanoScheduler, not a "
                "second execution engine",
            )
        self._registry = registry
        self._scheduler = scheduler
        self._meta_factory = meta_factory
        # M5.6.1: the live organization run for the current/last governed
        # execution; governed cancellation reads its record states.
        self._active_run = None
        # The existing run's task ids that are allowed to parent Nano work;
        # assignments whose parent is unknown to the run are orphans.
        self.known_parent_task_ids = tuple(str(item) for item in known_parent_task_ids)
        if run_timeout_seconds is not None and run_timeout_seconds <= 0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "run_timeout_seconds must be positive",
            )
        self.run_timeout_seconds = run_timeout_seconds

    # -- governed execution ----------------------------------------------------

    def execute(
        self,
        assignments: Sequence[NanoAssignment],
        *,
        gates: FeatureGateRegistry,
        cancel_check: Optional[Callable[[], bool]] = None,
        project_id: str = "nano-run",
        goal: str = "governed nano execution",
    ) -> Mapping[str, Any]:
        """Synchronous entry: run governed Nano work to a terminal state."""

        return asyncio.run(
            self.run(
                assignments,
                gates=gates,
                cancel_check=cancel_check,
                project_id=project_id,
                goal=goal,
            )
        )

    async def run(
        self,
        assignments: Sequence[NanoAssignment],
        *,
        gates: FeatureGateRegistry,
        cancel_check: Optional[Callable[[], bool]] = None,
        project_id: str = "nano-run",
        goal: str = "governed nano execution",
    ) -> Mapping[str, Any]:
        """Execute sealed assignments through the existing scheduler.

        Returns ``{"results": {nano_id: NanoResultRecord}, "cancelled":
        (nano_id, ...), "organization_run": OrganizationRun,
        "midflight_cancelled": bool}``. Cancelled Nano work produces no
        result record: a result presented after cancellation can never
        become authoritative.
        """

        # Gate first: a disabled gate means zero Nano construction work.
        require_nano_gate(gates)
        batch = tuple(assignments)
        if not batch:
            raise ContractError(
                ContractErrorCode.INVALID,
                "nano execution requires at least one sealed assignment",
            )
        for assignment in batch:
            self._validate_sealed_assignment(assignment)

        # Cancellation before dispatch: nothing is scheduled, nothing runs.
        if self._registry.run_cancelled or (
            cancel_check is not None and cancel_check()
        ):
            cancelled = []
            for assignment in batch:
                # Terminal from every active state (existing M3 mechanism);
                # nothing has been dispatched, so nothing is preserved.
                self._registry.cancel_member(assignment.nano_id)
                cancelled.append(assignment.nano_id)
            return {
                "results": {},
                "cancelled": tuple(dict.fromkeys(cancelled)),
                "organization_run": None,
                "midflight_cancelled": False,
            }

        entries = []
        for assignment in batch:
            if assignment.run_ref != batch[0].run_ref:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a nano execution batch must belong to a single run",
                )
            self._registry.transition(assignment.nano_id, AgentTaskStatus.AUTHORIZED)
            self._registry.transition(assignment.nano_id, AgentTaskStatus.QUEUED)
            entries.append(self._graph_entry(assignment))

        organization_run = self._scheduler.schedule(
            entries,
            organization_id=batch[0].run_ref,
            project_id=project_id,
            goal=goal,
        )
        # M5.6.1: governed cancellation needs the live record states.
        self._active_run = organization_run

        timed_out = False
        try:
            if self.run_timeout_seconds is not None:
                await asyncio.wait_for(
                    self._scheduler.run(organization_run),
                    timeout=self.run_timeout_seconds,
                )
            else:
                await self._scheduler.run(organization_run)
        except asyncio.TimeoutError:
            # The coordinator's bounded-execution limit fired: every touched
            # task must reach an explicit non-success terminal disposition.
            timed_out = True
        except asyncio.CancelledError:
            # Outer cancellation of the coordinator task itself: classify
            # truthfully, seal no results, and leave the ledger terminal.
            # (Governed in-run cancellation no longer aborts the scheduler
            # run — M5.6.1 routes it through the per-record cancel path.)
            cancelled = []
            for assignment in batch:
                if self._registry.status(assignment.nano_id) != AgentTaskStatus.CANCELLED:
                    self._registry.cancel_member(assignment.nano_id)
                cancelled.append(assignment.nano_id)
            return {
                "results": {},
                "cancelled": tuple(dict.fromkeys(cancelled)),
                "organization_run": organization_run,
                "midflight_cancelled": True,
            }

        return self._reconcile(
            batch,
            organization_run,
            cancel_check=cancel_check,
            timed_out=timed_out,
        )

    # -- governed cancellation ---------------------------------------------------

    async def cancel(self, nano_id: str) -> Tuple[str, ...]:
        """Authoritative governed cancellation for one whole lineage.

        M5.6.1 semantics, per member (single-threaded event loop makes the
        per-record reads deterministic):

        - record already COMPLETED/FAILED -> history is preserved: the
          member keeps its truthful terminal state and any sealed result;
        - record QUEUED (never dispatched) -> ledger and record both become
          CANCELLED, and the scheduler can never dispatch it (queue
          protection);
        - record actively executing -> the ledger becomes CANCELLED, the
          in-flight execution is stopped through the scheduler's
          cancellation surface (which transitions the record truthfully
          when the cancellation lands), and the adapter receives the
          EXISTING cancel signal — no competing protocol;
        - the registry refuses any new spawn under the cancelled lineage
          (existing M3 parent/run checks).
        """

        cancelled: list[str] = []
        root = self._registry.assignment(nano_id)
        if root is None:
            raise ContractError(
                ContractErrorCode.INVALID, f"unknown nano agent {nano_id!r}"
            )
        for member in self._registry.lineage_members(root.task_id):
            if self._registry.status(member) == AgentTaskStatus.CANCELLED:
                cancelled.append(member)
                continue
            if self._registry.status(member) == AgentTaskStatus.RETIRED:
                continue
            record = None
            if self._active_run is not None:
                assignment = self._registry.assignment(member)
                record = self._active_run.tasks.get(assignment.task_id)
            record_status = record.status if record is not None else None
            if record_status in (
                AgentTaskStatus.COMPLETED,
                AgentTaskStatus.FAILED,
            ):
                # committed before cancellation: truthful history stays
                continue
            self._registry.cancel_member(member)
            cancelled.append(member)
            if record is None:
                continue
            if record.status == AgentTaskStatus.QUEUED:
                # queue protection: a cancelled member can never dispatch
                record.transition(AgentTaskStatus.CANCELLED)
            elif record.status in (
                AgentTaskStatus.STARTING,
                AgentTaskStatus.RUNNING,
                AgentTaskStatus.WAITING,
                # PS1-13D (PS1-13B-FUNC-01): VERIFYING uses the EXISTING
                # scheduler + adapter cancel path (no parallel mechanism).
                AgentTaskStatus.VERIFYING,
            ):
                # stop the in-flight execution through the existing
                # scheduler surface + the existing adapter cancel protocol
                try:
                    await self._scheduler.cancel_active_execution(record.id)
                except Exception:
                    pass
                adapter = self._scheduler.adapters_by_id.get(member)
                cancel_method = getattr(adapter, "cancel", None)
                if cancel_method is not None:
                    try:
                        outcome = cancel_method()
                        if asyncio.iscoroutine(outcome):
                            await outcome
                    except Exception:
                        # the ledger terminal state stays authoritative even
                        # when an adapter cannot acknowledge the stop signal
                        pass
        return tuple(cancelled)

    async def cancel_run(self) -> Tuple[str, ...]:
        """Authoritative run-wide cancellation across every lineage."""

        cancelled: list[str] = []
        for assignment in self._registry.assignments():
            cancelled.extend(await self.cancel(assignment.nano_id))
        return tuple(dict.fromkeys(cancelled))

    # -- validation and reconciliation ---------------------------------------------

    def _validate_sealed_assignment(self, assignment: NanoAssignment) -> None:
        """Only a sealed, fresh, in-lineage assignment may execute."""

        sealed = self._registry.assignment(assignment.nano_id)
        if sealed is None:
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"nano {assignment.nano_id!r} has no sealed assignment; raw "
                "proposals and unregistered tasks can never execute",
            )
        if sealed.content_digest() != assignment.content_digest():
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"nano {assignment.nano_id!r} was presented with a mutated "
                "assignment (privilege widening or tampering is rejected)",
            )
        if self._registry.run_cancelled or self._registry.is_cancelled_lineage(
            assignment.parent_task_id
        ):
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"nano {assignment.nano_id!r} belongs to a cancelled lineage",
            )
        status = self._registry.status(assignment.nano_id)
        if status != AgentTaskStatus.PROPOSED:
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"nano {assignment.nano_id!r} is in terminal or active state "
                f"{status!r}; duplicate execution is rejected",
            )
        if (
            self.known_parent_task_ids
            and assignment.parent_task_id not in self.known_parent_task_ids
        ):
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"nano {assignment.nano_id!r} is an orphan: parent task "
                f"{assignment.parent_task_id!r} is not part of this run",
            )

    def _graph_entry(self, assignment: NanoAssignment) -> Mapping[str, Any]:
        """Map a sealed assignment onto the EXISTING scheduler graph format."""

        # The deterministic-tool executor is the no-model bounded default;
        # the adapter-backed executor reuses the scheduler's existing
        # nano_local_model path only when the caller bound a local adapter
        # for this specific Nano agent id.
        if self._scheduler.adapters_by_id.get(assignment.nano_id) is not None:
            executor_kind = ExecutorKind.NANO_LOCAL_MODEL
        else:
            executor_kind = ExecutorKind.DETERMINISTIC_TOOL
        entry: dict[str, Any] = {
            "id": assignment.task_id,
            "agent_id": assignment.nano_id,
            "executor_kind": executor_kind,
            "focus": assignment.purpose,
        }
        if assignment.expected_cost > 0:
            entry["max_cost_usd"] = float(assignment.expected_cost)
        return entry

    def _reconcile(
        self,
        batch: Tuple[NanoAssignment, ...],
        organization_run,
        *,
        cancel_check: Optional[Callable[[], bool]],
        timed_out: bool,
    ) -> Mapping[str, Any]:
        """Mirror terminal record states into the ledger and seal results.

        M5.6.1 precedence, deterministic under the single-threaded loop:
        a committed terminal record stands (history is never rewritten);
        an authoritative cancellation committed before the record turned
        terminal wins (no result may surface); and the two ledgers must
        always agree on the terminal state.
        """

        results: dict[str, NanoResultRecord] = {}
        cancelled: list[str] = []
        for assignment in batch:
            record = organization_run.tasks.get(assignment.task_id)
            ledger_status = self._registry.status(assignment.nano_id)
            record_status = record.status if record is not None else None
            if record_status == AgentTaskStatus.COMPLETED:
                if ledger_status == AgentTaskStatus.CANCELLED:
                    # structurally unreachable (cancel() preserves committed
                    # completions and the scheduler extension prevents post-
                    # cancel commits); fail closed if the invariant breaks
                    raise ContractError(
                        ContractErrorCode.FAILED,
                        f"lifecycle ledgers diverged for "
                        f"{assignment.nano_id!r}: record COMPLETED but "
                        "ledger CANCELLED",
                    )
                self._mirror_chain(assignment.nano_id, "completed")
                results[assignment.nano_id] = self._seal_result(
                    assignment,
                    record,
                    disposition=AgentCompletionDisposition.COMPLETE,
                    summary=record.result_summary or "",
                )
                continue
            if record_status == AgentTaskStatus.FAILED:
                self._mirror_chain(assignment.nano_id, "failed")
                results[assignment.nano_id] = self._seal_result(
                    assignment,
                    record,
                    disposition=AgentCompletionDisposition.FAILED,
                    summary=record.error or "executor failure",
                )
                continue
            if record_status == AgentTaskStatus.CANCELLED:
                # the scheduler or the governed cancel path terminated it
                if ledger_status != AgentTaskStatus.CANCELLED:
                    self._registry.cancel_member(assignment.nano_id)
                cancelled.append(assignment.nano_id)
                continue
            authoritative_cancel = ledger_status == AgentTaskStatus.CANCELLED or (
                cancel_check is not None and cancel_check()
            )
            if authoritative_cancel:
                # cancellation committed while the record was still active:
                # it wins, no result may be sealed
                # PS1-13D: VERIFYING (and other actives) use the EXISTING
                # canonical transition; race-safe, no dual terminal.
                if record is not None and record.status in (
                    AgentTaskStatus.QUEUED,
                    AgentTaskStatus.STARTING,
                    AgentTaskStatus.RUNNING,
                    AgentTaskStatus.WAITING,
                    AgentTaskStatus.VERIFYING,
                ):
                    # QUEUED/VERIFYING transition directly; STARTING/RUNNING/
                    # WAITING are already stopped via the scheduler surface
                    # but transition here if still active (deterministic).
                    if record.status in (AgentTaskStatus.QUEUED, AgentTaskStatus.VERIFYING):
                        record.transition(AgentTaskStatus.CANCELLED)
                    elif record.status in (
                        AgentTaskStatus.STARTING,
                        AgentTaskStatus.RUNNING,
                        AgentTaskStatus.WAITING,
                    ):
                        try:
                            record.transition(AgentTaskStatus.CANCELLED)
                        except Exception:
                            pass
                if ledger_status != AgentTaskStatus.CANCELLED:
                    self._registry.cancel_member(assignment.nano_id)
                cancelled.append(assignment.nano_id)
                continue
            if timed_out:
                if record is not None and record.status in (
                    AgentTaskStatus.STARTING,
                    AgentTaskStatus.RUNNING,
                    AgentTaskStatus.WAITING,
                    AgentTaskStatus.VERIFYING,
                ):
                    record.error = (
                        f"nano execution exceeded the {self.run_timeout_seconds}s "
                        "coordinator run timeout"
                    )
                    self._mirror_chain(assignment.nano_id, "failed")
                    results[assignment.nano_id] = self._seal_result(
                        assignment,
                        record,
                        disposition=AgentCompletionDisposition.FAILED,
                        summary=record.error,
                    )
                    continue
                # never dispatched before the run timeout: cancelled, and
                # truthfully so (it did not run, so it did not fail)
                if record is not None and record.status == AgentTaskStatus.QUEUED:
                    record.transition(AgentTaskStatus.CANCELLED)
                self._registry.cancel_member(assignment.nano_id)
                cancelled.append(assignment.nano_id)
                continue
            raise ContractError(
                ContractErrorCode.FAILED,
                f"nano {assignment.nano_id!r} ended in unterminated state "
                f"{record_status!r}; refusing to report success",
            )
        return {
            "results": results,
            "cancelled": tuple(dict.fromkeys(cancelled)),
            "organization_run": organization_run,
            "midflight_cancelled": False,
        }

    def _seal_result(
        self,
        assignment: NanoAssignment,
        record,
        *,
        disposition: AgentCompletionDisposition,
        summary: str,
    ) -> NanoResultRecord:
        """Build the typed M3 result and let the registry scope-check it."""

        result = NanoResultRecord(
            meta=self._meta_factory(f"result-{assignment.nano_id}"),
            nano_id=assignment.nano_id,
            disposition=disposition,
            summary=str(summary)[:500],
            output_digest=hashlib.sha256(
                str(summary).encode("utf-8")
            ).hexdigest()[:32],
            capabilities_used=(),
            evidence_refs=(f"task:{record.id}", f"executor:{record.executor_kind}"),
        )
        # The registry rejects results claiming capabilities outside the
        # immutable assignment scope (and, post-M5.6, any result for a
        # cancelled nano).
        return self._registry.record_result(result)

    # -- ledger mirroring ----------------------------------------------------

    _MIRROR_CHAINS: Mapping[str, Tuple[str, ...]] = {
        "completed": (
            AgentTaskStatus.STARTING,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.VERIFYING,
            AgentTaskStatus.COMPLETED,
        ),
        "failed": (
            AgentTaskStatus.STARTING,
            AgentTaskStatus.RUNNING,
            AgentTaskStatus.FAILED,
        ),
    }

    def _mirror_chain(self, nano_id: str, outcome: str) -> None:
        """Replay the production lifecycle path into the M3 ledger.

        The existing scheduler drove its own task record through the audited
        transitions; the registry mirror follows the same validated edges so
        the governed ledger stays truthful (each step re-validated by the
        existing ALLOWED_TRANSITIONS map). Cancellation needs no mirror: the
        registry's own cancel mechanism is terminal from every active state.
        """

        for step in self._MIRROR_CHAINS[outcome]:
            self._registry.transition(nano_id, step)
