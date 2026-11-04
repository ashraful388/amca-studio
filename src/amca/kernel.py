"""Asynchronous Solo and same-focus Joint Deliberation kernel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterable, Sequence
from uuid import uuid4

from amca.context import ContextBuilder
from amca.domain import (
    CheckpointStatus,
    CognitiveEvent,
    CollaborationMode,
    DecisionClass,
    DecisionRecord,
    EvidenceKind,
    EvidenceRecord,
    EvidenceStrength,
    EventType,
    EvaluatorKind,
    GoalCriterion,
    ParticipantDisposition,
    ParticipantDispositionStatus,
    ProjectState,
    Severity,
    SynchronizationCheckpoint,
    SyncMode,
    VerificationStatus,
    utc_now,
)
from amca.events import EventBus
from amca.models.base import ModelAdapter, ModelRequest, ModelResponse, TokenUsage
from amca.modes import (
    AdaptiveFeatures,
    AlternativePoint,
    DecompositionPlan,
    IndependentPhaseRecord,
    IndependentSubmissionReceipt,
    IntegrationReport,
    NormalizedAlternative,
    SubmissionStatus,
    WorkUnit,
    WorkUnitResult,
    WorkUnitStatus,
    canonical_digest,
    deterministic_integration_report,
)
from amca.routing import AdaptiveRouter, CostLatencyController
from amca.semantic import (
    SEMANTIC_RESPONSE_INSTRUCTIONS,
    SemanticContribution,
    SemanticEventExtractor,
    SemanticExtractionError,
)
from amca.state import StateManager
from amca.storage import BudgetReservationError
from amca.tools import MathTool


_RESERVED_EVENT_SOURCES = frozenset(
    {
        "criterion-evaluator",
        "collaboration-router",
        "cost-monitor",
        "experiment-runner",
        "goal-manager",
        "human-reviewer",
        "math-tool",
        "synchronization-controller",
        "synchronization-router",
        "user",
    }
)

_MODEL_FORBIDDEN_EVENT_TYPES = frozenset(
    {
        EventType.COST_UPDATE,
        EventType.GOAL_PROGRESS,
        EventType.MODEL_ERROR,
        EventType.MODE_SWITCH,
        EventType.PARTICIPANT_DISPOSITION,
        EventType.STATE_UPDATE,
        EventType.SYNC_CHANGE,
        EventType.SYNC_CHECKPOINT_CLOSED,
        EventType.SYNC_CHECKPOINT_OPENED,
        EventType.TOOL_RESULT,
        EventType.USER_INSTRUCTION,
    }
)


def _budget_blocks(state: Any) -> bool:
    """28B: budget checks block only when enforcement is not telemetry.

    Desktop runs mark the goal contract budget_is_telemetry=True; cost is
    then recorded but never blocks execution.  Provider/network
    authorization remains the security control.
    """
    contract = getattr(state, "goal_contract", None)
    return not bool(getattr(contract, "budget_is_telemetry", False))


class StopReason(str, Enum):
    GOAL_SATISFIED = "goal_satisfied"
    ROUND_LIMIT = "round_limit"
    NO_ELIGIBLE_MODELS = "no_eligible_models"
    BUDGET_OR_POLICY = "budget_or_policy"
    SYNCHRONIZATION_BLOCKED = "synchronization_blocked"
    RUNTIME_LIMIT = "runtime_limit"
    INTEGRATION_BLOCKED = "integration_blocked"
    STAGNATION = "stagnation"
    PROVIDER_FAILURE = "provider_failure"


class StalenessDisposition(str, Enum):
    CURRENT = "current"
    COMPATIBLE_MERGE = "compatible_merge"
    REBASED = "rebased"
    CRITICAL_REOPEN = "critical_reopen"
    FAILED = "failed"


class KernelTurnUncertain(RuntimeError):
    """A kernel turn whose prior external outcome is durably UNKNOWN.

    Raised before any redispatch: the turn must never be repeated and the
    architecture blocks semantic continuation from this position (A2.1D)."""


class _KernelTurnUncertain(KernelTurnUncertain):
    """Internal alias so _invoke can re-raise past its generic handler."""


def _public_model_error(exc: Exception) -> str:
    """User-facing model-call failure text: classified, never raw internals.

    The run transcript is user-visible, so adapter class names
    (``OpenAICompatibleAdapter``), raw HTTP codes and transport details
    stay out of it.  Operators keep the full chain via the raised
    exception (``raise ... from exc`` is preserved at the adapter layer)
    and the run log.
    """
    from amca.models.base import (
        ModelAuthenticationError,
        ModelCancelledError,
        ModelConfigurationError,
        ModelInvalidRequestError,
        ModelRateLimitError,
        ModelTimeoutError,
        ModelUnavailableError,
    )

    # asyncio.wait_for raises bare TimeoutError (often with an empty
    # message) on the kernel's own per-call deadline; it carries no
    # ModelTimeoutError wrapper, so it is classified first.
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "The provider did not answer in time. Try again."
    if isinstance(exc, ModelCancelledError):
        return "The model call was cancelled."
    if isinstance(exc, ModelConfigurationError):
        return (
            "The model is not configured (check the API key in "
            "Settings > Models & Providers, then Test connection)."
        )
    if isinstance(exc, ModelAuthenticationError):
        return (
            "The provider rejected the request (check the API key in "
            "Settings > Models & Providers, then Test connection)."
        )
    if isinstance(exc, ModelRateLimitError):
        return "The provider is rate-limiting requests. Wait a moment and try again."
    if isinstance(exc, ModelTimeoutError):
        return "The provider did not answer in time. Try again."
    if isinstance(exc, ModelUnavailableError):
        return "The provider is temporarily unavailable. Try again in a moment."
    if isinstance(exc, ModelInvalidRequestError):
        return "The provider rejected the request as invalid for this model."
    return "The model call failed. Try again."


@dataclass(slots=True)
class ModelCallRecord:
    adapter_id: str
    round_number: int
    start_state_version: int
    start_cognitive_revision: int
    focus: str
    status: str = "thinking"
    response_state_version: int | None = None
    integrated_state_version: int | None = None
    elapsed_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    summary: str = ""
    focus_resolved: bool = False
    error: str | None = None
    staleness: StalenessDisposition = StalenessDisposition.CURRENT
    rebase_calls: int = 0
    cognitive_lag: int = 0
    reservation_id: str | None = None
    reserved_api_cost_usd: float = 0.0
    usage_uncertain: bool = False
    #: Provider-reported facts about this call that the run record must keep.
    #: The governed tool loop records the tool calls the provider asked for
    #: that the iteration budget did not allow to run, so the record never
    #: reads as if the run completed work it skipped.
    provider_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KernelConfig:
    max_rounds: int = 3
    max_adaptive_phases: int = 4
    max_staleness_versions: int = 4
    model_timeout_seconds: float = 180.0
    maximum_context_items: int = 12
    recent_event_limit: int = 8
    rebase_stale_responses: bool = True
    bounded_staleness_base_mode: SyncMode = SyncMode.HARD
    checkpoint_timeout_seconds: float = 30.0
    model_reservation_lease_seconds: float = 300.0
    max_output_tokens_per_call: int = 1800
    desktop_cloud_call_limit: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "bounded_staleness_base_mode",
            SyncMode(self.bounded_staleness_base_mode),
        )
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if self.max_adaptive_phases < 1:
            raise ValueError("max_adaptive_phases must be positive")
        if self.max_output_tokens_per_call < 1:
            raise ValueError("max_output_tokens_per_call must be positive")
        if self.max_staleness_versions < 0:
            raise ValueError("max_staleness_versions must be non-negative")
        if not math.isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("model_timeout_seconds must be finite and positive")
        for name in (
            "checkpoint_timeout_seconds",
            "model_reservation_lease_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.bounded_staleness_base_mode is SyncMode.BOUNDED_STALENESS:
            raise ValueError(
                "bounded staleness requires a non-bounded synchronization base"
            )


@dataclass(frozen=True, slots=True)
class KernelResult:
    state: ProjectState
    stop_reason: StopReason
    rounds_completed: int
    call_records: tuple[ModelCallRecord, ...]
    synchronization_passed: bool
    criterion_attestations: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict
    )
    mode_trace: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "stop_reason": self.stop_reason.value,
            "rounds_completed": self.rounds_completed,
            "synchronization_passed": self.synchronization_passed,
            "criterion_attestations": {
                criterion_id: dict(by_source)
                for criterion_id, by_source in self.criterion_attestations.items()
            },
            "mode_trace": [dict(record) for record in self.mode_trace],
            "state": self.state.to_dict(),
            "calls": [
                {
                    "adapter_id": call.adapter_id,
                    "round": call.round_number,
                    "status": call.status,
                    "start_state_version": call.start_state_version,
                    "start_cognitive_revision": call.start_cognitive_revision,
                    "response_state_version": call.response_state_version,
                    "integrated_state_version": call.integrated_state_version,
                    "elapsed_seconds": call.elapsed_seconds,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "cost_usd": call.cost_usd,
                    "summary": call.summary,
                    "focus_resolved": call.focus_resolved,
                    "error": call.error,
                    "staleness": call.staleness.value,
                    "rebase_calls": call.rebase_calls,
                    "cognitive_lag": call.cognitive_lag,
                    "reservation_id": call.reservation_id,
                    "reserved_api_cost_usd": call.reserved_api_cost_usd,
                    "usage_uncertain": call.usage_uncertain,
                    "provider_metadata": dict(call.provider_metadata),
                }
                for call in self.call_records
            ],
        }


@dataclass(slots=True)
class _CompletedCall:
    adapter: ModelAdapter
    response: ModelResponse | None
    record: ModelCallRecord


@dataclass(slots=True)
class _AdmittedCall:
    call_key: str
    adapter: ModelAdapter
    request: ModelRequest
    record: ModelCallRecord
    reserved_api_cost: float


@dataclass(slots=True)
class _ParallelPrivateResult:
    unit: WorkUnit
    call: _CompletedCall | None
    contribution: SemanticContribution | None
    public_result: WorkUnitResult


CriterionEvaluator = Callable[
    [ProjectState, GoalCriterion, Mapping[str, str]], str | EvidenceRecord | None
]

IntegrationEvaluator = Callable[
    [ProjectState, DecompositionPlan, Sequence[WorkUnitResult]],
    IntegrationReport | bool | str,
]

RoundPromptAddendum = Callable[[int, ProjectState], str]


class CollaborationKernel:
    """Coordinate participants around one canonical, evidence-oriented state."""

    def __init__(
        self,
        state_manager: StateManager,
        adapters: Iterable[ModelAdapter],
        *,
        event_bus: EventBus | None = None,
        extractor: SemanticEventExtractor | None = None,
        cost_controller: CostLatencyController | None = None,
        math_tool: MathTool | None = None,
        criterion_evaluator: CriterionEvaluator | None = None,
        integration_evaluator: IntegrationEvaluator | None = None,
        round_prompt_addendum: RoundPromptAddendum | None = None,
        adaptive_router: AdaptiveRouter | None = None,
        config: KernelConfig | None = None,
        recovery_store: Any | None = None,
        journal_context: Mapping[str, object] | None = None,
    ) -> None:
        # A2.1D.1: durable per-call checkpointing of kernel-external model
        # invocations. journal_context carries the stable identity
        # (organization_id/task_id/attempt/architecture/phase) so call keys
        # derive from semantic position, never volatile runtime IDs.
        self.recovery_store = recovery_store
        self.journal_context = dict(journal_context or {})
        self.state_manager = state_manager
        self.adapters = tuple(adapters)
        if len({adapter.adapter_id for adapter in self.adapters}) != len(self.adapters):
            raise ValueError("collaboration participants require unique adapter IDs")
        reserved = sorted(
            adapter.adapter_id
            for adapter in self.adapters
            if adapter.adapter_id in _RESERVED_EVENT_SOURCES
        )
        if reserved:
            raise ValueError(
                "model adapter IDs cannot impersonate reserved event sources: "
                + ", ".join(reserved)
            )
        self.event_bus = event_bus or EventBus()
        self.extractor = extractor or SemanticEventExtractor()
        self.cost_controller = cost_controller or CostLatencyController()
        self.math_tool = math_tool or MathTool()
        self.criterion_evaluator = criterion_evaluator
        self.integration_evaluator = integration_evaluator
        self.round_prompt_addendum = round_prompt_addendum
        self.adaptive_router = adaptive_router or AdaptiveRouter()
        self.config = config or KernelConfig()
        self.context_builder = ContextBuilder(
            max_items_per_section=self.config.maximum_context_items,
            recent_event_limit=self.config.recent_event_limit,
        )
        self._call_records: list[ModelCallRecord] = []
        # Rounds in which an admitted call returned no response because the
        # provider itself failed (transport error, timeout, empty stream).
        # Admission/policy rejections never appear here, so an empty round can
        # be classified honestly instead of being reported as budget/policy.
        self._provider_failure_rounds: set[int] = set()
        self._run_lock = asyncio.Lock()
        self._criterion_attestations: dict[str, dict[str, str]] = {}
        self._mode_trace: list[Mapping[str, object]] = []
        self._session_deadline: float | None = None
        self._round_prompt_addendum = ""
        self._reservation_owner_id: str | None = None
        # A2.1D.1: live adaptive position for deterministic journal keys.
        self._journal_phase: int | None = None

    @property
    def state(self) -> ProjectState:
        return self.state_manager.state

    async def run(
        self,
        *,
        mode: CollaborationMode = CollaborationMode.JOINT,
        sync_mode: SyncMode = SyncMode.BOUNDED_STALENESS,
        decomposition_plan: DecompositionPlan | None = None,
        adaptive_features: AdaptiveFeatures | None = None,
        mode_lock: CollaborationMode | None = None,
        manual_mode: CollaborationMode | None = None,
        released_participant_ids: Iterable[str] = (),
        release_authorized_by: str | None = None,
        anti_anchoring_reason: str | None = None,
    ) -> KernelResult:
        """Run one isolated session, serializing reuse of this kernel instance."""
        # H1.2-B: round builders adapt to the active architecture.
        self._active_mode = mode

        async with self._run_lock:
            released = frozenset(str(item) for item in released_participant_ids)
            unknown_releases = released - {
                adapter.adapter_id for adapter in self.adapters
            }
            if unknown_releases:
                raise ValueError(
                    "released participants are not configured adapters: "
                    + ", ".join(sorted(unknown_releases))
                )
            authorization = (
                release_authorized_by.strip()
                if isinstance(release_authorized_by, str)
                else ""
            )
            if released and not authorization:
                raise ValueError(
                    "released participants require explicit release_authorized_by"
                )
            self._call_records = []
            self._criterion_attestations = {}
            self._mode_trace = []
            self._reservation_owner_id = uuid4().hex
            # Snapshot the per-run cloud-call baseline once per run, before
            # any admission path (rounds, phases, parallel units, rebases)
            # consumes the frontier allowance.
            self.cost_controller.start_run(
                self.state, desktop_cloud_call_limit=self.config.desktop_cloud_call_limit
            )
            try:
                self._reconcile_expired_model_calls()
                await self._close_expired_checkpoint_on_resume()
                contract = self.state.goal_contract
                runtime_limit = (
                    contract.max_runtime_seconds if contract is not None else None
                )
                self._session_deadline = (
                    time.monotonic() + runtime_limit
                    if runtime_limit is not None
                    else None
                )
                return await self._run_once(
                    mode=CollaborationMode(mode),
                    sync_mode=SyncMode(sync_mode),
                    decomposition_plan=decomposition_plan,
                    adaptive_features=adaptive_features,
                    mode_lock=mode_lock,
                    manual_mode=manual_mode,
                    released_participant_ids=released,
                    release_authorized_by=authorization or None,
                    anti_anchoring_reason=anti_anchoring_reason,
                )
            finally:
                self._session_deadline = None
                self._reconcile_current_model_call_owner()
                self._reservation_owner_id = None

    async def _run_once(
        self,
        *,
        mode: CollaborationMode,
        sync_mode: SyncMode,
        decomposition_plan: DecompositionPlan | None = None,
        adaptive_features: AdaptiveFeatures | None = None,
        mode_lock: CollaborationMode | None = None,
        manual_mode: CollaborationMode | None = None,
        released_participant_ids: frozenset[str] = frozenset(),
        release_authorized_by: str | None = None,
        anti_anchoring_reason: str | None = None,
    ) -> KernelResult:
        if mode is CollaborationMode.ADAPTIVE_HYBRID:
            return await self._run_adaptive(
                sync_mode=sync_mode,
                decomposition_plan=decomposition_plan,
                features=adaptive_features or AdaptiveFeatures(),
                mode_lock=mode_lock,
                manual_mode=manual_mode,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                anti_anchoring_reason=anti_anchoring_reason,
            )
        if mode is CollaborationMode.INDEPENDENT_DISCUSS:
            return await self._run_independent_discuss(
                sync_mode=sync_mode,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                anti_anchoring_reason=anti_anchoring_reason,
            )
        if mode is CollaborationMode.PARALLEL:
            if released_participant_ids:
                raise ValueError(
                    "parallel work-unit releases require a revised decomposition plan"
                )
            if decomposition_plan is None:
                raise ValueError("Parallel Decomposition requires a decomposition_plan")
            return await self._run_parallel_decomposition(
                decomposition_plan,
                sync_mode=sync_mode,
            )
        if mode not in {CollaborationMode.SOLO, CollaborationMode.JOINT}:
            raise ValueError(f"unknown executable collaboration mode {mode.value!r}")
        return await self._run_joint_or_solo(
            mode=mode,
            sync_mode=sync_mode,
            released_participant_ids=released_participant_ids,
            release_authorized_by=release_authorized_by,
        )

    async def _run_joint_or_solo(
        self,
        *,
        mode: CollaborationMode,
        sync_mode: SyncMode,
        released_participant_ids: frozenset[str] = frozenset(),
        release_authorized_by: str | None = None,
        max_rounds_override: int | None = None,
    ) -> KernelResult:
        selected = self.adapters[:1] if mode is CollaborationMode.SOLO else self.adapters
        if not selected:
            return self._result(StopReason.NO_ELIGIBLE_MODELS, 0, False)
        selected_ids = {adapter.adapter_id for adapter in selected}
        unrelated_releases = released_participant_ids - selected_ids
        if unrelated_releases:
            raise ValueError(
                "released participants are outside the selected collaboration mode: "
                + ", ".join(sorted(unrelated_releases))
            )
        active_selected = tuple(
            adapter
            for adapter in selected
            if adapter.adapter_id not in released_participant_ids
        )

        await self._apply(
            CognitiveEvent(
                event_type=EventType.MODE_SWITCH,
                source="collaboration-router",
                payload={
                    "mode": mode.value,
                    "participating_models": [
                        adapter.adapter_id for adapter in active_selected
                    ],
                },
                state_version=self.state.version,
            )
        )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.SYNC_CHANGE,
                source="synchronization-router",
                payload={"mode": sync_mode.value},
                state_version=self.state.version,
            )
        )

        if self.state_manager.store is not None:
            self.state_manager.save()

        synchronization_passed = False
        contract = self.state.goal_contract
        round_limit = (
            max_rounds_override
            if max_rounds_override is not None
            else self.config.max_rounds
        )
        if contract is not None and contract.max_iterations is not None:
            round_limit = min(round_limit, contract.max_iterations)
        for round_number in range(1, round_limit + 1):
            remaining_runtime = self._remaining_runtime()
            if remaining_runtime == 0:
                return self._result(
                    StopReason.RUNTIME_LIMIT,
                    round_number - 1,
                    synchronization_passed,
                )
            try:
                if remaining_runtime is None:
                    completed, proposed_evidence = await self._run_round(
                        active_selected, round_number
                    )
                else:
                    completed, proposed_evidence = await asyncio.wait_for(
                        self._run_round(active_selected, round_number),
                        timeout=remaining_runtime,
                    )
            except TimeoutError:
                return self._result(
                    StopReason.RUNTIME_LIMIT,
                    round_number - 1,
                    synchronization_passed,
                )
            checkpoint = await self._record_synchronization_checkpoint(
                sync_mode,
                selected,
                completed,
                round_number=round_number,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
            )
            synchronization_passed = checkpoint.passed
            if synchronization_passed:
                await self._accept_criterion_evidence(
                    proposed_evidence,
                    checkpoint_id=checkpoint.id,
                )
            if self.state.goal_satisfied:
                return self._result(
                    StopReason.GOAL_SATISFIED,
                    round_number,
                    synchronization_passed,
                )
            if self._remaining_runtime() == 0:
                return self._result(
                    StopReason.RUNTIME_LIMIT,
                    round_number,
                    synchronization_passed,
                )
            if not completed:
                return self._result(
                    self._provider_failure_stop_reason(round_number)
                    or StopReason.BUDGET_OR_POLICY,
                    round_number,
                    False,
                )

        reason = (
            StopReason.ROUND_LIMIT
            if synchronization_passed or round_limit == 0
            else StopReason.SYNCHRONIZATION_BLOCKED
        )
        return self._result(reason, round_limit, synchronization_passed)

    async def _run_bounded_phase(
        self,
        *,
        mode: CollaborationMode,
        sync_mode: SyncMode,
        decomposition_plan: DecompositionPlan | None = None,
        released_participant_ids: frozenset[str] = frozenset(),
        release_authorized_by: str | None = None,
        anti_anchoring_reason: str | None = None,
    ) -> KernelResult:
        if mode is CollaborationMode.INDEPENDENT_DISCUSS:
            return await self._run_independent_discuss(
                sync_mode=sync_mode,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                anti_anchoring_reason=anti_anchoring_reason,
            )
        if mode is CollaborationMode.PARALLEL:
            if released_participant_ids:
                raise ValueError(
                    "parallel work-unit releases require a revised decomposition plan"
                )
            if decomposition_plan is None:
                raise ValueError("Parallel Decomposition requires a decomposition_plan")
            return await self._run_parallel_decomposition(
                decomposition_plan,
                sync_mode=sync_mode,
            )
        if mode is CollaborationMode.JOINT:
            return await self._run_joint_or_solo(
                mode=mode,
                sync_mode=sync_mode,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                max_rounds_override=1,
            )
        if mode is CollaborationMode.SOLO:
            return await self._run_joint_or_solo(
                mode=mode,
                sync_mode=sync_mode,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                max_rounds_override=1,
            )
        raise ValueError(f"unknown executable collaboration mode {mode.value!r}")

    def _progress_fingerprint(self, state: ProjectState) -> str:
        criteria = ()
        if state.goal_contract is not None:
            # GoalContract.criteria is a list of GoalCriterion records.
            criteria = tuple(
                sorted(
                    (c.id, c.status.value)
                    for c in state.goal_contract.criteria
                )
            )
        data = {
            "goal_satisfied": state.goal_satisfied,
            "accepted_facts": len(state.accepted_facts),
            "evidence": len(state.evidence),
            "evidence_records": len(state.evidence_records),
            "hypotheses": len(state.hypotheses),
            "equations": len(state.equations),
            "pending_ideas": len(state.pending_ideas),
            "rejected_ideas": len(state.rejected_ideas),
            "open_questions": len(state.open_questions),
            "unresolved_disagreements": len(state.unresolved_disagreements),
            "disagreement_ids": [d.id for d in state.unresolved_disagreements],
            "criteria": criteria,
            "current_focus": state.current_focus,
            "active_subproblem": state.active_subproblem,
            "experimental_results": len(state.experimental_results),
            "critical_objections": [
                d.id for d in state.unresolved_disagreements if d.severity is Severity.CRITICAL
            ],
        }
        return canonical_digest(data)

    def _extract_runtime_features(
        self,
        *,
        initial_features: AdaptiveFeatures,
        state: ProjectState,
        last_mode: CollaborationMode,
        phase_result: KernelResult,
        meaningful_progress: bool,
    ) -> AdaptiveFeatures:
        has_parallel_conflict = (
            phase_result.stop_reason is StopReason.INTEGRATION_BLOCKED
            or any(
                isinstance(r, Mapping) and bool(r.get("conflicts"))
                for r in state.experimental_results[-3:]
            )
            or any(
                isinstance(r, Mapping)
                and r.get("type") == "parallel_decomposition"
                and not r.get("integration", {}).get("passed", True)
                for r in self._mode_trace[-3:]
            )
        )
        has_high_disagreement = (
            any(
                d.severity in {Severity.HIGH, Severity.CRITICAL}
                for d in state.unresolved_disagreements
            )
            or initial_features.high_disagreement
        )
        has_no_active_disagreement = len(state.unresolved_disagreements) == 0
        is_stagnating = (not meaningful_progress) or initial_features.stagnating
        is_routine_residual = (
            has_no_active_disagreement
            and not state.has_critical_objection
            and not has_parallel_conflict
            and (state.goal_satisfied or initial_features.routine_residual)
        )

        return AdaptiveFeatures(
            weakly_coupled=initial_features.weakly_coupled and not has_parallel_conflict,
            novelty_required=initial_features.novelty_required,
            stagnating=is_stagnating,
            high_disagreement=has_high_disagreement,
            parallel_conflict=has_parallel_conflict,
            routine_residual=is_routine_residual,
            agreement_established=initial_features.agreement_established
            or (
                has_no_active_disagreement
                and len(state.recent_events) > 0
                and meaningful_progress
            ),
            no_active_disagreement=has_no_active_disagreement,
            ambiguous_mixed_signals=False,
            cost_pressure=initial_features.cost_pressure,
            time_pressure=initial_features.time_pressure,
            estimated_cost_usd=initial_features.estimated_cost_usd,
            estimated_latency_seconds=initial_features.estimated_latency_seconds,
        )

    async def _run_adaptive(
        self,
        *,
        sync_mode: SyncMode,
        decomposition_plan: DecompositionPlan | None,
        features: AdaptiveFeatures,
        mode_lock: CollaborationMode | None,
        manual_mode: CollaborationMode | None,
        released_participant_ids: frozenset[str],
        release_authorized_by: str | None,
        anti_anchoring_reason: str | None,
    ) -> KernelResult:
        """Run the bounded Adaptive Hybrid supervisor loop.

        Note on persistence and recovery:
        - Adaptive phase and transition metadata survives project recovery via
          durable 'adaptive-transition:<version>' cognitive artifacts and event replay.
        - Mid-run Adaptive phase continuation/resume across kernel restart is not yet
          implemented; a newly instantiated supervisor initializes from phase 0.
        """
        max_phases = getattr(self.config, "max_adaptive_phases", 4)
        phase_count = 0
        current_features = features
        current_sync_mode = sync_mode
        last_result: KernelResult | None = None

        while phase_count < max_phases:
            transition = self.adaptive_router.decide(
                self.state,
                current_features,
                plan_available=decomposition_plan is not None,
                requested_sync_mode=current_sync_mode,
                user_lock=mode_lock,
                manual_instruction=manual_mode if phase_count == 0 else None,
                phase_index=phase_count,
            )
            record = transition.to_dict()
            self._mode_trace.append(record)

            await self._apply(
                CognitiveEvent(
                    event_type=EventType.ARTIFACT_UPDATE,
                    source="collaboration-router",
                    payload={
                        "artifact_id": f"adaptive-transition:{self.state.version + 1}",
                        **record,
                    },
                    state_version=self.state.version,
                )
            )

            if (
                transition.selected_mode is CollaborationMode.PARALLEL
                and decomposition_plan is None
            ):
                raise ValueError(
                    "Adaptive transition selected Parallel Decomposition, but no "
                    "decomposition_plan satisfies its mandatory entry condition"
                )

            fingerprint_before = self._progress_fingerprint(self.state)
            version_before = self.state.version

            self._journal_phase = phase_count + 1
            phase_result = await self._run_bounded_phase(
                mode=transition.selected_mode,
                sync_mode=transition.selected_sync_mode,
                decomposition_plan=decomposition_plan,
                released_participant_ids=released_participant_ids,
                release_authorized_by=release_authorized_by,
                anti_anchoring_reason=anti_anchoring_reason,
            )
            phase_count += 1
            last_result = phase_result

            fingerprint_after = self._progress_fingerprint(self.state)
            meaningful_progress = (fingerprint_after != fingerprint_before)

            record["state_version_after"] = self.state.version
            record["phase_stop_reason"] = phase_result.stop_reason.value

            if self.state.goal_satisfied:
                return self._result(
                    StopReason.GOAL_SATISFIED,
                    phase_count,
                    phase_result.synchronization_passed,
                )
            if phase_result.stop_reason in {
                StopReason.RUNTIME_LIMIT,
                StopReason.NO_ELIGIBLE_MODELS,
            }:
                return self._result(
                    phase_result.stop_reason,
                    phase_count,
                    phase_result.synchronization_passed,
                )

            if not meaningful_progress and phase_count > 1:
                return self._result(
                    StopReason.STAGNATION,
                    phase_count,
                    phase_result.synchronization_passed,
                )

            current_features = self._extract_runtime_features(
                initial_features=features,
                state=self.state,
                last_mode=transition.selected_mode,
                phase_result=phase_result,
                meaningful_progress=meaningful_progress,
            )

            if self.state.has_critical_objection or current_features.parallel_conflict:
                current_sync_mode = SyncMode.HARD
            elif current_features.high_disagreement:
                current_sync_mode = SyncMode.QUORUM
            else:
                current_sync_mode = transition.selected_sync_mode

            if (
                transition.selected_mode is CollaborationMode.SOLO
                and meaningful_progress
                and not current_features.parallel_conflict
                and not current_features.high_disagreement
                and not current_features.stagnating
            ):
                return self._result(
                    phase_result.stop_reason,
                    phase_count,
                    phase_result.synchronization_passed,
                )

            if (
                transition.selected_mode is CollaborationMode.PARALLEL
                and not current_features.parallel_conflict
                and not current_features.high_disagreement
                and not self.state.has_critical_objection
            ):
                return self._result(
                    phase_result.stop_reason,
                    phase_count,
                    phase_result.synchronization_passed,
                )

            if (
                transition.selected_mode is CollaborationMode.INDEPENDENT_DISCUSS
                and not current_features.parallel_conflict
                and not current_features.high_disagreement
                and not self.state.has_critical_objection
            ):
                return self._result(
                    phase_result.stop_reason,
                    phase_count,
                    phase_result.synchronization_passed,
                )

            if (
                transition.selected_mode is CollaborationMode.JOINT
                and phase_count > 1
                and not current_features.parallel_conflict
                and not self.state.has_critical_objection
                and not current_features.high_disagreement
            ):
                return self._result(
                    phase_result.stop_reason,
                    phase_count,
                    phase_result.synchronization_passed,
                )

        return self._result(
            StopReason.ROUND_LIMIT,
            phase_count,
            last_result.synchronization_passed if last_result else True,
        )

    async def _run_independent_discuss(
        self,
        *,
        sync_mode: SyncMode,
        released_participant_ids: frozenset[str],
        release_authorized_by: str | None,
        anti_anchoring_reason: str | None,
    ) -> KernelResult:
        selected = self.adapters
        participant_ids = {adapter.adapter_id for adapter in selected}
        unknown_releases = released_participant_ids - participant_ids
        if unknown_releases:
            raise ValueError(
                "released independent participants are not configured adapters: "
                + ", ".join(sorted(unknown_releases))
            )
        if not selected:
            return self._result(StopReason.NO_ELIGIBLE_MODELS, 0, False)

        focus_digest = canonical_digest(self.state.current_focus or self.state.goal)
        repeated_focus = any(
            isinstance(artifact, Mapping)
            and artifact.get("type") == "independent_phase"
            and artifact.get("focus_digest") == focus_digest
            for artifact in self.state.artifacts.values()
        )
        if repeated_focus and not (
            anti_anchoring_reason is not None and anti_anchoring_reason.strip()
        ):
            raise ValueError(
                "restarting Independent→Discuss on the same focus requires a "
                "logged anti_anchoring_reason"
            )

        await self._enter_mode(
            CollaborationMode.INDEPENDENT_DISCUSS,
            sync_mode,
            selected,
        )
        packet = self.context_builder.build(self.state)
        shared_prompt = (
            packet.as_prompt()
            + "\n\nWork independently on the active focus. Do not assume or infer "
            "any peer submission. Return one self-contained structured alternative; "
            "your provisional submission remains sealed until the reveal barrier."
        )
        request = ModelRequest(
            messages=({"role": "user", "content": shared_prompt},),
            system=SEMANTIC_RESPONSE_INSTRUCTIONS,
            temperature=0.35,
            max_output_tokens=self.config.max_output_tokens_per_call,
        )
        requested = [
            (
                adapter.adapter_id,
                adapter,
                request,
                self.state.current_focus or self.state.goal,
                packet.state_version,
                packet.cognitive_revision,
            )
            for adapter in selected
            if adapter.adapter_id not in released_participant_ids
        ]
        admitted, rejections = await self._admit_calls(requested, round_number=1)
        completed_by_key, runtime_expired = await self._invoke_admitted(admitted)

        reveal_version = self.state.version + 1
        receipts: list[IndependentSubmissionReceipt] = []
        for adapter in selected:
            participant_id = adapter.adapter_id
            if participant_id in released_participant_ids:
                receipts.append(
                    IndependentSubmissionReceipt(
                        participant_id=participant_id,
                        base_state_version=packet.state_version,
                        base_cognitive_revision=packet.cognitive_revision,
                        content_digest=None,
                        status=SubmissionStatus.RELEASED,
                        error="Explicitly released from this independent phase.",
                    )
                )
                continue
            if participant_id in rejections:
                receipts.append(
                    IndependentSubmissionReceipt(
                        participant_id=participant_id,
                        base_state_version=packet.state_version,
                        base_cognitive_revision=packet.cognitive_revision,
                        content_digest=None,
                        status=SubmissionStatus.FAILED,
                        error=rejections[participant_id],
                    )
                )
                continue
            item = completed_by_key.get(participant_id)
            if item is None or item.response is None:
                if item is not None:
                    self._provider_failure_rounds.add(item.record.round_number)
                error = (
                    item.record.error
                    if item is not None
                    else "The task runtime deadline fired before submission."
                )
                status = (
                    SubmissionStatus.TIMED_OUT
                    if runtime_expired or "did not answer in time" in (error or "")
                    else SubmissionStatus.FAILED
                )
                receipts.append(
                    IndependentSubmissionReceipt(
                        participant_id=participant_id,
                        base_state_version=packet.state_version,
                        base_cognitive_revision=packet.cognitive_revision,
                        content_digest=None,
                        status=status,
                        error=error,
                    )
                )
                continue
            receipts.append(
                IndependentSubmissionReceipt(
                    participant_id=participant_id,
                    base_state_version=item.record.start_state_version,
                    base_cognitive_revision=item.record.start_cognitive_revision,
                    content_digest=canonical_digest(item.response.text),
                    status=SubmissionStatus.REVEALED,
                    revealed_state_version=reveal_version,
                )
            )

        normalized_alternatives: list[NormalizedAlternative] = []
        for receipt in receipts:
            if receipt.status is not SubmissionStatus.REVEALED:
                continue
            item = completed_by_key.get(receipt.participant_id)
            if item is None or item.response is None or not receipt.content_digest:
                continue
            try:
                contribution = self.extractor.extract(
                    item.response.text,
                    source=receipt.participant_id,
                    state_version=receipt.base_state_version,
                )
            except SemanticExtractionError:
                continue
            if not contribution.summary:
                continue
            normalized_alternatives.append(
                NormalizedAlternative(
                    participant_id=receipt.participant_id,
                    content_digest=receipt.content_digest,
                    summary=contribution.summary,
                    points=tuple(
                        AlternativePoint(
                            kind=event.event_type.value,
                            content=str(event.payload.get("content", "")),
                            severity=event.severity.value,
                            evidence_refs=tuple(
                                str(reference)
                                for reference in event.payload.get(
                                    "evidence_refs", ()
                                )
                            ),
                        )
                        for event in contribution.events
                        if event.payload.get("content")
                    ),
                    criterion_ids=tuple(
                        evidence["criterion_id"]
                        for evidence in contribution.criterion_evidence
                    ),
                    focus_resolved=contribution.focus_resolved,
                )
            )

        phase = IndependentPhaseRecord(
            focus_digest=focus_digest,
            context_digest=canonical_digest(shared_prompt),
            required_participant_ids=tuple(adapter.adapter_id for adapter in selected),
            receipts=tuple(receipts),
            reveal_state_version=reveal_version,
            alternatives=tuple(normalized_alternatives),
            anti_anchoring_reason=anti_anchoring_reason,
        )
        phase_record = phase.to_dict()
        if released_participant_ids:
            phase_record["release_authorizations"] = {
                participant_id: release_authorized_by
                for participant_id in sorted(released_participant_ids)
            }
        self._mode_trace.append(phase_record)
        # This is the reveal event.  No response text, summary, semantic event,
        # or criterion attestation is applied to canonical state before it.
        await self._apply(
            CognitiveEvent(
                event_type=EventType.ARTIFACT_UPDATE,
                source="collaboration-router",
                payload={
                    "artifact_id": f"independent-reveal:{reveal_version}",
                    **phase_record,
                },
                state_version=self.state.version,
            )
        )

        revealed_ids = set(phase.revealed_participant_ids)
        if not revealed_ids:
            for item in completed_by_key.values():
                await self._record_cost(item)
            return self._result(
                StopReason.RUNTIME_LIMIT
                if runtime_expired
                else self._provider_failure_stop_reason(1)
                or StopReason.BUDGET_OR_POLICY,
                1,
                False,
            )

        proposed_evidence: list[tuple[str, str, str]] = []
        outstanding = sum(
            call.reserved_api_cost
            for call in admitted
            if not call.adapter.capabilities().is_local
        )
        pending_cloud = sum(
            not call.adapter.capabilities().is_local for call in admitted
        )
        admitted_by_key = {call.call_key: call for call in admitted}
        for participant_id in sorted(completed_by_key):
            item = completed_by_key[participant_id]
            call = admitted_by_key[participant_id]
            current_reservation = call.reserved_api_cost
            try:
                if item.response is None:
                    await self._model_error(
                        participant_id,
                        "independent_generation",
                        item.record.error or "Unknown independent submission failure",
                        item.record.start_state_version,
                    )
                    await self._record_cost(item)
                    continue
                receipt = next(
                    value for value in receipts if value.participant_id == participant_id
                )
                if canonical_digest(item.response.text) != receipt.content_digest:
                    item.record.status = "failed"
                    item.record.error = "Sealed submission digest mismatch at reveal"
                    await self._model_error(
                        participant_id,
                        "independent_reveal",
                        item.record.error,
                        item.record.start_state_version,
                    )
                    await self._record_cost(item)
                    continue
                contribution = await self._integrate_response(
                    item,
                    call.request,
                    pending_cloud_calls=pending_cloud,
                    outstanding_api_reservation=outstanding,
                    current_api_reservation=current_reservation,
                )
                if contribution is None:
                    continue
                if contribution.summary:
                    await self._apply(
                        CognitiveEvent(
                            event_type=EventType.ALTERNATIVE,
                            source=participant_id,
                            payload={
                                "content": contribution.summary,
                                "phase": "independent_reveal",
                                "content_digest": receipt.content_digest,
                            },
                            state_version=self.state.version,
                        )
                    )
                if contribution.focus_resolved:
                    proposed_evidence.extend(
                        (
                            evidence["criterion_id"],
                            evidence["evidence"],
                            contribution.source,
                        )
                        for evidence in contribution.criterion_evidence
                    )
            finally:
                if not item.adapter.capabilities().is_local:
                    pending_cloud -= 1
                    outstanding = max(0.0, outstanding - current_reservation)

        if runtime_expired or self._remaining_runtime() == 0:
            return self._result(StopReason.RUNTIME_LIMIT, 1, False)

        # Discussion is executable, not merely a label: after the simultaneous
        # reveal, all participants receive the normalized alternatives through
        # canonical context and perform a Joint synthesis round.
        previous_addendum = self._round_prompt_addendum
        self._round_prompt_addendum = (
            "\n\n# Simultaneously revealed independent alternatives\n"
            + json.dumps(
                [item.to_dict() for item in phase.alternatives],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\nCompare, critique, and synthesize these alternatives. Treat each "
            "as provisional and resolve disputes through evidence."
        )
        try:
            synthesis = await self._run_once(
                mode=CollaborationMode.JOINT,
                sync_mode=sync_mode,
            )
        finally:
            self._round_prompt_addendum = previous_addendum
        latest_checkpoint = next(
            reversed(self.state.synchronization_checkpoints.values()), None
        )
        if latest_checkpoint is not None and latest_checkpoint.passed:
            await self._accept_criterion_evidence(
                proposed_evidence,
                checkpoint_id=latest_checkpoint.id,
            )
        reason = (
            StopReason.GOAL_SATISFIED
            if self.state.goal_satisfied
            else synthesis.stop_reason
        )
        return self._result(
            reason,
            synthesis.rounds_completed + 1,
            synthesis.synchronization_passed,
        )

    async def _run_parallel_decomposition(
        self,
        plan: DecompositionPlan,
        *,
        sync_mode: SyncMode,
    ) -> KernelResult:
        adapters_by_id = {adapter.adapter_id: adapter for adapter in self.adapters}
        unknown = sorted(
            {
                assignee
                for unit in plan.work_units
                for assignee in unit.assignee_ids
                if assignee not in adapters_by_id
            }
        )
        if unknown:
            raise ValueError(
                "decomposition plan references unknown assignees: "
                + ", ".join(unknown)
            )
        selected = tuple(
            adapters_by_id[identifier]
            for identifier in dict.fromkeys(
                unit.assignee_ids[0] for unit in plan.work_units
            )
        )
        required_selected = tuple(
            adapters_by_id[identifier]
            for identifier in dict.fromkeys(
                unit.assignee_ids[0] for unit in plan.work_units if unit.required
            )
        )
        await self._enter_mode(CollaborationMode.PARALLEL, sync_mode, selected)
        parent_packet = self.context_builder.build(self.state)
        initial_artifact_conflicts = self._artifact_version_conflicts(plan)
        private_results: list[_ParallelPrivateResult] = []
        completed_units: set[str] = set()
        levels = plan.topological_levels()
        contract = self.state.goal_contract
        level_limit = self.config.max_rounds
        if contract is not None and contract.max_iterations is not None:
            level_limit = min(level_limit, contract.max_iterations)
        runtime_fired = False

        for level_index, level in enumerate(levels, start=1):
            if level_index > level_limit:
                for unit in level:
                    private_results.append(
                        self._blocked_work_unit(
                            unit,
                            parent_packet,
                            "The configured iteration limit prevented dispatch.",
                        )
                    )
                continue
            dispatchable: list[WorkUnit] = []
            for unit in level:
                stale_artifacts = initial_artifact_conflicts.get(unit.id, ())
                if stale_artifacts:
                    private_results.append(
                        self._blocked_work_unit(
                            unit,
                            parent_packet,
                            "Artifact version precondition failed: "
                            + "; ".join(stale_artifacts),
                        )
                    )
                elif not set(unit.dependencies) <= completed_units:
                    private_results.append(
                        self._blocked_work_unit(
                            unit,
                            parent_packet,
                            "One or more dependency work units did not complete.",
                        )
                    )
                else:
                    dispatchable.append(unit)
            requested: list[
                tuple[str, ModelAdapter, ModelRequest, str, int, int]
            ] = []
            for unit in dispatchable:
                dependency_summaries = {
                    result.public_result.work_unit_id: result.public_result.summary
                    for result in private_results
                    if result.public_result.work_unit_id in unit.dependencies
                }
                request = self._parallel_request(
                    parent_packet.as_prompt(),
                    parent_packet.state_version,
                    unit,
                    dependency_summaries,
                )
                requested.append(
                    (
                        unit.id,
                        adapters_by_id[unit.assignee_ids[0]],
                        request,
                        unit.focus,
                        parent_packet.state_version,
                        parent_packet.cognitive_revision,
                    )
                )
            admitted, rejections = await self._admit_calls(
                requested,
                round_number=1,
            )
            completed_by_key, runtime_expired = await self._invoke_admitted(admitted)
            admitted_by_key = {call.call_key: call for call in admitted}
            for unit in dispatchable:
                if unit.id in rejections:
                    private_results.append(
                        self._blocked_work_unit(
                            unit, parent_packet, rejections[unit.id]
                        )
                    )
                    continue
                item = completed_by_key.get(unit.id)
                if item is None:
                    private_results.append(
                        self._blocked_work_unit(
                            unit,
                            parent_packet,
                            "The task runtime deadline fired before completion.",
                        )
                    )
                    continue
                private = await self._extract_parallel_result(
                    unit,
                    item,
                    admitted_by_key[unit.id].request,
                )
                # 05B Part B/C bounded corrective retry: a single malformed or
                # failed model response used to condemn the whole work unit
                # (and with it the integration level, turning the run
                # 'partial').  Re-dispatch the SAME bounded unit once.  When
                # the failure was a semantic-extraction failure — the model
                # answered in prose instead of the committee JSON interchange
                # — the retry shows the model its own verbatim reply plus an
                # explicit reformat instruction, because an identical
                # re-prompt deterministically reproduces the same prose.
                def _unit_reports_blocker(result: WorkUnitResult) -> bool:
                    # 05B/06: small local models occasionally emit a spurious
                    # 'blocker' or 'critical_conflict' event that trips the
                    # deterministic integration gate and hard-fails the run.
                    # One bounded re-ask is allowed before accepting the claim.
                    return bool(
                        result.status is WorkUnitStatus.COMPLETED
                        and getattr(result, "event_types", ())
                        and {"blocker", "critical_conflict"} & set(result.event_types)
                    )

                retry_used = False
                while (
                    (
                        private.public_result.status is WorkUnitStatus.FAILED
                        or _unit_reports_blocker(private.public_result)
                    )
                    and not runtime_expired
                    and not retry_used
                ):
                    retry_used = True
                    base_request = admitted_by_key[unit.id].request
                    first_error = str(private.public_result.error or "")
                    retry_request = base_request
                    if (
                        "Response is not valid JSON" in first_error
                        and item.response is not None
                        and item.response.text.strip()
                    ):
                        retry_request = replace(
                            base_request,
                            messages=(
                                {
                                    "role": "user",
                                    "content": (
                                        "Your previous reply was not in the required JSON "
                                        "interchange format. Here is your verbatim previous "
                                        "reply:\n\n"
                                        + item.response.text[:4000]
                                        + "\n\nConvert it now. Return ONLY the JSON object "
                                        "with keys summary, events, criterion_evidence, and "
                                        "focus_resolved, exactly as instructed. No prose "
                                        "outside the JSON."
                                    ),
                                },
                            ),
                        )
                    retry_admitted, _ = await self._admit_calls(
                        [(unit.id, admitted_by_key[unit.id].adapter, retry_request, unit.focus, parent_packet.state_version, parent_packet.cognitive_revision)],
                        round_number=1,
                    )
                    if not retry_admitted:
                        break
                    retry_completed, retry_expired = await self._invoke_admitted(
                        retry_admitted
                    )
                    runtime_expired = runtime_expired or retry_expired
                    retry_item = retry_completed.get(unit.id)
                    if retry_item is None:
                        break
                    private = await self._extract_parallel_result(
                        unit,
                        retry_item,
                        retry_admitted[0].request,
                    )
                private_results.append(private)
                if private.public_result.status is WorkUnitStatus.COMPLETED:
                    completed_units.add(unit.id)
            if runtime_expired:
                runtime_fired = True
                break

        present = {result.unit.id for result in private_results}
        for unit in plan.work_units:
            if unit.id not in present:
                private_results.append(
                    self._blocked_work_unit(
                        unit,
                        parent_packet,
                        "Execution stopped before this work unit became ready.",
                    )
                )
        public_results = tuple(result.public_result for result in private_results)
        report = deterministic_integration_report(plan, public_results)
        if self.integration_evaluator is not None:
            try:
                custom_report = self._evaluate_integration(plan, public_results)
            except Exception as exc:
                await self._model_error(
                    "criterion-evaluator",
                    "parallel_integration_evaluation",
                    f"{type(exc).__name__}: {exc}",
                    self.state.version,
                )
                custom_report = IntegrationReport(
                    passed=False,
                    criterion_results={"custom_integration_evaluator": False},
                    conflicts=("custom integration evaluator failed",),
                    rationale="The host integration evaluator failed closed.",
                    evaluator=getattr(
                        self.integration_evaluator,
                        "__qualname__",
                        type(self.integration_evaluator).__name__,
                    ),
                )
            report = IntegrationReport(
                passed=report.passed and custom_report.passed,
                criterion_results={
                    **dict(report.criterion_results),
                    **dict(custom_report.criterion_results),
                },
                conflicts=(*report.conflicts, *custom_report.conflicts),
                rationale=" ".join(
                    value
                    for value in (report.rationale, custom_report.rationale)
                    if value
                ),
                evaluator=(
                    f"{report.evaluator}+{custom_report.evaluator}"
                ),
            )
        final_artifact_conflicts = self._artifact_version_conflicts(plan)
        if final_artifact_conflicts:
            flattened = tuple(
                conflict
                for unit_id in sorted(final_artifact_conflicts)
                for conflict in final_artifact_conflicts[unit_id]
            )
            report = IntegrationReport(
                passed=False,
                criterion_results={
                    **dict(report.criterion_results),
                    "artifact_versions_current": False,
                },
                conflicts=(*report.conflicts, *flattened),
                rationale="Artifact versions changed or did not match the pinned plan.",
                evaluator=report.evaluator,
            )
        else:
            report = IntegrationReport(
                passed=report.passed,
                criterion_results={
                    **dict(report.criterion_results),
                    "artifact_versions_current": True,
                },
                conflicts=report.conflicts,
                rationale=report.rationale,
                evaluator=report.evaluator,
            )
        if runtime_fired:
            report = IntegrationReport(
                passed=False,
                criterion_results={
                    **dict(report.criterion_results),
                    "runtime_deadline_respected": False,
                },
                conflicts=(
                    *report.conflicts,
                    "the task runtime deadline fired before integration",
                ),
                rationale="Parallel results remain provisional because runtime expired.",
                evaluator=report.evaluator,
            )

        contributions = tuple(
            result.contribution
            for result in private_results
            if result.contribution is not None
        )
        batch_error = self._prevalidate_parallel_batch(contributions)
        if batch_error is not None:
            report = IntegrationReport(
                passed=False,
                criterion_results={
                    **dict(report.criterion_results),
                    "atomic_state_reduction": False,
                },
                conflicts=(*report.conflicts, batch_error),
                rationale="Parallel results could not be atomically integrated.",
                evaluator=report.evaluator,
            )

        parallel_record: dict[str, object] = {
            "type": "parallel_decomposition",
            "plan_digest": plan.digest,
            "pinned_parent_state_version": parent_packet.state_version,
            "topological_levels": [
                [unit.id for unit in level] for level in levels
            ],
            "results": [result.to_dict() for result in public_results],
            "integration": report.to_dict(),
        }
        self._mode_trace.append(parallel_record)
        await self._apply(
            CognitiveEvent(
                event_type=EventType.ARTIFACT_UPDATE,
                source="collaboration-router",
                payload={
                    "artifact_id": f"parallel-integration:{self.state.version + 1}",
                    **parallel_record,
                },
                state_version=self.state.version,
            )
        )

        proposed_evidence: list[tuple[str, str, str]] = []
        if report.passed:
            for private in private_results:
                contribution = private.contribution
                if contribution is None:
                    continue
                for event in contribution.events:
                    event.state_version = min(event.state_version, self.state.version)
                    await self._apply(event)
                    if event.event_type is EventType.TOOL_REQUEST:
                        await self._execute_tool_request(event)
                if private.call is not None:
                    private.call.record.integrated_state_version = self.state.version
                if contribution.focus_resolved:
                    proposed_evidence.extend(
                        (
                            evidence["criterion_id"],
                            evidence["evidence"],
                            contribution.source,
                        )
                        for evidence in contribution.criterion_evidence
                    )
        else:
            await self._apply(
                CognitiveEvent(
                    event_type=EventType.BLOCKER,
                    source="collaboration-router",
                    payload={
                        "content": report.rationale,
                        "claim": "parallel integration",
                        "proposed_check": "Jointly review the integration conflicts.",
                        "conflicts": list(report.conflicts),
                    },
                    state_version=self.state.version,
                    severity=Severity.HIGH,
                )
            )

        checkpoint = await self._record_synchronization_checkpoint(
            sync_mode,
            required_selected,
            [
                result.call
                for result in private_results
                if result.call is not None
                and result.public_result.status is WorkUnitStatus.COMPLETED
            ],
            round_number=1,
            gate_allows=report.passed,
            gate_reason=(
                "parallel integration criteria passed"
                if report.passed
                else report.rationale
            ),
        )
        if checkpoint.passed:
            await self._accept_criterion_evidence(
                proposed_evidence,
                checkpoint_id=checkpoint.id,
            )
        if not report.passed:
            await self._apply(
                CognitiveEvent(
                    event_type=EventType.MODE_SWITCH,
                    source="collaboration-router",
                    payload={
                        "mode": CollaborationMode.JOINT.value,
                        "participating_models": [
                            adapter.adapter_id for adapter in selected
                        ],
                        "reason": "Parallel integration conflict",
                    },
                    state_version=self.state.version,
                )
            )
            return self._result(
                (
                    StopReason.RUNTIME_LIMIT
                    if runtime_fired
                    else StopReason.INTEGRATION_BLOCKED
                ),
                min(len(levels), level_limit),
                False,
            )
        reason = (
            StopReason.GOAL_SATISFIED
            if self.state.goal_satisfied
            else (
                StopReason.ROUND_LIMIT
                if checkpoint.passed
                else StopReason.SYNCHRONIZATION_BLOCKED
            )
        )
        return self._result(
            reason,
            min(len(levels), level_limit),
            checkpoint.passed,
        )

    async def _enter_mode(
        self,
        mode: CollaborationMode,
        sync_mode: SyncMode,
        selected: Sequence[ModelAdapter],
    ) -> None:
        await self._apply(
            CognitiveEvent(
                event_type=EventType.MODE_SWITCH,
                source="collaboration-router",
                payload={
                    "mode": mode.value,
                    "participating_models": [
                        adapter.adapter_id for adapter in selected
                    ],
                },
                state_version=self.state.version,
            )
        )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.SYNC_CHANGE,
                source="synchronization-router",
                payload={"mode": sync_mode.value},
                state_version=self.state.version,
            )
        )
        if self.state_manager.store is not None:
            self.state_manager.save()

    async def _admit_calls(
        self,
        requested: Sequence[
            tuple[str, ModelAdapter, ModelRequest, str, int, int]
        ],
        *,
        round_number: int,
    ) -> tuple[list[_AdmittedCall], dict[str, str]]:
        admitted: list[_AdmittedCall] = []
        rejections: dict[str, str] = {}
        aggregate_reservation = 0.0
        remaining_frontier_calls = self.cost_controller.remaining_frontier_calls(
            self.state
        )
        budget = (
            self.state.goal_contract.max_api_cost_usd
            if self.state.goal_contract
            else None
        )
        for (
            call_key,
            adapter,
            request,
            focus,
            base_state_version,
            base_cognitive_revision,
        ) in requested:
            is_local = adapter.capabilities().is_local
            if not is_local and remaining_frontier_calls <= 0:
                reason = (
                    "The atomic mode phase would exceed the compute policy "
                    "frontier-call limit."
                )
                rejections[call_key] = reason
                await self._model_error(
                    adapter.adapter_id, "preflight", reason, self.state.version
                )
                continue
            admission = self.cost_controller.admit(
                self.state,
                adapter,
                estimated_input_tokens=adapter.estimate_tokens(request),
                estimated_output_tokens=request.max_output_tokens or 0,
            )
            if not admission.allowed:
                rejections[call_key] = admission.reason
                await self._model_error(
                    adapter.adapter_id,
                    "preflight",
                    admission.reason,
                    self.state.version,
                )
                continue
            reservation = 0.0 if is_local else admission.estimated_cost_usd
            if (
                budget is not None
                and _budget_blocks(self.state)
                and self.state.cost_state.api_cost_usd
                + aggregate_reservation
                + reservation
                > budget
            ):
                reason = "Atomic mode phase reservation would exceed the API budget."
                rejections[call_key] = reason
                await self._model_error(
                    adapter.adapter_id, "preflight", reason, self.state.version
                )
                continue
            record = ModelCallRecord(
                adapter_id=adapter.adapter_id,
                round_number=round_number,
                start_state_version=base_state_version,
                start_cognitive_revision=base_cognitive_revision,
                focus=focus,
            )
            if not self._reserve_durable_model_call(
                adapter,
                record,
                estimated_api_cost_usd=reservation,
                enforce_budget=_budget_blocks(self.state),
            ):
                reason = (
                    "Durable atomic mode reservation would exceed the API budget."
                )
                rejections[call_key] = reason
                await self._model_error(
                    adapter.adapter_id, "preflight", reason, self.state.version
                )
                continue
            aggregate_reservation += reservation
            if not is_local:
                remaining_frontier_calls -= 1
            self._call_records.append(record)
            admitted.append(
                _AdmittedCall(call_key, adapter, request, record, reservation)
            )
        return admitted, rejections

    async def _invoke_admitted(
        self, admitted: Sequence[_AdmittedCall]
    ) -> tuple[dict[str, _CompletedCall], bool]:
        # 05B Part B: bounded launch staggering.  Two local models starting on
        # one small GPU/CPU at the same instant contend for the weights load and
        # the inference queue, which surfaced as spurious participant timeouts.
        # Stagger the starts (15s per additional participant, capped at 45s)
        # while keeping the branches logically concurrent - admission smoothing,
        # not sequential execution.
        tasks = {}
        for index, call in enumerate(admitted):
            if index:
                await asyncio.sleep(min(15.0 * index, 45.0))
            tasks[asyncio.create_task(
                self._invoke(call.adapter, call.request, call.record)
            )] = call.call_key
        runtime_expired = False
        try:
            if not tasks:
                return {}, False
            remaining = self._remaining_runtime()
            if remaining == 0:
                runtime_expired = True
                return {}, True
            gather = asyncio.gather(*tasks)
            try:
                completed = (
                    await gather
                    if remaining is None
                    else await asyncio.wait_for(gather, timeout=remaining)
                )
            except TimeoutError:
                runtime_expired = True
                partial: dict[str, _CompletedCall] = {}
                for task, call_key in tasks.items():
                    if task.done() and not task.cancelled():
                        try:
                            partial[call_key] = task.result()
                        except Exception:
                            # _invoke normally normalizes provider failures, but
                            # a defensive guard keeps one unexpected task failure
                            # from hiding other completed sealed submissions.
                            continue
                return partial, True
            return {
                tasks[task]: result
                for task, result in zip(tasks, completed, strict=True)
            }, runtime_expired
        finally:
            admitted_by_key = {call.call_key: call for call in admitted}
            for task, call_key in tasks.items():
                if not task.done():
                    call = admitted_by_key[call_key]
                    call.record.status = "cancelled"
                    call.record.staleness = StalenessDisposition.FAILED
                    call.record.error = "Goal Contract runtime limit cancelled the call"
                    if not call.adapter.capabilities().is_local:
                        call.record.usage_uncertain = True
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def _parallel_request(
        self,
        parent_prompt: str,
        parent_state_version: int,
        unit: WorkUnit,
        dependency_summaries: Mapping[str, str],
    ) -> ModelRequest:
        unit_data = unit.to_dict()
        work_packet = {
            "work_unit_id": unit.id,
            "pinned_parent_state_version": parent_state_version,
            "focus": unit.focus,
            "inputs": unit_data["inputs"],
            "dependencies": list(unit.dependencies),
            "dependency_summaries": dict(dependency_summaries),
            "output_contract": unit_data["output_contract"],
            "integration_owner": unit.integration_owner,
            "declared_assumptions": unit_data["assumptions"],
            "artifact_owners": unit_data["artifact_owners"],
            "artifact_base_versions": unit_data["artifact_base_versions"],
        }
        return ModelRequest(
            messages=(
                {
                    "role": "user",
                    "content": (
                        parent_prompt
                        + "\n\n# Parallel work unit\n"
                        + json.dumps(
                            work_packet,
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\nAdvance only this bounded work unit. Do not claim that the "
                        "parent task is integrated or complete."
                    ),
                },
            ),
            system=SEMANTIC_RESPONSE_INSTRUCTIONS,
            temperature=0.2,
            max_output_tokens=self.config.max_output_tokens_per_call,
        )

    def _artifact_version_conflicts(
        self, plan: DecompositionPlan
    ) -> dict[str, tuple[str, ...]]:
        conflicts: dict[str, tuple[str, ...]] = {}
        for unit in plan.work_units:
            unit_conflicts: list[str] = []
            for artifact_id, expected in unit.artifact_base_versions.items():
                stored = self.state.artifacts.get(artifact_id)
                if stored is None:
                    actual: object = 0
                elif isinstance(stored, Mapping):
                    actual = stored.get("version", 0)
                else:
                    actual = 0
                if (
                    isinstance(actual, bool)
                    or not isinstance(actual, int)
                    or actual != expected
                ):
                    unit_conflicts.append(
                        f"{artifact_id!r} expected version {expected}, found {actual!r}"
                    )
            if unit_conflicts:
                conflicts[unit.id] = tuple(unit_conflicts)
        return conflicts

    async def _extract_parallel_result(
        self,
        unit: WorkUnit,
        item: _CompletedCall,
        request: ModelRequest,
    ) -> _ParallelPrivateResult:
        del request  # Retained in the signature for future unit-specific rebase.
        if item.response is None:
            self._provider_failure_rounds.add(item.record.round_number)
            await self._model_error(
                item.adapter.adapter_id,
                "parallel_generation",
                item.record.error or "Unknown work-unit generation failure",
                item.record.start_state_version,
            )
            await self._record_cost(item)
            return _ParallelPrivateResult(
                unit,
                item,
                None,
                WorkUnitResult(
                    work_unit_id=unit.id,
                    participant_id=item.adapter.adapter_id,
                    status=WorkUnitStatus.FAILED,
                    base_state_version=item.record.start_state_version,
                    base_cognitive_revision=item.record.start_cognitive_revision,
                    error=item.record.error,
                ),
            )
        try:
            contribution = self.extractor.extract(
                item.response.text,
                source=item.adapter.adapter_id,
                state_version=item.record.start_state_version,
            )
        except SemanticExtractionError as exc:
            item.record.status = "failed"
            item.record.error = str(exc)
            item.record.staleness = StalenessDisposition.FAILED
            await self._apply(
                self.extractor.extraction_error_event(
                    exc,
                    source=item.adapter.adapter_id,
                    state_version=min(
                        item.record.start_state_version, self.state.version
                    ),
                )
            )
            await self._record_cost(item)
            return _ParallelPrivateResult(
                unit,
                item,
                None,
                WorkUnitResult(
                    work_unit_id=unit.id,
                    participant_id=item.adapter.adapter_id,
                    status=WorkUnitStatus.FAILED,
                    base_state_version=item.record.start_state_version,
                    base_cognitive_revision=item.record.start_cognitive_revision,
                    content_digest=canonical_digest(item.response.text),
                    error=str(exc),
                ),
            )
        await self._audit_dropped_semantic_events(
            contribution,
            item.adapter.adapter_id,
            item.record.start_state_version,
        )
        rejection, contribution = self._prevalidate_contribution(contribution)
        if rejection is not None:
            item.record.status = "failed"
            item.record.error = rejection
            item.record.staleness = StalenessDisposition.FAILED
            await self._model_error(
                item.adapter.adapter_id,
                "parallel_state_reduction",
                rejection,
                item.record.start_state_version,
            )
            await self._record_cost(item)
            return _ParallelPrivateResult(
                unit,
                item,
                None,
                WorkUnitResult(
                    work_unit_id=unit.id,
                    participant_id=item.adapter.adapter_id,
                    status=WorkUnitStatus.FAILED,
                    base_state_version=item.record.start_state_version,
                    base_cognitive_revision=item.record.start_cognitive_revision,
                    content_digest=canonical_digest(item.response.text),
                    error=rejection,
                ),
            )
        item.record.status = "complete"
        item.record.summary = contribution.summary
        item.record.focus_resolved = contribution.focus_resolved
        item.record.staleness = StalenessDisposition.CURRENT
        item.record.cognitive_lag = max(
            0,
            self.state.cognitive_revision - item.record.start_cognitive_revision,
        )
        await self._record_cost(item)
        return _ParallelPrivateResult(
            unit,
            item,
            contribution,
            WorkUnitResult(
                work_unit_id=unit.id,
                participant_id=item.adapter.adapter_id,
                status=WorkUnitStatus.COMPLETED,
                base_state_version=item.record.start_state_version,
                base_cognitive_revision=item.record.start_cognitive_revision,
                content_digest=canonical_digest(item.response.text),
                summary=contribution.summary,
                event_types=tuple(
                    event.event_type.value for event in contribution.events
                ),
                focus_resolved=contribution.focus_resolved,
                # Artifact claims are not inferred from prose.  A contract that
                # requires artifacts stays blocked until a typed artifact result
                # protocol supplies them.
                artifact_ids=(),
            ),
        )

    @staticmethod
    def _blocked_work_unit(
        unit: WorkUnit,
        parent_packet: object,
        reason: str,
    ) -> _ParallelPrivateResult:
        return _ParallelPrivateResult(
            unit,
            None,
            None,
            WorkUnitResult(
                work_unit_id=unit.id,
                participant_id=unit.assignee_ids[0],
                status=WorkUnitStatus.BLOCKED,
                base_state_version=int(getattr(parent_packet, "state_version")),
                base_cognitive_revision=int(
                    getattr(parent_packet, "cognitive_revision")
                ),
                error=reason,
            ),
        )

    def _evaluate_integration(
        self,
        plan: DecompositionPlan,
        results: Sequence[WorkUnitResult],
    ) -> IntegrationReport:
        assert self.integration_evaluator is not None
        evaluation = self.integration_evaluator(self.state, plan, results)
        evaluator = getattr(
            self.integration_evaluator,
            "__qualname__",
            type(self.integration_evaluator).__name__,
        )
        if isinstance(evaluation, IntegrationReport):
            return evaluation
        if isinstance(evaluation, bool):
            return IntegrationReport(
                passed=evaluation,
                criterion_results={"custom_integration_evaluator": evaluation},
                conflicts=() if evaluation else ("custom integration evaluator failed",),
                rationale=(
                    "Custom integration evaluator passed."
                    if evaluation
                    else "Custom integration evaluator blocked integration."
                ),
                evaluator=evaluator,
            )
        if isinstance(evaluation, str) and evaluation.strip():
            return IntegrationReport(
                passed=True,
                criterion_results={"custom_integration_evaluator": True},
                rationale=evaluation.strip(),
                evaluator=evaluator,
            )
        raise TypeError(
            "integration_evaluator must return IntegrationReport, bool, or a "
            "non-empty rationale string"
        )

    def _prevalidate_parallel_batch(
        self, contributions: Sequence[SemanticContribution]
    ) -> str | None:
        preview = self.state
        for contribution in contributions:
            for event in contribution.events:
                preview_event = CognitiveEvent.from_dict(event.to_dict())
                preview_event.state_version = min(
                    preview_event.state_version, preview.version
                )
                try:
                    preview = StateManager.reduce(preview, preview_event)
                except (KeyError, ValueError) as exc:
                    return (
                        "Atomic parallel batch rejected: "
                        f"{type(exc).__name__}: {exc}"
                    )
        return None

    def _reconcile_expired_model_calls(self) -> None:
        store = self.state_manager.store
        reconcile = getattr(store, "reconcile_expired_model_calls", None)
        if callable(reconcile):
            reconcile(project_id=self.state.project_id)

    def _reconcile_current_model_call_owner(self) -> None:
        owner_id = self._reservation_owner_id
        if owner_id is None:
            return
        store = self.state_manager.store
        reconcile = getattr(store, "reconcile_model_call_owner", None)
        if callable(reconcile):
            reconcile(
                owner_id,
                reason="kernel run ended before authoritative cost settlement",
            )

    async def _close_expired_checkpoint_on_resume(self) -> None:
        checkpoint_id = self.state.active_checkpoint_id
        if checkpoint_id is None:
            return
        checkpoint = self.state.synchronization_checkpoint(checkpoint_id)
        if checkpoint is None or checkpoint.status is not CheckpointStatus.OPEN:
            return
        now = utc_now()
        if not checkpoint.is_expired(now):
            raise RuntimeError(
                "an unexpired synchronization checkpoint is already active"
            )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.SYNC_CHECKPOINT_CLOSED,
                source="synchronization-controller",
                payload={
                    "checkpoint_id": checkpoint.id,
                    "passed": False,
                    "reason": "checkpoint deadline expired before restart recovery",
                },
                state_version=self.state.version,
                created_at=now,
            )
        )

    def _reserve_durable_model_call(
        self,
        adapter: ModelAdapter,
        record: ModelCallRecord,
        *,
        estimated_api_cost_usd: float,
        enforce_budget: bool = True,
    ) -> bool:
        reserve = getattr(self.state_manager.store, "reserve_model_call", None)
        if not callable(reserve):
            return True
        owner_id = self._reservation_owner_id
        if owner_id is None:
            raise RuntimeError("model calls require an active reservation owner")
        reservation_id = uuid4().hex
        # 28B: telemetry mode passes no budget so reservations record cost
        # without ever failing closed on budget.
        budget = (
            self.state.goal_contract.max_api_cost_usd
            if enforce_budget and self.state.goal_contract is not None
            else None
        )
        try:
            reservation = reserve(
                reservation_id=reservation_id,
                project_id=self.state.project_id,
                owner_id=owner_id,
                adapter_id=adapter.adapter_id,
                is_api=not adapter.capabilities().is_local,
                estimated_cost_usd=estimated_api_cost_usd,
                recorded_api_cost_usd=self.state.cost_state.api_cost_usd,
                budget_usd=budget,
                lease_seconds=self.config.model_reservation_lease_seconds,
            )
        except BudgetReservationError:
            return False
        record.reservation_id = reservation.id
        record.reserved_api_cost_usd = reservation.estimated_cost_usd
        return True

    def _increase_durable_model_call_reservation(
        self, record: ModelCallRecord, *, additional_estimated_api_cost_usd: float
    ) -> bool:
        if record.reservation_id is None:
            return True
        increase = getattr(
            self.state_manager.store, "increase_model_call_reservation", None
        )
        if not callable(increase):
            return True
        budget = (
            self.state.goal_contract.max_api_cost_usd
            if _budget_blocks(self.state) and self.state.goal_contract is not None
            else None
        )
        try:
            reservation = increase(
                record.reservation_id,
                additional_estimated_cost_usd=additional_estimated_api_cost_usd,
                recorded_api_cost_usd=self.state.cost_state.api_cost_usd,
                budget_usd=budget,
            )
        except BudgetReservationError:
            return False
        record.reserved_api_cost_usd = reservation.estimated_cost_usd
        return True

    def _settle_durable_model_call(self, item: _CompletedCall) -> None:
        reservation_id = item.record.reservation_id
        if reservation_id is None:
            return
        store = self.state_manager.store
        if (
            (item.response is None or item.record.usage_uncertain)
            and not item.adapter.capabilities().is_local
        ):
            reconcile = getattr(store, "reconcile_model_call", None)
            if callable(reconcile):
                reconcile(
                    reservation_id,
                    reason="provider call ended without authoritative usage",
                )
            return
        settle = getattr(store, "settle_model_call", None)
        if callable(settle):
            settle(
                reservation_id,
                actual_cost_usd=(
                    item.record.cost_usd
                    if not item.adapter.capabilities().is_local
                    else 0.0
                ),
            )

    def _remaining_runtime(self) -> float | None:
        deadline = getattr(self, "_session_deadline", None)
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def _provider_failure_stop_reason(self, round_number: int) -> StopReason | None:
        """Classify an empty round: a provider that failed is not a budget or
        policy decision.  Only admitted calls that returned no response are
        tracked, so an empty round caused by admission rejections still
        reports budget_or_policy."""

        if round_number in self._provider_failure_rounds:
            return StopReason.PROVIDER_FAILURE
        return None

    async def _run_round(
        self, adapters: Sequence[ModelAdapter], round_number: int
    ) -> tuple[list[_CompletedCall], list[tuple[str, str, str]]]:
        packet = self.context_builder.build(self.state)
        base_prompt = packet.as_prompt()
        staged_addendum = ""
        if self.round_prompt_addendum is not None:
            staged_addendum = self.round_prompt_addendum(round_number, self.state)
            if not isinstance(staged_addendum, str):
                raise TypeError("round_prompt_addendum must return a string")
        # H1.2-B (Solo direct answer): a solo first round is a direct user
        # conversation. The user's message is delivered verbatim with a
        # direct-answer instruction so the reply IS the user-facing answer.
        # Cancellation semantics are unchanged (the same await points exist).
        solo_direct = (
            getattr(self, "_active_mode", None) is CollaborationMode.SOLO
            and round_number == 1
            and len(adapters) == 1
            and not self._round_prompt_addendum
            and not staged_addendum
        )
        self._solo_direct_round = bool(solo_direct)
        system_prompt = (
            "You are AMCA Studio's assistant. Answer the user's message "
            "directly and concisely. When the user asks you to reply with "
            "an exact text, reply with exactly that text and nothing else."
        ) if solo_direct else SEMANTIC_RESPONSE_INSTRUCTIONS
        user_content = (
            (self.state.goal or "").strip() or base_prompt
        ) if solo_direct else (
            base_prompt
            + "\n\nContribute to the same active focus. React to the current "
            "shared conclusions and objections; do not silently change focus."
            + staged_addendum
        )
        request = ModelRequest(
            messages=({"role": "user", "content": user_content},),
            system=system_prompt,
            temperature=0.2,
            max_output_tokens=self.config.max_output_tokens_per_call,
        )

        eligible: list[tuple[ModelAdapter, ModelCallRecord]] = []
        reserved_api_costs: dict[str, float] = {}
        aggregate_reservation = 0.0
        remaining_frontier_calls = self.cost_controller.remaining_frontier_calls(
            self.state
        )
        budget = self.state.goal_contract.max_api_cost_usd if self.state.goal_contract else None
        for adapter in adapters:
            is_local = adapter.capabilities().is_local
            if not is_local:
                if remaining_frontier_calls <= 0:
                    await self._model_error(
                        adapter.adapter_id,
                        "preflight",
                        "The atomic round would exceed the compute policy frontier-call limit.",
                        self.state.version,
                    )
                    continue
            input_estimate = adapter.estimate_tokens(request)
            admission = self.cost_controller.admit(
                self.state,
                adapter,
                estimated_input_tokens=input_estimate,
                estimated_output_tokens=request.max_output_tokens or 0,
            )
            if not admission.allowed:
                await self._model_error(
                    adapter.adapter_id,
                    "preflight",
                    admission.reason,
                    self.state.version,
                )
                continue
            reserved_cost = 0.0 if is_local else admission.estimated_cost_usd
            aggregate_reservation += reserved_cost
            if (
                budget is not None
                and _budget_blocks(self.state)
                and self.state.cost_state.api_cost_usd + aggregate_reservation > budget
            ):
                await self._model_error(
                    adapter.adapter_id,
                    "preflight",
                    "Atomic round reservation would exceed the API budget.",
                    self.state.version,
                )
                aggregate_reservation -= reserved_cost
                continue
            record = ModelCallRecord(
                adapter_id=adapter.adapter_id,
                round_number=round_number,
                start_state_version=packet.state_version,
                start_cognitive_revision=packet.cognitive_revision,
                focus=self.state.current_focus or self.state.goal,
            )
            if not self._reserve_durable_model_call(
                adapter,
                record,
                estimated_api_cost_usd=reserved_cost,
                enforce_budget=_budget_blocks(self.state),
            ):
                aggregate_reservation -= reserved_cost
                await self._model_error(
                    adapter.adapter_id,
                    "preflight",
                    "Durable atomic round reservation would exceed the API budget.",
                    self.state.version,
                )
                continue
            if not is_local:
                remaining_frontier_calls -= 1
            self._call_records.append(record)
            eligible.append((adapter, record))
            reserved_api_costs[adapter.adapter_id] = reserved_cost

        tasks = [
            asyncio.create_task(self._invoke(adapter, request, record))
            for adapter, record in eligible
        ]
        try:
            completed: list[_CompletedCall] = []
            criterion_evidence: list[tuple[str, str, str]] = []
            pending_cloud_calls = sum(
                not adapter.capabilities().is_local for adapter, _ in eligible
            )
            outstanding_api_reservation = aggregate_reservation
            for future in asyncio.as_completed(tasks):
                item = await future
                current_api_reservation = reserved_api_costs[item.adapter.adapter_id]
                try:
                    if item.response is None:
                        self._provider_failure_rounds.add(round_number)
                        await self._model_error(
                            item.adapter.adapter_id,
                            "generation",
                            item.record.error or "Unknown model failure",
                            item.record.start_state_version,
                        )
                        await self._record_cost(item)
                        continue
                    contribution = await self._integrate_response(
                        item,
                        request,
                        pending_cloud_calls=pending_cloud_calls,
                        outstanding_api_reservation=outstanding_api_reservation,
                        current_api_reservation=current_api_reservation,
                    )
                    if contribution is None:
                        continue
                    completed.append(item)
                    if contribution.focus_resolved:
                        criterion_evidence.extend(
                            (
                                evidence["criterion_id"],
                                evidence["evidence"],
                                contribution.source,
                            )
                            for evidence in contribution.criterion_evidence
                        )
                finally:
                    if not item.adapter.capabilities().is_local:
                        pending_cloud_calls -= 1
                        outstanding_api_reservation = max(
                            0.0,
                            outstanding_api_reservation - current_api_reservation,
                        )
            return completed, criterion_evidence
        finally:
            # A cancelled/failed run must not release the run lock while its
            # provider tasks continue in the background and overlap a later run.
            for task, (adapter, record) in zip(tasks, eligible, strict=True):
                if not task.done():
                    record.status = "cancelled"
                    record.staleness = StalenessDisposition.FAILED
                    record.error = "Goal Contract runtime limit cancelled the call"
                    if not adapter.capabilities().is_local:
                        record.usage_uncertain = True
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _invoke(
        self,
        adapter: ModelAdapter,
        request: ModelRequest,
        record: ModelCallRecord,
    ) -> _CompletedCall:
        started = time.perf_counter()
        call_key = self._durable_call_key(adapter, record)
        try:
            remaining = self._remaining_runtime()
            call_timeout = self.config.model_timeout_seconds
            if remaining is not None:
                call_timeout = min(call_timeout, remaining)
            if call_timeout <= 0:
                raise TimeoutError("Goal Contract runtime limit exhausted")
            effective_request = replace(request, timeout_seconds=call_timeout)
            committed = self._reuse_committed_turn(call_key)
            if committed is not None:
                # A2.1D continuation: a durably committed turn is reused
                # verbatim; the provider is never called again.
                record.status = "reviewing"
                record.cost_usd = committed["cost"]
                record.input_tokens = 0
                record.output_tokens = 0
                usage = type(
                    "Usage",
                    (),
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": committed["cost"],
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                )()
                response = type(
                    "Response",
                    (),
                    {
                        "text": committed["payload"],
                        "usage": usage,
                        "finish_reason": "stop",
                        "model": getattr(adapter, "model", ""),
                        "provider": getattr(adapter, "provider", ""),
                    },
                )()
                return _CompletedCall(adapter, response, record)
            self._journal_kernel_call(call_key, "DISPATCH_INTENT_DURABLE", adapter)
            self._journal_kernel_call(call_key, "DISPATCHED", adapter)
            response = await asyncio.wait_for(
                self._collect_stream(adapter, effective_request),
                timeout=call_timeout,
            )
            self._journal_kernel_call(
                call_key,
                "RESPONSE_RECEIVED",
                adapter,
                cost=float(response.usage.cost_usd or 0.0),
            )
            record.status = "reviewing"
            record.response_state_version = self.state.version
            record.input_tokens = response.usage.input_tokens
            record.output_tokens = response.usage.output_tokens
            record.cost_usd = response.usage.cost_usd
            # Keep the provider's own facts about this call in the run record
            # (for example the governed tool calls the provider asked for
            # that the iteration budget did not allow to run), so the record
            # can never read as if the run completed work it skipped.
            record.provider_metadata = dict(
                getattr(response, "provider_metadata", {}) or {}
            )
            self._journal_kernel_call(
                call_key,
                "RESPONSE_COMMITTED",
                adapter,
                cost=float(response.usage.cost_usd or 0.0),
                payload=str(response.text or "")[:2000],
            )
            return _CompletedCall(adapter, response, record)
        except _KernelTurnUncertain:
            raise
        except Exception as exc:
            record.status = "failed"
            record.staleness = StalenessDisposition.FAILED
            record.error = _public_model_error(exc)
            # The transcript keeps only the public text, but operators keep
            # the full chain: journal the internal detail (adapter class,
            # HTTP code, transport cause) for diagnosis.  No secrets travel
            # here — adapter messages carry status codes, never keys.
            self._journal_kernel_call(
                call_key,
                "MODEL_CALL_FAILED",
                adapter,
                cost=0.0,
                payload=f"{type(exc).__name__}: {exc}"[:2000],
            )
            if not adapter.capabilities().is_local:
                record.usage_uncertain = True
            return _CompletedCall(adapter, None, record)
        finally:
            record.elapsed_seconds += time.perf_counter() - started

    def _durable_call_key(self, adapter: ModelAdapter, record: ModelCallRecord) -> str | None:
        """Deterministic semantic identity for this kernel invocation."""

        ctx = self.journal_context
        if not ctx or self.recovery_store is None:
            return None
        architecture = str(ctx.get("architecture") or "joint").strip().lower()
        phase = int(ctx.get("phase", 1) or 1)
        round_number = int(getattr(record, "round_number", 0) or 0)
        participant = (
            getattr(adapter, "adapter_id", "") or str(record.adapter_id or "")
        )
        if architecture in {"adaptive_hybrid", "adaptive"}:
            # Live adaptive position: the kernel's current bounded phase
            # disambiguates repeated step numbers across phase transitions.
            adaptive_phase = self._journal_phase or phase
            return (
                f"adaptive:phase:{adaptive_phase}"
                f":step:{round_number}:participant:{participant}"
            )
        if architecture in {"independent_discuss", "independent_discussion"}:
            return (
                f"independent:phase:{phase}:round:{round_number}"
                f":participant:{participant}"
            )
        if architecture in {"adaptive_hybrid", "adaptive"}:
            return (
                f"adaptive:phase:{phase}:step:{round_number}"
                f":participant:{participant}"
            )
        return f"joint:phase:{phase}:round:{round_number}:participant:{participant}"

    def _journal_identity(self) -> tuple[str, str, int] | None:
        ctx = self.journal_context
        organization_id = str(ctx.get("organization_id") or "")
        task_id = str(ctx.get("task_id") or "")
        attempt = int(ctx.get("attempt", 1) or 1)
        if not organization_id or not task_id:
            return None
        return organization_id, task_id, attempt

    def _journal_kernel_call(
        self,
        call_key: str | None,
        state: str,
        adapter: ModelAdapter,
        *,
        cost: float = 0.0,
        payload: str | None = None,
    ) -> None:
        """Advance one kernel call's durable checkpoint (no-op unconfigured)."""

        if call_key is None or self.recovery_store is None:
            return
        identity = self._journal_identity()
        if identity is None:
            return
        organization_id, task_id, attempt = identity
        self.recovery_store.record_call_state(
            organization_id=organization_id,
            task_id=task_id,
            task_attempt_number=attempt,
            call_key=call_key,
            state=state,
            executor_kind="collaboration",
            adapter_id=getattr(adapter, "adapter_id", "") or "",
            reservation_id="",
            actual_cost_usd=cost,
            response_payload=payload,
        )

    def _reuse_committed_turn(self, call_key: str | None) -> dict[str, Any] | None:
        """Return the durable committed payload for this turn, if any.

        A row already past RESPONSE_COMMITTED is authoritative and reusable;
        a dispatched-without-commit row means the external outcome is unknown
        and the turn must block rather than redispatch.
        """

        if call_key is None or self.recovery_store is None:
            return None
        identity = self._journal_identity()
        if identity is None:
            return None
        organization_id, task_id, attempt = identity
        row = self.recovery_store.get_call_attempt(
            organization_id, task_id, attempt, call_key
        )
        if row is None:
            return None
        state = row["state"]
        if state in ("RESPONSE_COMMITTED", "SETTLED"):
            return {
                "payload": str(row["response_payload"] or ""),
                "cost": float(row["actual_cost_usd"] or 0.0),
            }
        if state in ("DISPATCH_INTENT_DURABLE", "DISPATCHED", "RESPONSE_RECEIVED"):
            raise _KernelTurnUncertain(call_key)
        return None

    @staticmethod
    async def _collect_stream(
        adapter: ModelAdapter, request: ModelRequest
    ) -> ModelResponse:
        pieces: list[str] = []
        terminal: ModelResponse | None = None
        async for chunk in adapter.stream(request):
            if chunk.delta:
                pieces.append(chunk.delta)
            if chunk.done and chunk.response is not None:
                terminal = chunk.response
        if terminal is None:
            text = "".join(pieces)
            return ModelResponse(
                text=text,
                model=adapter.model,
                provider=adapter.provider,
                usage=TokenUsage(
                    input_tokens=adapter.estimate_tokens(request),
                    output_tokens=adapter.estimate_tokens(text),
                    cost_usd=adapter.estimate_cost(
                        adapter.estimate_tokens(request), adapter.estimate_tokens(text)
                    ),
                ),
                finish_reason="stream_ended",
            )
        return terminal

    async def _integrate_response(
        self,
        item: _CompletedCall,
        original_request: ModelRequest,
        *,
        pending_cloud_calls: int,
        outstanding_api_reservation: float,
        current_api_reservation: float,
    ) -> SemanticContribution | None:
        assert item.response is not None
        finish_reason = str(item.response.finish_reason or "").casefold()
        if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
            # A truncated generation is incomplete and is never published as a
            # completed answer (desktop chats surface it as a failed run), but
            # it is a provider condition, not a budget or policy decision.
            # The desktop output ceiling is what keeps long answers from
            # reaching this branch at all.
            # A provider that stopped at its output ceiling is a provider
            # condition, never a budget or policy decision by this runtime.
            self._provider_failure_rounds.add(item.record.round_number)
            item.record.status = "failed"
            item.record.error = (
                "ModelOutputTruncatedError: provider finish_reason="
                + finish_reason
            )
            item.record.staleness = StalenessDisposition.FAILED
            await self._model_error(
                item.adapter.adapter_id,
                "generation",
                item.record.error,
                item.record.start_state_version,
            )
            await self._record_cost(item)
            return None
        try:
            contribution = self.extractor.extract(
                item.response.text,
                source=item.adapter.adapter_id,
                state_version=item.record.start_state_version,
            )
        except SemanticExtractionError as exc:
            # H1.2-B: Solo direct-answer rounds legitimately produce plain
            # language (the user-facing reply), not the committee JSON
            # interchange. Accept the verbatim answer as the summary instead of
            # failing the run; no events are inferred.
            if (
                getattr(self, "_active_mode", None) is CollaborationMode.SOLO
                and getattr(self, "_solo_direct_round", False)
                and item.response.text.strip()
            ):
                from amca.semantic import SemanticContribution

                contribution = SemanticContribution(
                    source=item.adapter.adapter_id,
                    summary=item.response.text.strip(),
                    events=(),
                    criterion_evidence=(),
                    focus_resolved=False,
                    raw_text=item.response.text,
                    structured=False,
                    dropped_event_count=0,
                )
            else:
                # An EMPTY provider body is a provider failure, not a
                # budget-or-policy decision.  The run record of the installed
                # failure of 2026-10-21 carried stop_reason
                # "budget_or_policy" for a provider that returned nothing,
                # which sent the investigation after the wrong cause.
                if not item.response.text.strip():
                    self._provider_failure_rounds.add(item.record.round_number)
                item.record.status = "failed"
                item.record.error = str(exc)
                item.record.staleness = StalenessDisposition.FAILED
                await self._apply(
                    self.extractor.extraction_error_event(
                        exc,
                        source=item.adapter.adapter_id,
                        state_version=min(item.record.start_state_version, self.state.version),
                    )
                )
                await self._record_cost(item)
                return None

        await self._audit_dropped_semantic_events(
            contribution,
            item.adapter.adapter_id,
            item.record.start_state_version,
        )
        rejection, contribution = self._prevalidate_contribution(contribution)
        if rejection is not None:
            item.record.status = "failed"
            item.record.error = rejection
            item.record.staleness = StalenessDisposition.FAILED
            await self._model_error(
                item.adapter.adapter_id,
                "state_reduction",
                rejection,
                self.state.version,
            )
            await self._record_cost(item)
            return None

        lag = max(
            0,
            self.state.cognitive_revision - item.record.start_cognitive_revision,
        )
        item.record.cognitive_lag = lag
        critical = any(
            event.event_type is EventType.CRITICAL_CONFLICT
            or (
                event.event_type in {EventType.OBJECTION, EventType.BLOCKER}
                and event.severity is Severity.CRITICAL
            )
            for event in contribution.events
        )
        stale_critical_resolution = any(
            event.event_type is EventType.CONSENSUS
            and any(
                disagreement.id == event.payload.get("disagreement_id")
                and not disagreement.resolved
                and disagreement.severity is Severity.CRITICAL
                for disagreement in self.state.active_disagreements
            )
            for event in contribution.events
        )
        if lag == 0:
            item.record.staleness = StalenessDisposition.CURRENT
        elif critical:
            item.record.staleness = StalenessDisposition.CRITICAL_REOPEN
        elif stale_critical_resolution and self.config.rebase_stale_responses:
            rebased = await self._rebase(
                item,
                contribution,
                original_request,
                pending_cloud_calls=pending_cloud_calls,
                outstanding_api_reservation=outstanding_api_reservation,
                current_api_reservation=current_api_reservation,
            )
            if rebased is None:
                await self._record_cost(item)
                return None
            contribution = rebased
            item.record.staleness = StalenessDisposition.REBASED
        elif stale_critical_resolution:
            item.record.status = "stale_result"
            item.record.error = "A stale critical-disagreement resolution requires rebase"
            await self._record_cost(item)
            return None
        elif lag <= self.config.max_staleness_versions:
            item.record.staleness = StalenessDisposition.COMPATIBLE_MERGE
        elif self.config.rebase_stale_responses:
            rebased = await self._rebase(
                item,
                contribution,
                original_request,
                pending_cloud_calls=pending_cloud_calls,
                outstanding_api_reservation=outstanding_api_reservation,
                current_api_reservation=current_api_reservation,
            )
            if rebased is None:
                await self._record_cost(item)
                return None
            contribution = rebased
            item.record.staleness = StalenessDisposition.REBASED
        else:
            item.record.status = "stale_result"
            item.record.error = f"Response exceeded staleness bound by {lag} versions"
            await self._record_cost(item)
            return None

        for event in contribution.events:
            event.state_version = min(event.state_version, self.state.version)
            try:
                await self._apply(event)
                if event.event_type is EventType.TOOL_REQUEST:
                    await self._execute_tool_request(event)
            except (KeyError, ValueError) as exc:
                item.record.status = "failed"
                item.record.error = f"Semantic event rejected: {type(exc).__name__}: {exc}"
                item.record.staleness = StalenessDisposition.FAILED
                await self._model_error(
                    item.adapter.adapter_id,
                    "state_reduction",
                    item.record.error,
                    self.state.version,
                )
                await self._record_cost(item)
                return None
        item.record.summary = contribution.summary
        item.record.focus_resolved = contribution.focus_resolved
        item.record.integrated_state_version = self.state.version
        item.record.status = "complete"
        await self._record_cost(item)
        return contribution

    def _prevalidate_contribution(
        self, contribution: SemanticContribution
    ) -> tuple[str | None, SemanticContribution]:
        """Validate the whole semantic batch before any event becomes canonical.

        07C: non-authorizable tool_request events (e.g. 'simplify') emitted by
        small local models are DROPPED from the contribution and audited on the
        call record instead of aborting the whole semantic batch.  The
        authorization boundary is unchanged — such a request is never executed.
        """

        preview = self.state
        dropped: list[Any] = []
        for event in contribution.events:
            if event.event_type in _MODEL_FORBIDDEN_EVENT_TYPES:
                return (
                    "Semantic event rejected: models cannot emit operational event "
                    f"type {event.event_type.value!r}",
                    contribution,
                )
            if event.event_type is EventType.TOOL_REQUEST:
                payload = event.payload
                if (
                    payload.get("tool", "math") != "math"
                    or payload.get("operation") != "evaluate"
                    or not isinstance(payload.get("expression"), str)
                ):
                    dropped.append(event)
                    continue
            preview_event = CognitiveEvent.from_dict(event.to_dict())
            preview_event.state_version = min(
                preview_event.state_version, preview.version
            )
            try:
                preview = StateManager.reduce(preview, preview_event)
            except (KeyError, ValueError) as exc:
                return f"Semantic event rejected: {type(exc).__name__}: {exc}", contribution
        if dropped:
            kept = tuple(e for e in contribution.events if e not in dropped)
            contribution = SemanticContribution(
                source=contribution.source,
                summary=contribution.summary,
                events=kept,
                criterion_evidence=contribution.criterion_evidence,
                focus_resolved=contribution.focus_resolved,
                raw_text=contribution.raw_text,
                dropped_event_count=contribution.dropped_event_count + len(dropped),
            )
        return None, contribution

    async def _rebase(
        self,
        item: _CompletedCall,
        original: SemanticContribution,
        original_request: ModelRequest,
        *,
        pending_cloud_calls: int,
        outstanding_api_reservation: float,
        current_api_reservation: float,
    ) -> SemanticContribution | None:
        packet = self.context_builder.build(self.state)
        request = ModelRequest(
            messages=(
                {
                    "role": "user",
                    "content": (
                        packet.as_prompt()
                        + "\n\nYour earlier contribution was based on an older state:\n"
                        + original.summary
                        + "\nReturn a compact reconsideration against the current state. "
                        "Keep only still-relevant claims and explicitly flag contradictions."
                    ),
                },
            ),
            system=SEMANTIC_RESPONSE_INSTRUCTIONS,
            temperature=original_request.temperature,
            max_output_tokens=min(original_request.max_output_tokens or 900, 900),
        )
        admission = self.cost_controller.admit(
            self.state,
            item.adapter,
            estimated_input_tokens=item.adapter.estimate_tokens(request),
            estimated_output_tokens=request.max_output_tokens or 0,
        )
        if not admission.allowed:
            item.record.error = "Rebase blocked: " + admission.reason
            item.record.status = "stale_result"
            return None
        contract_limit = (
            self.state.goal_contract.max_api_cost_usd
            if self.state.goal_contract is not None
            else None
        )
        if (
            contract_limit is not None
            and _budget_blocks(self.state)
            and not item.adapter.capabilities().is_local
            and self.state.cost_state.api_cost_usd
            + max(0.0, outstanding_api_reservation - current_api_reservation)
            + item.record.cost_usd
            + admission.estimated_cost_usd
            > contract_limit
        ):
            item.record.error = "Rebase blocked: the original call plus rebase would exceed budget"
            item.record.status = "stale_result"
            return None
        if (
            not item.adapter.capabilities().is_local
            and pending_cloud_calls + 1
            > self.cost_controller.remaining_frontier_calls(self.state)
        ):
            item.record.error = "Rebase blocked: compute policy frontier-call limit"
            item.record.status = "stale_result"
            return None
        if not self._increase_durable_model_call_reservation(
            item.record,
            additional_estimated_api_cost_usd=(
                0.0
                if item.adapter.capabilities().is_local
                else admission.estimated_cost_usd
            ),
        ):
            item.record.error = (
                "Rebase blocked: durable reservation would exceed the API budget"
            )
            item.record.status = "stale_result"
            return None
        started = time.perf_counter()
        try:
            item.record.rebase_calls += 1
            remaining = self._remaining_runtime()
            call_timeout = self.config.model_timeout_seconds
            if remaining is not None:
                call_timeout = min(call_timeout, remaining)
            if call_timeout <= 0:
                raise TimeoutError("Goal Contract runtime limit exhausted")
            effective_request = replace(request, timeout_seconds=call_timeout)
            response = await asyncio.wait_for(
                item.adapter.generate(effective_request),
                timeout=call_timeout,
            )
            item.record.input_tokens += response.usage.input_tokens
            item.record.output_tokens += response.usage.output_tokens
            item.record.cost_usd += response.usage.cost_usd
            return self.extractor.extract(
                response.text,
                source=item.adapter.adapter_id,
                state_version=self.state.version,
            )
        except Exception as exc:
            item.record.error = f"Rebase failed: {type(exc).__name__}: {exc}"
            item.record.status = "stale_result"
            if not item.adapter.capabilities().is_local:
                item.record.usage_uncertain = True
            return None
        finally:
            item.record.elapsed_seconds += time.perf_counter() - started

    async def _record_cost(self, item: _CompletedCall) -> None:
        # Settle the durable admission before publishing telemetry. If the
        # process dies between these operations the cost ledger remains safe;
        # the project snapshot may temporarily under-report but cannot reuse
        # the provider budget.
        self._settle_durable_model_call(item)
        await self._apply(
            CognitiveEvent(
                event_type=EventType.COST_UPDATE,
                source="cost-monitor",
                payload={
                    "model": item.adapter.adapter_id,
                    "input_tokens": item.record.input_tokens,
                    "output_tokens": item.record.output_tokens,
                    "api_cost_usd": (
                        0.0 if item.adapter.capabilities().is_local else item.record.cost_usd
                    ),
                    "local_compute_seconds": (
                        item.record.elapsed_seconds
                        if item.adapter.capabilities().is_local
                        else 0.0
                    ),
                    "latency_seconds": item.record.elapsed_seconds,
                    "calls": 1 + item.record.rebase_calls,
                    "api_calls": (
                        0
                        if item.adapter.capabilities().is_local
                        else 1 + item.record.rebase_calls
                    ),
                    "local_calls": (
                        1 + item.record.rebase_calls
                        if item.adapter.capabilities().is_local
                        else 0
                    ),
                },
                state_version=self.state.version,
            )
        )

    async def _execute_tool_request(self, request_event: CognitiveEvent) -> None:
        """Execute the first slice's single allow-listed deterministic tool."""

        payload = request_event.payload
        tool = payload.get("tool", "math")
        operation = payload.get("operation")
        expression = payload.get("expression")
        disagreement_id = (
            str(payload["disagreement_id"])
            if payload.get("disagreement_id") is not None
            else None
        )
        criterion_id = (
            str(payload["criterion_id"])
            if payload.get("criterion_id") is not None
            else None
        )
        evidence_id = str(
            payload.get("evidence_id")
            or (
                f"tool:{disagreement_id}"
                if disagreement_id
                else f"tool:{request_event.id}"
            )
        )
        if tool != "math" or operation != "evaluate" or not isinstance(expression, str):
            result_payload: dict[str, object] = {
                "request_event_id": request_event.id,
                "evidence_id": evidence_id,
                "addresses_disagreement_ids": (
                    [disagreement_id] if disagreement_id else []
                ),
                "supports_criterion_ids": [criterion_id] if criterion_id else [],
                "ok": False,
                "error": (
                    "Only a structured arithmetic evaluate request is allowed in this slice."
                ),
            }
            elapsed = 0.0
        else:
            variables = payload.get("variables")
            if not isinstance(variables, dict):
                variables = None
            result = self.math_tool.execute(
                operation,
                expression,
                symbol=(str(payload["symbol"]) if payload.get("symbol") is not None else None),
                variables=variables,
            )
            elapsed = result.elapsed_seconds
            result_payload = {
                "request_event_id": request_event.id,
                "evidence_id": evidence_id,
                "addresses_disagreement_ids": (
                    [disagreement_id] if disagreement_id else []
                ),
                "supports_criterion_ids": [criterion_id] if criterion_id else [],
                "ok": result.ok,
                "operation": result.operation,
                "expression": expression,
                "output": result.output,
                "error": result.error,
                "metadata": dict(result.metadata),
            }
        await self._apply(
            CognitiveEvent(
                event_type=EventType.TOOL_RESULT,
                source="math-tool",
                payload={"result": result_payload},
                state_version=self.state.version,
                severity=(Severity.INFO if result_payload["ok"] else Severity.ERROR),
                causation_id=request_event.id,
            )
        )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.COST_UPDATE,
                source="cost-monitor",
                payload={
                    "local_compute_seconds": elapsed,
                    "latency_seconds": elapsed,
                    "calls": 0,
                },
                state_version=self.state.version,
                causation_id=request_event.id,
            )
        )

    async def _model_error(
        self,
        source: str,
        stage: str,
        error: str,
        start_version: int,
    ) -> None:
        await self._apply(
            CognitiveEvent(
                event_type=EventType.MODEL_ERROR,
                source=source,
                payload={"stage": stage, "error": error},
                state_version=min(start_version, self.state.version),
                severity=Severity.ERROR,
            )
        )

    async def _audit_dropped_semantic_events(
        self, contribution: object, source: str, start_version: int
    ) -> None:
        """Record malformed-event drops so tolerant extraction is never silent."""

        dropped = getattr(contribution, "dropped_event_count", 0)
        if dropped:
            await self._model_error(
                source,
                "semantic_extraction",
                f"dropped {dropped} malformed semantic event(s) with unknown or "
                "non-authorable types; the model summary was kept and no "
                "malformed content was integrated into canonical state",
                start_version,
            )

    def _synchronization_passes(
        self,
        sync_mode: SyncMode,
        selected: Sequence[ModelAdapter],
        completed: Sequence[_CompletedCall],
    ) -> bool:
        if self.state.has_critical_objection:
            return False
        completed_ids = {
            item.adapter.adapter_id
            for item in completed
        }
        if sync_mode in {SyncMode.ASYNC, SyncMode.SOFT}:
            return True
        if sync_mode is SyncMode.QUORUM:
            threshold = max(2, math.ceil(2 * len(selected) / 3))
            return len(completed_ids) >= min(threshold, len(selected))
        if completed_ids != {adapter.adapter_id for adapter in selected}:
            return False
        if sync_mode is SyncMode.BOUNDED_STALENESS:
            return all(
                item.record.cognitive_lag <= self.config.max_staleness_versions
                or item.record.staleness is StalenessDisposition.REBASED
                for item in completed
            )
        return True

    async def _record_synchronization_checkpoint(
        self,
        sync_mode: SyncMode,
        selected: Sequence[ModelAdapter],
        completed: Sequence[_CompletedCall],
        *,
        round_number: int,
        gate_allows: bool = True,
        gate_reason: str | None = None,
        released_participant_ids: frozenset[str] = frozenset(),
        release_authorized_by: str | None = None,
    ) -> SynchronizationCheckpoint:
        """Persist the round gate and every participant's disposition."""

        del completed  # Call records carry failures as well as successful responses.
        participant_ids = [adapter.adapter_id for adapter in selected]
        if released_participant_ids - set(participant_ids):
            raise ValueError("checkpoint release references an ineligible participant")
        if released_participant_ids and not release_authorized_by:
            raise ValueError("checkpoint releases require explicit authorization")
        opened_time = datetime.now(timezone.utc)
        opened_at = opened_time.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        deadline_at = (
            opened_time + timedelta(seconds=self.config.checkpoint_timeout_seconds)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        checkpoint = SynchronizationCheckpoint(
            sync_mode=sync_mode,
            base_mode=(
                self.config.bounded_staleness_base_mode
                if sync_mode is SyncMode.BOUNDED_STALENESS
                else sync_mode
            ),
            participant_ids=participant_ids,
            required_participant_ids=(
                participant_ids
                if sync_mode in {SyncMode.HARD, SyncMode.BOUNDED_STALENESS}
                else []
            ),
            max_version_lag=self.config.max_staleness_versions,
            opened_state_version=self.state.version + 1,
            reason=f"round {round_number}",
            opened_at=opened_at,
            deadline_at=deadline_at,
        )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.SYNC_CHECKPOINT_OPENED,
                source="synchronization-controller",
                payload={"checkpoint": checkpoint.to_dict()},
                state_version=self.state.version,
            )
        )

        # ASYNC has no barrier and solicits no checkpoint dispositions. The
        # persisted record is an integration receipt used only to anchor a
        # possible PROVISIONAL DecisionRecord.
        if sync_mode is not SyncMode.ASYNC:
            round_records = {
                record.adapter_id: record
                for record in self._call_records
                if record.round_number == round_number
            }
            for participant_id in participant_ids:
                record = round_records.get(participant_id)
                event_source = "synchronization-controller"
                if participant_id in released_participant_ids:
                    disposition = ParticipantDisposition(
                        participant_id=participant_id,
                        status=ParticipantDispositionStatus.RELEASED,
                        summary="Explicitly released by the user from this checkpoint.",
                        start_state_version=self.state.version,
                        authorized_by=release_authorized_by,
                    )
                    event_source = "user"
                elif record is None:
                    disposition = ParticipantDisposition(
                        participant_id=participant_id,
                        status=ParticipantDispositionStatus.FAILED,
                        summary="Participant was not admitted by policy or budget.",
                        start_state_version=self.state.version,
                    )
                else:
                    if record.status == "complete":
                        status = (
                            ParticipantDispositionStatus.SUPPORT
                            if record.focus_resolved
                            else ParticipantDispositionStatus.OBJECT
                        )
                    elif record.error and "did not answer in time" in record.error:
                        status = ParticipantDispositionStatus.TIMEOUT
                    else:
                        status = ParticipantDispositionStatus.FAILED
                    disposition = ParticipantDisposition(
                        participant_id=participant_id,
                        status=status,
                        summary=record.summary or record.error or record.status,
                        start_state_version=record.start_state_version,
                        integrated_state_version=record.integrated_state_version,
                        cognitive_lag=record.cognitive_lag,
                        rebased=(
                            record.staleness is StalenessDisposition.REBASED
                        ),
                    )
                await self._apply(
                    CognitiveEvent(
                        event_type=EventType.PARTICIPANT_DISPOSITION,
                        source=event_source,
                        payload={
                            "checkpoint_id": checkpoint.id,
                            "disposition": disposition.to_dict(),
                        },
                        state_version=self.state.version,
                    )
                )

        current = self.state.synchronization_checkpoint(checkpoint.id)
        if current is None:
            raise RuntimeError("synchronization checkpoint was not persisted")
        closing_at = utc_now()
        synchronization_allows = current.can_pass(
            has_critical_objection=self.state.has_critical_objection,
            at=closing_at,
        )
        passed = synchronization_allows and gate_allows
        if passed:
            reason = gate_reason or "synchronization requirements passed"
        elif not gate_allows:
            reason = gate_reason or "the enclosing integration gate blocked completion"
        else:
            reason = (
                "participant dispositions or a critical objection blocked the gate"
            )
        await self._apply(
            CognitiveEvent(
                event_type=EventType.SYNC_CHECKPOINT_CLOSED,
                source="synchronization-controller",
                payload={
                    "checkpoint_id": checkpoint.id,
                    "passed": passed,
                    "reason": reason,
                },
                state_version=self.state.version,
                created_at=closing_at,
            )
        )
        closed = self.state.synchronization_checkpoint(checkpoint.id)
        if closed is None or closed.status is CheckpointStatus.OPEN:
            raise RuntimeError("synchronization checkpoint did not close")
        return closed

    async def _accept_criterion_evidence(
        self,
        proposals: Sequence[tuple[str, str, str]],
        *,
        checkpoint_id: str,
    ) -> None:
        contract = self.state.goal_contract
        checkpoint = self.state.synchronization_checkpoint(checkpoint_id)
        if (
            contract is None
            or checkpoint is None
            or not checkpoint.passed
            or self.state.has_critical_objection
        ):
            return
        for criterion_id, evidence, source in proposals:
            if contract.criterion(criterion_id) is None:
                continue
            self._criterion_attestations.setdefault(criterion_id, {})[source] = evidence

        criterion_ids = [criterion.id for criterion in contract.unmet_criteria]
        for criterion_id in criterion_ids:
            current_contract = self.state.goal_contract
            if current_contract is None:
                return
            criterion = current_contract.criterion(criterion_id)
            if (
                criterion is None
                or criterion.satisfied
                or not checkpoint.permits(criterion.decision_class)
            ):
                continue
            evaluator_name = criterion.evaluator_kind.value
            matching: list[EvidenceRecord] = []
            if criterion.evaluator_kind is EvaluatorKind.CUSTOM:
                by_source = self._criterion_attestations.get(criterion_id, {})
                if self.criterion_evaluator is None or not by_source:
                    continue
                try:
                    evaluation = self.criterion_evaluator(
                        self.state, criterion, dict(by_source)
                    )
                except Exception as exc:
                    await self._model_error(
                        "criterion-evaluator",
                        "criterion_evaluation",
                        f"{type(exc).__name__}: {exc}",
                        self.state.version,
                    )
                    continue
                if not evaluation:
                    continue
                evaluator_name = getattr(
                    self.criterion_evaluator,
                    "__qualname__",
                    type(self.criterion_evaluator).__name__,
                )
                record = self._normalize_evaluator_evidence(
                    criterion,
                    checkpoint,
                    by_source,
                    evaluation,
                    evaluator_name=evaluator_name,
                )
                await self._apply(
                    CognitiveEvent(
                        event_type=EventType.EVIDENCE,
                        source="criterion-evaluator",
                        payload={"record": record.to_dict()},
                        state_version=self.state.version,
                        created_at=record.created_at,
                    )
                )
                persisted = self.state.evidence_record(record.id)
                if persisted is not None:
                    matching.append(persisted)
                matching.extend(
                    candidate
                    for candidate in self.state.evidence_records.values()
                    if candidate.id != record.id
                    and criterion_id in candidate.supports_criterion_ids
                )
            else:
                matching.extend(
                    record
                    for record in self.state.evidence_records.values()
                    if self._criterion_evidence_matches(criterion, record)
                )

            matching = list({record.id: record for record in matching}.values())
            matching = [
                record
                for record in matching
                if record.verified
                and record.has_provenance
                and record.strength
                in {EvidenceStrength.MODERATE, EvidenceStrength.STRONG}
                and criterion_id in record.supports_criterion_ids
            ]
            if len(matching) < criterion.evidence_requirement:
                continue
            evidence_ids = [record.id for record in matching]
            dispositions = {
                participant_id: disposition.status.value
                for participant_id, disposition in checkpoint.dispositions.items()
            }
            critical_dispositions = {
                disagreement.id: "resolved"
                for disagreement in self.state.active_disagreements
                if disagreement.severity is Severity.CRITICAL
                and disagreement.resolved
            }
            decision = DecisionRecord(
                criterion_id=criterion_id,
                decision_class=criterion.decision_class,
                checkpoint_id=checkpoint.id,
                evidence_ids=evidence_ids,
                dispositions=dispositions,
                critical_disagreement_dispositions=critical_dispositions,
                rationale="; ".join(record.summary for record in matching),
                evaluator=evaluator_name,
                passed=True,
            )
            await self._apply(
                CognitiveEvent(
                    event_type=EventType.GOAL_PROGRESS,
                    source="criterion-evaluator",
                    payload={
                        "criterion_id": criterion_id,
                        "satisfied": True,
                        "decision": decision.to_dict(),
                    },
                    state_version=self.state.version,
                )
            )

    @staticmethod
    def _normalize_evaluator_evidence(
        criterion: GoalCriterion,
        checkpoint: SynchronizationCheckpoint,
        attestations: Mapping[str, str],
        evaluation: str | EvidenceRecord,
        *,
        evaluator_name: str,
    ) -> EvidenceRecord:
        if isinstance(evaluation, EvidenceRecord):
            data = evaluation.to_dict()
            supports = list(data.get("supports_criterion_ids", []))
            if criterion.id not in supports:
                supports.append(criterion.id)
            data["supports_criterion_ids"] = supports
            return EvidenceRecord.from_dict(data)
        created_at = utc_now()
        digest_input = {
            "criterion_id": criterion.id,
            "checkpoint_id": checkpoint.id,
            "attestations": dict(sorted(attestations.items())),
            "evaluation": evaluation,
            "evaluator": evaluator_name,
        }
        digest = hashlib.sha256(
            json.dumps(
                digest_input,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return EvidenceRecord(
            id=f"evaluation:{checkpoint.id}:{criterion.id}",
            summary=evaluation,
            kind=EvidenceKind.TEST_RESULT,
            provenance={
                "source": "criterion-evaluator",
                "locator": evaluator_name,
                "timestamp": created_at,
                "content_digest": digest,
            },
            method=evaluator_name,
            supports_criterion_ids=[criterion.id],
            strength=EvidenceStrength.STRONG,
            verification_status=VerificationStatus.VERIFIED,
            value=True,
            producer="criterion-evaluator",
            created_at=created_at,
        )

    @staticmethod
    def _criterion_evidence_matches(
        criterion: GoalCriterion, record: EvidenceRecord
    ) -> bool:
        if (
            not record.verified
            or not record.has_provenance
            or criterion.id not in record.supports_criterion_ids
        ):
            return False
        value = record.value
        if criterion.evaluator_kind is EvaluatorKind.EXACT_VALUE:
            return value == criterion.expected_value
        if criterion.evaluator_kind is EvaluatorKind.NUMERIC_TOLERANCE:
            try:
                tolerance = criterion.tolerance or 0.0
                return abs(float(value) - float(criterion.expected_value)) <= tolerance
            except (TypeError, ValueError):
                return False
        if criterion.evaluator_kind is EvaluatorKind.CONTENT_PREDICATE:
            return str(criterion.expected_value) in str(value)
        if criterion.evaluator_kind is EvaluatorKind.JSON_SCHEMA:
            schema = criterion.expected_value
            if not isinstance(schema, Mapping) or not isinstance(value, Mapping):
                return False
            required = schema.get("required", [])
            return isinstance(required, list) and all(key in value for key in required)
        if criterion.evaluator_kind is EvaluatorKind.FILE_EXISTS:
            return bool(
                isinstance(value, Mapping)
                and value.get("exists") is True
                and record.method == "file_exists"
            )
        if criterion.evaluator_kind in {
            EvaluatorKind.COMMAND_EXIT,
            EvaluatorKind.TEST_SUITE,
        }:
            expected = 0 if criterion.expected_value is None else criterion.expected_value
            if isinstance(value, Mapping):
                value = value.get("exit_code")
            return record.kind is EvidenceKind.TEST_RESULT and value == expected
        if criterion.evaluator_kind is EvaluatorKind.HUMAN_REVIEW:
            approved = value is True or (
                isinstance(value, Mapping) and value.get("approved") is True
            )
            return bool(
                approved
                and record.kind is EvidenceKind.USER_PREMISE
                and record.producer in {"user", "human-reviewer"}
            )
        return False

    async def _apply(self, event: CognitiveEvent) -> ProjectState:
        state = self.state_manager.apply(event)
        await self.event_bus.publish(event)
        return state

    def _result(
        self,
        reason: StopReason,
        rounds: int,
        synchronization_passed: bool,
    ) -> KernelResult:
        return KernelResult(
            state=self.state_manager.snapshot(),
            stop_reason=reason,
            rounds_completed=rounds,
            call_records=tuple(self._call_records),
            synchronization_passed=synchronization_passed,
            criterion_attestations={
                criterion_id: dict(by_source)
                for criterion_id, by_source in self._criterion_attestations.items()
            },
            mode_trace=tuple(dict(record) for record in self._mode_trace),
        )


__all__ = [
    "CollaborationKernel",
    "CriterionEvaluator",
    "IntegrationEvaluator",
    "KernelConfig",
    "KernelResult",
    "ModelCallRecord",
    "StalenessDisposition",
    "StopReason",
]
