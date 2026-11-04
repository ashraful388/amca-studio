"""M2 bounded Hybrid Execution foundation (state machine + coordinator).

Additive module introduced by the M2 Hybrid Execution Foundation milestone
(zcode/amca-v0.5.0-m2-hybrid-execution-foundation-01). Implements the hybrid
orchestration mechanism only:

    Task -> Hybrid Coordinator -> deterministic baseline
         -> optional bounded adaptive evaluation
         -> validated strategy decision -> deterministic-compatible output

The coordinator never gains tool, model, permission, or execution authority:
it only sequences the M1 selection seam, the deterministic planner, and the
M1.2 adaptive adapter, and validates whatever they return against locally
carried execution bounds. Every Hybrid session is deterministic-first — it
must begin from an admissible deterministic baseline, adaptive evaluation can
only refine among authorized alternatives, and any adaptive failure falls
back to the untouched deterministic result. With the shipped gates (all
FALSE) an explicit Hybrid request fails closed instead of activating.

Import-isolated as in M0-M1.2: nothing in the pre-existing application
imports this package, and direct unit-test construction is allowed.
"""

import dataclasses
import enum
from typing import Any, Callable, ClassVar, Optional, Tuple

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    PlannerResult,
    PlannerResultStatus,
    contract_post_init,
)
from .gates import FeatureGateRegistry
from .planner_selection import (
    AdaptivePlanner,
    AdaptivePlannerError,
    AdaptivePlannerUnavailable,
    CandidateSet,
    DeterministicPlanner,
    PolicyContext,
)


class HybridSessionState(enum.Enum):
    """Small audited hybrid lifecycle (names follow the approved contracts)."""

    INITIALIZED = "initialized"
    DETERMINISTIC_BASELINE = "deterministic_baseline"
    ADAPTIVE_EVALUATION = "adaptive_evaluation"
    DECISION = "decision"
    COMPLETED = "completed"
    FALLBACK = "fallback"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


#: Allowed lifecycle transitions; anything else fails closed. COMPLETED and
#: the terminal conditions (FALLBACK/BLOCKED/CANCELLED/FAILED) are durable.
HYBRID_TRANSITIONS = {
    HybridSessionState.INITIALIZED: frozenset(
        {
            HybridSessionState.DETERMINISTIC_BASELINE,
            HybridSessionState.BLOCKED,
            HybridSessionState.CANCELLED,
            HybridSessionState.FAILED,
        }
    ),
    HybridSessionState.DETERMINISTIC_BASELINE: frozenset(
        {
            HybridSessionState.ADAPTIVE_EVALUATION,
            HybridSessionState.DECISION,
            HybridSessionState.BLOCKED,
            HybridSessionState.CANCELLED,
            HybridSessionState.FAILED,
        }
    ),
    HybridSessionState.ADAPTIVE_EVALUATION: frozenset(
        {
            HybridSessionState.DECISION,
            HybridSessionState.FALLBACK,
            HybridSessionState.CANCELLED,
            HybridSessionState.FAILED,
        }
    ),
    HybridSessionState.DECISION: frozenset(
        {
            HybridSessionState.COMPLETED,
            HybridSessionState.FALLBACK,
            HybridSessionState.BLOCKED,
            HybridSessionState.CANCELLED,
            HybridSessionState.FAILED,
        }
    ),
    HybridSessionState.COMPLETED: frozenset(),
    HybridSessionState.FALLBACK: frozenset(),
    HybridSessionState.BLOCKED: frozenset(),
    HybridSessionState.CANCELLED: frozenset(),
    HybridSessionState.FAILED: frozenset(),
}


def validate_hybrid_transition(previous, next_state) -> None:
    """Fail closed on any lifecycle transition outside the audited map."""

    previous = _enum_from_value(HybridSessionState, previous, "previous")
    next_state = _enum_from_value(HybridSessionState, next_state, "next_state")
    if next_state not in HYBRID_TRANSITIONS[previous]:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"invalid hybrid transition {previous.value} -> {next_state.value}",
        )


def _enum_from_value(enum_cls, value, field):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"unknown {enum_cls.__name__} value for {field}: {value!r}",
        ) from None


class HybridDecisionPolicy(enum.Enum):
    """The deterministic, inspectable decision outcomes (no learned policy)."""

    DETERMINISTIC_BASELINE = "deterministic_baseline"
    ADAPTIVE_PROPOSAL = "adaptive_proposal"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


