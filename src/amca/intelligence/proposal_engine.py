"""M1.1 bounded adaptive proposal engine.

Additive module introduced by the M1.1 Adaptive Proposal Engine milestone
(zcode/amca-v0.5.0-m1.1-adaptive-proposal-engine-01). Implements an offline,
deterministic, auditable proposal engine over explicit candidate descriptors:

- hard constraints (cost, work-unit bounds, risk, permissions) are enforced
  BEFORE any scoring, so only admissible candidates can be selected;
- scoring is bounded to the constraint-declared dimension whitelist, rejects
  unknown dimensions and non-finite scores per candidate, and ranks
  deterministically (ascending total, candidate-id tie-break);
- every failure mode is structured: no eligible candidate yields a BLOCKED
  proposal, an evaluator exception yields a FALLBACK_SIGNAL the caller maps
  to the deterministic planner, and malformed/duplicate/tampered records are
  rejected fail-closed.

The engine is a passive library: the adaptive_planner feature gate remains
FALSE, no pre-existing application code imports this package, the engine is
unreachable from existing production execution, and tests may instantiate it
directly. It never calls a model provider and never mutates canonical state.
"""

import dataclasses
import enum
import math
from typing import Any, Callable, ClassVar, Mapping, Optional, Tuple

from .canonical import content_digest
from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    contract_post_init,
)


class CandidateRiskClass(enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_RISK_ORDER = {
    CandidateRiskClass.LOW: 0,
    CandidateRiskClass.MEDIUM: 1,
    CandidateRiskClass.HIGH: 2,
}


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


def _finite(value, field: str, *, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be a number"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be finite"
        )
    if minimum is not None and numeric < minimum:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be >= {minimum}"
        )
    return numeric


@dataclasses.dataclass(frozen=True)
class ProposalCandidate(Contract):
    """One explicit, self-describing strategy candidate (no hidden state)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.proposal_candidate"

    meta: ContractMetadata
    candidate_id: str
    label: str
    collaboration_mode: str
    synchronization_mode: str
    estimated_cost: float
    work_unit_count: int
    required_permissions: Tuple[str, ...]
    risk_class: CandidateRiskClass

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        _finite(self.estimated_cost, "estimated_cost", minimum=0.0)
        if isinstance(self.work_unit_count, bool) or not isinstance(
            self.work_unit_count, int
        ) or self.work_unit_count < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "work_unit_count must be a positive integer",
            )


@dataclasses.dataclass(frozen=True)
class ProposalConstraints(Contract):
    """The hard envelope candidates must satisfy before any scoring."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.proposal_constraints"

    meta: ContractMetadata
    max_total_cost: float
    max_work_units: int
    allowed_permissions: Tuple[str, ...]
    max_allowed_risk: CandidateRiskClass
    scoring_dimensions: Tuple[str, ...]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        _finite(self.max_total_cost, "max_total_cost", minimum=0.0)
        if isinstance(self.max_work_units, bool) or not isinstance(
            self.max_work_units, int
        ) or self.max_work_units < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "max_work_units must be a positive integer",
            )
        if not self.scoring_dimensions:
            raise ContractError(
                ContractErrorCode.INVALID,
                "scoring_dimensions must declare at least one dimension",
            )
        if len(set(self.scoring_dimensions)) != len(self.scoring_dimensions):
            raise ContractError(
                ContractErrorCode.INVALID,
                "scoring_dimensions must not repeat a dimension",
            )


class CandidateRejected(Exception):
    """Internal: a single candidate failed scoring validation."""

    def __init__(self, candidate_id: str, reason_code: str, detail: str) -> None:
        super().__init__(f"{candidate_id}: {reason_code}: {detail}")
        self.candidate_id = candidate_id
        self.reason_code = reason_code
        self.detail = detail


CandidateScorer = Callable[[ProposalCandidate], Mapping[str, float]]


