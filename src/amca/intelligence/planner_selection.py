"""M1 planner selection interface, adaptive abstraction, and deterministic fallback.

Additive module introduced by the M1 Adaptive Planner Foundation milestone
(zcode/amca-v0.5.0-m1-adaptive-planner-foundation-01). It establishes only the
future v0.5.0 seam

    Task -> Planner Selection -> [Deterministic Planner] OR [Adaptive Planner Interface]

while the v0.4.0 execution path (deterministic planner by default) stays
untouched: selection consults the M0 feature gates, the adaptive gate ships
OFF so the deterministic planner is always selected, and any adaptive
unavailability or error falls back to the deterministic planner with a
recorded reason. No LLM/API model is called, no existing deterministic
planner logic is modified, and no pre-existing application code imports this
package.
"""

import dataclasses
import enum
from typing import Any, ClassVar, Mapping, Optional, Protocol, Tuple

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    PlannerInput,
    PlannerResult,
    contract_post_init,
)
from .evidence_hooks import (
    PlannerEvaluationEvidence,
    PlannerEvaluationStatus,
    execution_strategy_metadata,
)
from .gates import FeatureGateRegistry


class PlannerKind(enum.Enum):
    """The two planner kinds the seam can select between."""

    DETERMINISTIC = "deterministic"
    ADAPTIVE = "adaptive"


class PlannerSelectionMode(enum.Enum):
    """Explicit caller request; AUTO defers to the feature gates."""

    AUTO = "auto"
    DETERMINISTIC = "deterministic"
    ADAPTIVE = "adaptive"


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


class AdaptivePlannerUnavailable(Exception):
    """The adaptive planner cannot evaluate this request (fallback path)."""


class AdaptivePlannerError(Exception):
    """The adaptive planner failed while proposing (fallback path)."""


class DeterministicPlanner(Protocol):
    """Structural seam for the existing deterministic planning authority."""

    def propose(self, planner_input: PlannerInput) -> PlannerResult:
        ...


class AdaptivePlanner(Protocol):
    """Abstract adaptive proposal interface (contracts spec 2.5).

    No engine ships in M1. A future implementation ranks only candidates
    already eligible under deterministic hard constraints and may never start
    execution, reserve funds, grant permissions, or mutate canonical state.
    """

    policy_id: str
    policy_version: str

    def propose(
        self,
        planner_input: PlannerInput,
        candidates: "CandidateSet",
        policy_context: "PolicyContext",
    ) -> PlannerResult:
        ...


@dataclasses.dataclass(frozen=True)
class CandidateSet(Contract):
    """Bounded candidate descriptors an adaptive proposal may rank (spec 2.5).

    Candidates are deterministic-eligibility descriptors only; the set carries
    no authority and an adaptive planner may not add ineligible entries.
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_candidate_set"

    meta: ContractMetadata
    request_ref: str
    candidates: Tuple[Mapping[str, Any], ...]

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class PolicyContext(Contract):
    """Identifies the evaluated policy envelope for an adaptive proposal."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_policy_context"

    meta: ContractMetadata
    policy_id: str
    policy_version: str
    evaluated_envelope: Mapping[str, Any]
    pinned: bool

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class PlannerSelection(Contract):
    """Immutable record of which planner the seam selected, and why."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_selection"

    meta: ContractMetadata
    request_ref: str
    requested_mode: PlannerSelectionMode
    selected: PlannerKind
    adaptive_gate_enabled: bool
    reason: str
    fallback: bool
    fallback_reason: Optional[str]

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class PlannerInvocation(Contract):
    """Outcome of invoking the selected planner, with its evidence hook."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_invocation"

    meta: ContractMetadata
    request_ref: str
    planner_used: PlannerKind
    result: PlannerResult
    evidence: PlannerEvaluationEvidence
    used_fallback: bool

    def __post_init__(self):
        contract_post_init(self)


def select_planner(
    *,
    gates: FeatureGateRegistry,
    meta: ContractMetadata,
    request_ref: str,
    requested_mode: PlannerSelectionMode = PlannerSelectionMode.AUTO,
) -> PlannerSelection:
    """Select the planner for one request without executing anything.

    Fail-closed rules: unknown modes are rejected; with the adaptive gate
    OFF (the shipped default) the deterministic planner is selected; an
    explicit ADAPTIVE request is refused while the gate is OFF instead of
    silently downgraded. Selection carries no execution authority.
    """

    mode = _enum_from_value(PlannerSelectionMode, requested_mode, "requested_mode")
    adaptive_gate = gates.is_enabled("adaptive_planner")
    if mode is PlannerSelectionMode.ADAPTIVE and not adaptive_gate:
        raise ContractError(
            ContractErrorCode.INVALID,
            "adaptive planner requested while the adaptive_planner gate is "
            "disabled; deterministic remains the default",
        )
    if mode is PlannerSelectionMode.ADAPTIVE:
        return PlannerSelection(
            meta=meta,
            request_ref=request_ref,
            requested_mode=mode,
            selected=PlannerKind.ADAPTIVE,
            adaptive_gate_enabled=True,
            reason="adaptive planner explicitly requested and gate enabled",
            fallback=False,
            fallback_reason=None,
        )
    if adaptive_gate:
        return PlannerSelection(
            meta=meta,
            request_ref=request_ref,
            requested_mode=mode,
            selected=PlannerKind.ADAPTIVE,
            adaptive_gate_enabled=True,
            reason="adaptive_planner gate enabled; adaptive interface selected",
            fallback=False,
            fallback_reason=None,
        )
    reason = (
        "deterministic planner explicitly requested"
        if mode is PlannerSelectionMode.DETERMINISTIC
        else "adaptive_planner gate disabled; deterministic is the default"
    )
    return PlannerSelection(
        meta=meta,
        request_ref=request_ref,
        requested_mode=mode,
        selected=PlannerKind.DETERMINISTIC,
        adaptive_gate_enabled=False,
        reason=reason,
        fallback=False,
        fallback_reason=None,
    )