@dataclasses.dataclass(frozen=True)
class HybridExecutionBounds(Contract):
    """The bounded authority envelope adaptive evaluation may never enlarge."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.hybrid_execution_bounds"

    meta: ContractMetadata
    max_rounds: int
    max_estimated_cost: float
    max_work_units: int
    allowed_collaboration_modes: Tuple[str, ...]
    allowed_permissions: Tuple[str, ...]
    max_allowed_risk: str

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if isinstance(self.max_rounds, bool) or not isinstance(
            self.max_rounds, int
        ) or self.max_rounds < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "max_rounds must be a positive integer"
            )
        for field_name in ("max_estimated_cost",):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"{field_name} must be a number",
                )
            if value != value or value in (float("inf"), float("-inf")):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field_name} must be finite"
                )
            if value < 0:
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field_name} must be non-negative"
                )
        if isinstance(self.max_work_units, bool) or not isinstance(
            self.max_work_units, int
        ) or self.max_work_units < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "max_work_units must be a positive integer",
            )


@dataclasses.dataclass(frozen=True)
class HybridOutcome(Contract):
    """Deterministic-compatible terminal outcome of one hybrid session."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.hybrid_outcome"

    meta: ContractMetadata
    request_ref: str
    state: HybridSessionState
    decision: Optional[HybridDecisionPolicy]
    result: Optional[PlannerResult]
    transitions: Tuple[str, ...]
    bounds: HybridExecutionBounds
    hybrid_gate_enabled: bool
    fallback_reason: Optional[str]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.transitions:
            if self.transitions[0] != HybridSessionState.INITIALIZED.value:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "the audited transition path must start at initialized",
                )
            for previous, next_state in zip(
                self.transitions, self.transitions[1:]
            ):
                validate_hybrid_transition(previous, next_state)
            if self.transitions[-1] != self.state.value:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "the audited transition path must end at the final state",
                )
        if self.state is HybridSessionState.FALLBACK and not self.fallback_reason:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a fallback outcome requires a fallback reason",
            )
        if self.decision is None and self.state in (
            HybridSessionState.COMPLETED,
            HybridSessionState.FALLBACK,
        ):
            raise ContractError(
                ContractErrorCode.INVALID,
                f"a {self.state.value} outcome requires a recorded decision",
            )
        if self.state in (
            HybridSessionState.COMPLETED,
            HybridSessionState.FALLBACK,
            HybridSessionState.BLOCKED,
        ) and self.result is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"a {self.state.value} outcome must carry a "
                "deterministic-compatible result",
            )