@dataclasses.dataclass(frozen=True)
class RejectedCandidate(Contract):
    """One candidate refused by a hard constraint or score validation."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.rejected_candidate"

    meta: ContractMetadata
    candidate_id: str
    reason_code: str
    detail: str

    def __post_init__(self):
        contract_post_init(self)


class AdaptiveProposalStatus(enum.Enum):
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    FALLBACK_SIGNAL = "fallback_signal"


#: The only evaluation-evidence keys the engine records. Counts and selected
#: candidate identity are bounded metadata; no candidate payload, score
#: matrix, or evaluator internals are captured.
ALLOWED_PROPOSAL_EVIDENCE_KEYS = frozenset(
    {
        "candidate_count",
        "admissible_count",
        "rejected_count",
        "scoring_dimensions",
        "selected_candidate_id",
        "selected_total_score",
        "selected_estimated_cost",
    }
)


def _proposal_evidence(
    *,
    candidate_count: int,
    admissible_count: int,
    rejected_count: int,
    scoring_dimensions: Tuple[str, ...],
    selected: Optional[ProposalCandidate],
    selected_total_score: Optional[float],
) -> Mapping[str, Any]:
    evidence: dict[str, Any] = {
        "candidate_count": candidate_count,
        "admissible_count": admissible_count,
        "rejected_count": rejected_count,
        "scoring_dimensions": list(scoring_dimensions),
    }
    if selected is not None:
        evidence["selected_candidate_id"] = selected.candidate_id
    if selected_total_score is not None:
        evidence["selected_total_score"] = selected_total_score
        cost = selected.estimated_cost if selected is not None else None
        if cost is not None:
            evidence["selected_estimated_cost"] = cost
    unexpected = set(evidence) - ALLOWED_PROPOSAL_EVIDENCE_KEYS
    if unexpected:
        raise ContractError(
            ContractErrorCode.INTEGRITY_VIOLATION,
            f"proposal evidence keys outside the whitelist: {sorted(unexpected)}",
        )
    return evidence


@dataclasses.dataclass(frozen=True)
class AdaptiveProposal(Contract):
    """Structured, auditable outcome of one bounded proposal evaluation.

    The evaluated constraints are carried inline and bound to
    ``constraints_digest`` so a tampered digest or a modified constraint
    record fails closed on read.
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.adaptive_proposal"

    meta: ContractMetadata
    request_ref: str
    status: AdaptiveProposalStatus
    constraints: ProposalConstraints
    constraints_digest: str
    selected: Optional[ProposalCandidate]
    ranked: Tuple[ProposalCandidate, ...]
    rejected: Tuple[RejectedCandidate, ...]
    fallback_reason: Optional[str]
    evidence: Mapping[str, Any]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.constraints_digest != content_digest(self.constraints):
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                "proposal constraints_digest does not match the bound "
                "constraints record",
            )
        unexpected = set(self.evidence) - ALLOWED_PROPOSAL_EVIDENCE_KEYS
        if unexpected:
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                f"proposal evidence keys outside the whitelist: "
                f"{sorted(unexpected)}",
            )
        if self.status is AdaptiveProposalStatus.PROPOSED:
            if self.selected is None or not self.ranked:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a proposed outcome requires a selected candidate and ranking",
                )
            if self.ranked[0].candidate_id != self.selected.candidate_id:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "the selected candidate must rank first",
                )
            if self.fallback_reason is not None:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a proposed outcome must not carry a fallback reason",
                )
        elif self.status is AdaptiveProposalStatus.BLOCKED:
            if self.selected is not None or self.ranked:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a blocked outcome must not carry a selection or ranking",
                )
        else:  # FALLBACK_SIGNAL
            if not self.fallback_reason:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a fallback signal requires a fallback reason",
                )
            if self.selected is not None or self.ranked:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a fallback signal must not carry a selection or ranking",
                )


def _hard_constraint_rejection(
    candidate: ProposalCandidate, constraints: ProposalConstraints
) -> Optional[Tuple[str, str]]:
    """Return (reason_code, detail) when a hard constraint refuses the
    candidate, or None when it is admissible. Enforced strictly before any
    scoring so only admissible candidates can ever be selected."""

    if candidate.estimated_cost > constraints.max_total_cost:
        return (
            "cost_limit",
            f"estimated_cost {candidate.estimated_cost} exceeds "
            f"max_total_cost {constraints.max_total_cost}",
        )
    if candidate.work_unit_count > constraints.max_work_units:
        return (
            "work_unit_bound",
            f"work_unit_count {candidate.work_unit_count} exceeds "
            f"max_work_units {constraints.max_work_units}",
        )
    if _RISK_ORDER[candidate.risk_class] > _RISK_ORDER[constraints.max_allowed_risk]:
        return (
            "risk_exceeded",
            f"risk_class {candidate.risk_class.value} exceeds "
            f"max_allowed_risk {constraints.max_allowed_risk.value}",
        )
    missing = [
        permission
        for permission in candidate.required_permissions
        if permission not in constraints.allowed_permissions
    ]
    if missing:
        return (
            "permission_not_granted",
            f"required permissions outside the allowed set: {missing}",
        )
    return None


def _validate_scores(
    candidate: ProposalCandidate,
    scores: Mapping[str, float],
    scoring_dimensions: Tuple[str, ...],
) -> float:
    """Validate one evaluator score mapping and return its bounded total."""

    declared = set(scoring_dimensions)
    unknown = sorted(set(scores) - declared)
    if unknown:
        raise CandidateRejected(
            candidate.candidate_id,
            "unknown_scoring_dimension",
            f"evaluator returned undeclared dimensions: {unknown}",
        )
    total = 0.0
    for dimension, value in scores.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CandidateRejected(
                candidate.candidate_id,
                "non_finite_score",
                f"dimension {dimension!r} score is not a number",
            )
        numeric = float(value)
        if not math.isfinite(numeric):
            raise CandidateRejected(
                candidate.candidate_id,
                "non_finite_score",
                f"dimension {dimension!r} score is not finite",
            )
        total += numeric
    return total