def invoke_selected_planner(
    *,
    selection: PlannerSelection,
    planner_input: PlannerInput,
    deterministic: DeterministicPlanner,
    adaptive: Optional[AdaptivePlanner] = None,
    candidates: Optional[CandidateSet] = None,
    policy_context: Optional[PolicyContext] = None,
    meta: ContractMetadata,
) -> PlannerInvocation:
    """Invoke the selected planner with the deterministic fallback path.

    Only adaptive failures fall back: if the adaptive interface is missing,
    unavailable, or raises while proposing, the deterministic planner runs and
    the evidence hook records the fallback reason. Errors raised by the
    deterministic planner propagate unchanged — it remains the authoritative
    default and its failures are never masked.
    """

    if selection.selected is PlannerKind.DETERMINISTIC:
        result = deterministic.propose(planner_input)
        evidence = PlannerEvaluationEvidence(
            meta=meta,
            request_ref=planner_input.request_id,
            planner_type_selected=PlannerKind.DETERMINISTIC.value,
            evaluation_status=PlannerEvaluationStatus.NOT_EVALUATED,
            fallback=False,
            fallback_reason=None,
            strategy_metadata=execution_strategy_metadata(result),
        )
        return PlannerInvocation(
            meta=meta,
            request_ref=planner_input.request_id,
            planner_used=PlannerKind.DETERMINISTIC,
            result=result,
            evidence=evidence,
            used_fallback=False,
        )

    if adaptive is None:
        return _fallback_invocation(
            meta=meta,
            planner_input=planner_input,
            deterministic=deterministic,
            fallback_reason="adaptive planner not configured",
        )
    try:
        bounded_candidates = candidates if candidates is not None else CandidateSet(
            meta=meta,
            request_ref=planner_input.request_id,
            candidates=(),
        )
        bounded_policy = policy_context if policy_context is not None else PolicyContext(
            meta=meta,
            policy_id=str(getattr(adaptive, "policy_id", "unspecified")),
            policy_version=str(getattr(adaptive, "policy_version", "unspecified")),
            evaluated_envelope={},
            pinned=False,
        )
        result = adaptive.propose(planner_input, bounded_candidates, bounded_policy)
    except (
        AdaptivePlannerUnavailable,
        AdaptivePlannerError,
        ContractError,
    ) as error:
        reason = f"adaptive planner failed: {error}"
        return _fallback_invocation(
            meta=meta,
            planner_input=planner_input,
            deterministic=deterministic,
            fallback_reason=reason,
        )
    evidence = PlannerEvaluationEvidence(
        meta=meta,
        request_ref=planner_input.request_id,
        planner_type_selected=PlannerKind.ADAPTIVE.value,
        evaluation_status=PlannerEvaluationStatus.EVALUATED,
        fallback=False,
        fallback_reason=None,
        strategy_metadata=execution_strategy_metadata(result),
    )
    return PlannerInvocation(
        meta=meta,
        request_ref=planner_input.request_id,
        planner_used=PlannerKind.ADAPTIVE,
        result=result,
        evidence=evidence,
        used_fallback=False,
    )


def _fallback_invocation(
    *,
    meta: ContractMetadata,
    planner_input: PlannerInput,
    deterministic: DeterministicPlanner,
    fallback_reason: str,
) -> PlannerInvocation:
    """Run the deterministic planner after an adaptive failure and record it."""

    result = deterministic.propose(planner_input)
    evidence = PlannerEvaluationEvidence(
        meta=meta,
        request_ref=planner_input.request_id,
        planner_type_selected=PlannerKind.DETERMINISTIC.value,
        evaluation_status=PlannerEvaluationStatus.FELL_BACK,
        fallback=True,
        fallback_reason=fallback_reason,
        strategy_metadata=execution_strategy_metadata(result),
    )
    return PlannerInvocation(
        meta=meta,
        request_ref=planner_input.request_id,
        planner_used=PlannerKind.DETERMINISTIC,
        result=result,
        evidence=evidence,
        used_fallback=True,
    )