class HybridCoordinator:
    """Deterministic-first hybrid sequencing with bounded adaptive phase.

    The coordinator holds no tool/model/permission/execution authority: it
    only calls the injected deterministic planner and (when explicitly
    configured) the injected adaptive planner, validates results against the
    carried bounds, and returns a deterministic-compatible outcome.
    """

    def __init__(
        self,
        *,
        meta_factory: Callable[[str], ContractMetadata],
        deterministic: DeterministicPlanner,
        adaptive: Optional[AdaptivePlanner] = None,
        bounds: HybridExecutionBounds,
    ) -> None:
        self._meta_factory = meta_factory
        self._deterministic = deterministic
        self._adaptive = adaptive
        self.bounds = bounds

    # -- public API ---------------------------------------------------------

    def run(
        self,
        *,
        request_ref: str,
        planner_input,
        gates: FeatureGateRegistry,
        candidates: Optional[CandidateSet] = None,
        policy_context: Optional[PolicyContext] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> HybridOutcome:
        """Run one hybrid session; terminal state is always reached."""

        hybrid_gate = gates.is_enabled("hybrid_execution")
        transitions: list[str] = [HybridSessionState.INITIALIZED.value]

        def advance(next_state: HybridSessionState) -> None:
            validate_hybrid_transition(
                HybridSessionState(transitions[-1]), next_state
            )
            transitions.append(next_state.value)

        if not hybrid_gate:
            raise ContractError(
                ContractErrorCode.INVALID,
                "hybrid_execution requested while the hybrid_execution gate "
                "is disabled; refusing instead of silently activating Hybrid",
            )

        if cancel_check is not None and cancel_check():
            advance(HybridSessionState.CANCELLED)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.CANCELLED,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=None,
                decision=None,
                fallback_reason=None,
                deterministic_fallback_result=None,
            )

        advance(HybridSessionState.DETERMINISTIC_BASELINE)
        baseline = self._deterministic.propose(planner_input)
        if baseline.status is not PlannerResultStatus.PROPOSED:
            advance(HybridSessionState.BLOCKED)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.BLOCKED,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=baseline,
                decision=None,
                fallback_reason=None,
                deterministic_fallback_result=baseline,
            )

        if cancel_check is not None and cancel_check():
            advance(HybridSessionState.CANCELLED)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.CANCELLED,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=None,
                decision=None,
                fallback_reason=None,
                deterministic_fallback_result=baseline,
            )

        if self._adaptive is None:
            advance(HybridSessionState.DECISION)
            advance(HybridSessionState.COMPLETED)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.COMPLETED,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=baseline,
                decision=HybridDecisionPolicy.DETERMINISTIC_BASELINE,
                fallback_reason=None,
                deterministic_fallback_result=baseline,
            )

        advance(HybridSessionState.ADAPTIVE_EVALUATION)
        try:
            adaptive_result = self._adaptive.propose(
                planner_input,
                candidates if candidates is not None else CandidateSet(
                    meta=self._meta_factory("cands"),
                    request_ref=request_ref,
                    candidates=(),
                ),
                policy_context if policy_context is not None else PolicyContext(
                    meta=self._meta_factory("policy"),
                    policy_id=str(getattr(self._adaptive, "policy_id", "unspecified")),
                    policy_version=str(
                        getattr(self._adaptive, "policy_version", "unspecified")
                    ),
                    evaluated_envelope={},
                    pinned=False,
                ),
            )
        except (AdaptivePlannerError, AdaptivePlannerUnavailable) as error:
            advance(HybridSessionState.FALLBACK)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.FALLBACK,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=baseline,
                decision=HybridDecisionPolicy.DETERMINISTIC_FALLBACK,
                fallback_reason=f"adaptive evaluation failed: {error}",
                deterministic_fallback_result=baseline,
            )

        if cancel_check is not None and cancel_check():
            advance(HybridSessionState.CANCELLED)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.CANCELLED,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=None,
                decision=None,
                fallback_reason=None,
                deterministic_fallback_result=baseline,
            )

        advance(HybridSessionState.DECISION)
        violation = self._bounds_violation(adaptive_result)
        if violation is not None:
            advance(HybridSessionState.FALLBACK)
            return self._terminal(
                request_ref=request_ref,
                state=HybridSessionState.FALLBACK,
                transitions=transitions,
                hybrid_gate=hybrid_gate,
                result=baseline,
                decision=HybridDecisionPolicy.DETERMINISTIC_FALLBACK,
                fallback_reason=f"adaptive proposal rejected: {violation}",
                deterministic_fallback_result=baseline,
            )

        advance(HybridSessionState.COMPLETED)
        return self._terminal(
            request_ref=request_ref,
            state=HybridSessionState.COMPLETED,
            transitions=transitions,
            hybrid_gate=hybrid_gate,
            result=adaptive_result,
            decision=HybridDecisionPolicy.ADAPTIVE_PROPOSAL,
            fallback_reason=None,
            deterministic_fallback_result=baseline,
        )

    # -- internals -----------------------------------------------------------

    def _bounds_violation(self, adaptive_result: PlannerResult) -> Optional[str]:
        """Validate the adaptive result against the carried bounds."""

        if adaptive_result.status is not PlannerResultStatus.PROPOSED:
            return (
                f"adaptive result disposition is "
                f"{adaptive_result.status.value}, not proposed"
            )
        strategy = adaptive_result.strategy
        if strategy is None:
            return "adaptive proposal carries no execution strategy"
        if strategy.max_rounds > self.bounds.max_rounds:
            return (
                f"max_rounds {strategy.max_rounds} exceeds bound "
                f"{self.bounds.max_rounds}"
            )
        if strategy.max_total_cost > self.bounds.max_estimated_cost:
            return (
                f"max_total_cost {strategy.max_total_cost} exceeds bound "
                f"{self.bounds.max_estimated_cost}"
            )
        unit_count = len(strategy.work_units or ())
        if unit_count > self.bounds.max_work_units:
            return (
                f"work unit count {unit_count} exceeds bound "
                f"{self.bounds.max_work_units}"
            )
        mode = strategy.collaboration_mode
        if (
            self.bounds.allowed_collaboration_modes
            and mode not in self.bounds.allowed_collaboration_modes
        ):
            return f"collaboration mode {mode!r} is not allowed"
        return None

    def _terminal(
        self,
        *,
        request_ref: str,
        state: HybridSessionState,
        transitions: list[str],
        hybrid_gate: bool,
        result,
        decision,
        fallback_reason: Optional[str],
        deterministic_fallback_result,
    ) -> HybridOutcome:
        authoritative = result
        if authoritative is None:
            # Cancellation: no stale proposal acceptance; the deterministic
            # safety baseline is only attached when it exists.
            authoritative = deterministic_fallback_result
        return HybridOutcome(
            meta=self._meta_factory("hybrid-outcome"),
            request_ref=request_ref,
            state=state,
            decision=decision,
            result=authoritative,
            transitions=tuple(transitions),
            bounds=self.bounds,
            hybrid_gate_enabled=hybrid_gate,
            fallback_reason=fallback_reason,
        )