def propose(
    *,
    meta: ContractMetadata,
    request_ref: str,
    candidates: Tuple[ProposalCandidate, ...],
    constraints: ProposalConstraints,
    evaluator: CandidateScorer,
) -> AdaptiveProposal:
    """Run one bounded, deterministic proposal evaluation.

    Order of operations: structural integrity (duplicate/conflicting ids),
    hard-constraint admissibility, then bounded scoring of admissible
    candidates only. Evaluator exceptions map to a FALLBACK_SIGNAL so the
    caller can route to the deterministic planner; score-content violations
    reject the individual candidate. Identical inputs produce identical
    proposals (no randomness anywhere).
    """

    constraints_digest = content_digest(constraints)
    identifiers = [candidate.candidate_id for candidate in candidates]
    duplicates = sorted({i for i in identifiers if identifiers.count(i) > 1})
    if duplicates:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"conflicting duplicate candidate identifiers: {duplicates}",
        )

    rejected: list[RejectedCandidate] = []
    admissible: list[ProposalCandidate] = []
    for candidate in candidates:
        verdict = _hard_constraint_rejection(candidate, constraints)
        if verdict is not None:
            reason_code, detail = verdict
            rejected.append(
                RejectedCandidate(
                    meta=meta,
                    candidate_id=candidate.candidate_id,
                    reason_code=reason_code,
                    detail=detail,
                )
            )
        else:
            admissible.append(candidate)

    scored: list[Tuple[float, str, ProposalCandidate]] = []
    try:
        for candidate in admissible:
            try:
                scores = evaluator(candidate)
                if not isinstance(scores, Mapping):
                    raise CandidateRejected(
                        candidate.candidate_id,
                        "invalid_scores",
                        "evaluator did not return a mapping",
                    )
                total = _validate_scores(
                    candidate, scores, constraints.scoring_dimensions
                )
            except CandidateRejected as rejection:
                # Score-content violations reject the individual candidate;
                # the remaining admissible candidates keep being evaluated.
                rejected.append(
                    RejectedCandidate(
                        meta=meta,
                        candidate_id=rejection.candidate_id,
                        reason_code=rejection.reason_code,
                        detail=rejection.detail,
                    )
                )
                continue
            scored.append((total, candidate.candidate_id, candidate))
    except Exception as error:  # evaluator failure -> deterministic fallback signal
        return AdaptiveProposal(
            meta=meta,
            request_ref=request_ref,
            status=AdaptiveProposalStatus.FALLBACK_SIGNAL,
            constraints=constraints,
            selected=None,
            ranked=(),
            rejected=tuple(rejected),
            constraints_digest=constraints_digest,
            fallback_reason=f"evaluator failed: {error}",
            evidence=_proposal_evidence(
                candidate_count=len(candidates),
                admissible_count=len(admissible),
                rejected_count=len(rejected),
                scoring_dimensions=constraints.scoring_dimensions,
                selected=None,
                selected_total_score=None,
            ),
        )

    if not scored:
        return AdaptiveProposal(
            meta=meta,
            request_ref=request_ref,
            status=AdaptiveProposalStatus.BLOCKED,
            constraints=constraints,
            selected=None,
            ranked=(),
            rejected=tuple(rejected),
            constraints_digest=constraints_digest,
            fallback_reason=None,
            evidence=_proposal_evidence(
                candidate_count=len(candidates),
                admissible_count=0,
                rejected_count=len(rejected),
                scoring_dimensions=constraints.scoring_dimensions,
                selected=None,
                selected_total_score=None,
            ),
        )

    scored.sort(key=lambda entry: (entry[0], entry[1]))
    ranked = [entry[2] for entry in scored]
    best_total, _, selected = scored[0]
    return AdaptiveProposal(
        meta=meta,
        request_ref=request_ref,
        status=AdaptiveProposalStatus.PROPOSED,
        constraints=constraints,
        selected=selected,
        ranked=tuple(ranked),
        rejected=tuple(rejected),
        constraints_digest=constraints_digest,
        fallback_reason=None,
        evidence=_proposal_evidence(
            candidate_count=len(candidates),
            admissible_count=len(admissible),
            rejected_count=len(rejected),
            scoring_dimensions=constraints.scoring_dimensions,
            selected=selected,
            selected_total_score=best_total,
        ),
    )
