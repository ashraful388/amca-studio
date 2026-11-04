"""M4 model router foundation (bridge over the existing model abstraction).

Additive module introduced by the M4 Model Router Foundation milestone
(zcode/amca-v0.5.0-m4-model-router-foundation-01).

Critical reuse rule honored: the EXISTING deterministic model selection is
reused, not replaced. ``route_adapters`` delegates eligibility + ranking for
real ``ModelAdapter`` objects to ``amca.routing.ModelRouter.select`` (the
v0.4.x deterministic router: privacy-mode and compute-policy filtering,
inventory awareness, locality ranking, adapter-id tie-break). The new
envelope path (``route``) works on uniform ``RoutingCandidate`` records that
represent local and API models identically, re-applying the same ordering
philosophy: authorized-candidate filtering happens strictly BEFORE ranking,
hard budget/policy constraints fail closed, and ranking is deterministic
(cost, then latency, then candidate-id tie-break).

Authority rules: the router never creates provider authority — it only ranks
and rejects; it cannot invent providers or models (candidates must be
locally supplied); evidence is whitelisted and privacy-safe; every failure
is a structured rejection with a machine-readable reason.

Import-isolated as in M0-M3: nothing in the pre-existing application imports
this package, the model_router gate remains FALSE, production workflows are
unchanged, and tests may instantiate the foundation directly. No real or
paid model calls exist anywhere in this module.
"""

import dataclasses
import enum
import math
from typing import Any, Callable, ClassVar, Mapping, Optional, Sequence, Tuple

from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    contract_post_init,
)
from .gates import FeatureGateRegistry


def require_router_gate(gates: FeatureGateRegistry) -> None:
    """Refuse the router path while the model_router gate is disabled."""

    if not gates.is_enabled("model_router"):
        raise ContractError(
            ContractErrorCode.INVALID,
            "model routing requested while the model_router gate is "
            "disabled; refusing instead of activating the router",
        )


def _finite(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


@dataclasses.dataclass(frozen=True)
class RoutingCandidate(Contract):
    """One uniformly represented model candidate (local or API)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.routing_candidate"

    meta: ContractMetadata
    candidate_id: str
    provider: str
    model: str
    local_execution: bool
    capabilities: Tuple[str, ...]
    estimated_cost: Optional[float]
    latency_ms: Optional[float]
    available: bool

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        for field_name in ("estimated_cost", "latency_ms"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not _finite(value) or value < 0:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"{field_name} must be a non-negative finite number",
                )


@dataclasses.dataclass(frozen=True)
class RoutingPolicy(Contract):
    """The hard eligibility envelope applied before any ranking."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.routing_policy"

    meta: ContractMetadata
    allowed_providers: Tuple[str, ...]
    required_capabilities: Tuple[str, ...]
    local_only: bool
    cloud_disabled: bool
    max_budget: float
    strict_unknown_cost: bool
    maximum: int

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if not _finite(self.max_budget) or self.max_budget < 0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "max_budget must be a non-negative finite number",
            )
        if isinstance(self.maximum, bool) or not isinstance(self.maximum, int) \
                or self.maximum < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "maximum must be a positive integer"
            )


class RoutingOutcomeStatus(enum.Enum):
    ROUTED = "routed"
    BLOCKED = "blocked"


ALLOWED_ROUTING_EVIDENCE_KEYS = frozenset(
    {
        "candidate_count",
        "eligible_count",
        "rejected_count",
        "skipped_count",
        "selected_candidate_id",
        "policy_index",
    }
)


@dataclasses.dataclass(frozen=True)
class RejectedCandidate(Contract):
    """One candidate refused by an eligibility rule (reason-coded)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.routing_rejected_candidate"

    meta: ContractMetadata
    candidate_id: str
    reason_code: str
    detail: str

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class RoutingOutcome(Contract):
    """Structured, auditable outcome of one bounded routing decision."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.routing_outcome"

    meta: ContractMetadata
    request_ref: str
    status: RoutingOutcomeStatus
    selected: Optional[RoutingCandidate]
    ranked: Tuple[RoutingCandidate, ...]
    rejected: Tuple[RejectedCandidate, ...]
    evidence: Mapping[str, Any]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        unexpected = set(self.evidence) - ALLOWED_ROUTING_EVIDENCE_KEYS
        if unexpected:
            raise ContractError(
                ContractErrorCode.INTEGRITY_VIOLATION,
                f"routing evidence keys outside the whitelist: "
                f"{sorted(unexpected)}",
            )
        if self.status is RoutingOutcomeStatus.ROUTED:
            if self.selected is None or not self.ranked:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a routed outcome requires a selected candidate",
                )
            if self.ranked[0].candidate_id != self.selected.candidate_id:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "the selected candidate must rank first",
                )
        elif self.selected is not None or self.ranked:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a blocked outcome must not carry a selection or ranking",
            )


def _eligibility_rejection(
    candidate: RoutingCandidate, policy: RoutingPolicy
) -> Optional[Tuple[str, str]]:
    """Hard eligibility rules, applied strictly BEFORE any ranking."""

    if not candidate.available:
        return None  # unavailable candidates are skipped, not rejected
    if policy.allowed_providers and candidate.provider not in policy.allowed_providers:
        return (
            "unauthorized_provider",
            f"provider {candidate.provider!r} is not in the allowed set",
        )
    missing = [
        capability
        for capability in policy.required_capabilities
        if capability not in candidate.capabilities
    ]
    if missing:
        return (
            "missing_required_capability",
            f"missing required capabilities: {missing}",
        )
    if (policy.local_only or policy.cloud_disabled) and not candidate.local_execution:
        return (
            "cloud_policy_excluded",
            "non-local candidate excluded by "
            + ("local-only" if policy.local_only else "cloud-disabled")
            + " policy",
        )
    if candidate.estimated_cost is None:
        if policy.strict_unknown_cost:
            return (
                "unknown_cost_strict_budget",
                "candidate cost is unknown and the budget policy is strict",
            )
    elif candidate.estimated_cost > policy.max_budget:
        return (
            "budget_limit",
            f"estimated_cost {candidate.estimated_cost} exceeds "
            f"max_budget {policy.max_budget}",
        )
    return None


def _rank_key(candidate: RoutingCandidate) -> Tuple[float, float, str]:
    """Deterministic ranking: cost, then latency, then candidate id.

    Unknown cost/latency sort last so known-finite candidates win; the
    candidate-id tie-break keeps identical inputs identical.
    """

    cost = candidate.estimated_cost if candidate.estimated_cost is not None else math.inf
    latency = candidate.latency_ms if candidate.latency_ms is not None else math.inf
    return (cost, latency, candidate.candidate_id)


def route(
    *,
    meta: ContractMetadata,
    request_ref: str,
    candidates: Tuple[RoutingCandidate, ...],
    policy: RoutingPolicy,
    gates: FeatureGateRegistry,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> RoutingOutcome:
    """One bounded routing decision over locally supplied candidates.

    Gate-off in the normal activation path refuses; duplicate identifiers
    fail closed; eligibility runs before ranking; unavailable candidates are
    skipped; exhausted candidates yield a structured BLOCKED outcome. The
    router only ranks — it never creates provider authority or executes
    anything.
    """

    require_router_gate(gates)
    if cancel_check is not None and cancel_check():
        raise ContractError(ContractErrorCode.CANCELLED, "routing cancelled")

    identifiers = [candidate.candidate_id for candidate in candidates]
    duplicates = sorted({i for i in identifiers if identifiers.count(i) > 1})
    if duplicates:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"conflicting duplicate candidate identifiers: {duplicates}",
        )

    rejected: list[RejectedCandidate] = []
    eligible: list[RoutingCandidate] = []
    skipped = 0
    for candidate in candidates:
        verdict = _eligibility_rejection(candidate, policy)
        if verdict is None:
            if not candidate.available:
                # unavailable candidates are skipped, never ranked
                skipped += 1
                continue
            eligible.append(candidate)
            continue
        reason_code, detail = verdict
        rejected.append(
            RejectedCandidate(
                meta=meta,
                candidate_id=candidate.candidate_id,
                reason_code=reason_code,
                detail=detail,
            )
        )

    def evidence(selected, total_eligible: int) -> dict:
        payload = {
            "candidate_count": len(candidates),
            "eligible_count": total_eligible,
            "rejected_count": len(rejected),
            "skipped_count": skipped,
        }
        if selected is not None:
            payload["selected_candidate_id"] = selected.candidate_id
        return payload

    if not eligible:
        return RoutingOutcome(
            meta=meta,
            request_ref=request_ref,
            status=RoutingOutcomeStatus.BLOCKED,
            selected=None,
            ranked=(),
            rejected=tuple(rejected),
            evidence=evidence(None, 0),
        )

    ranked = sorted(eligible, key=_rank_key)[: policy.maximum]
    selected = ranked[0]
    return RoutingOutcome(
        meta=meta,
        request_ref=request_ref,
        status=RoutingOutcomeStatus.ROUTED,
        selected=selected,
        ranked=tuple(ranked),
        rejected=tuple(rejected),
        evidence=evidence(selected, len(eligible)),
    )


def route_with_fallback(
    *,
    meta: ContractMetadata,
    request_ref: str,
    candidates: Tuple[RoutingCandidate, ...],
    policies: Sequence[RoutingPolicy],
    gates: FeatureGateRegistry,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[RoutingOutcome, int]:
    """Walk a bounded fallback chain of policies.

    The first policy that routes wins; every rejected candidate accumulates
    across the chain; exhausting the chain yields one BLOCKED outcome.
    Cancellation halts the fallback walk immediately.
    """

    if not policies:
        raise ContractError(
            ContractErrorCode.INVALID, "the fallback chain must contain at least one policy"
        )
    accumulated: list[RejectedCandidate] = []
    for index, policy in enumerate(policies):
        if cancel_check is not None and cancel_check():
            raise ContractError(ContractErrorCode.CANCELLED, "routing cancelled")
        outcome = route(
            meta=meta,
            request_ref=request_ref,
            candidates=candidates,
            policy=policy,
            gates=gates,
        )
        if outcome.status is RoutingOutcomeStatus.ROUTED:
            evidence = dict(outcome.evidence)
            evidence["policy_index"] = index
            return (
                RoutingOutcome(
                    meta=meta,
                    request_ref=request_ref,
                    status=outcome.status,
                    selected=outcome.selected,
                    ranked=outcome.ranked,
                    rejected=tuple(accumulated) + outcome.rejected,
                    evidence=evidence,
                ),
                index,
            )
        accumulated.extend(outcome.rejected)
    return (
        RoutingOutcome(
            meta=meta,
            request_ref=request_ref,
            status=RoutingOutcomeStatus.BLOCKED,
            selected=None,
            ranked=(),
            rejected=tuple(accumulated),
            evidence={
                "candidate_count": len(candidates),
                "eligible_count": 0,
                "rejected_count": len(accumulated),
                "skipped_count": 0,
            },
        ),
        len(policies),
    )


def _capabilities_tuple(caps) -> Tuple[str, ...]:
    """Project an existing ModelCapabilities record into capability names."""

    names = []
    for field in dataclasses.fields(caps):
        value = getattr(caps, field.name)
        if isinstance(value, bool) and value:
            names.append(field.name)
    return tuple(names)


def route_adapters(
    *,
    meta_factory: Callable[[str], ContractMetadata],
    request_ref: str,
    state,
    adapters,
    gates: FeatureGateRegistry,
    maximum: Optional[int] = None,
    local_runtime=None,
) -> Tuple[RoutingCandidate, ...]:
    """Bridge to the EXISTING deterministic router for real model adapters.

    Delegates eligibility + ranking to ``amca.routing.ModelRouter.select``
    (privacy-mode/compute-policy filtering, inventory awareness, locality
    ranking, adapter-id tie-break) and seals the chosen adapters into
    uniform RoutingCandidate envelopes. Selection only — no provider
    authority is created and no call is made.
    """

    require_router_gate(gates)
    from ..routing import ModelRouter

    chosen = ModelRouter().select(
        state, adapters, maximum=maximum, local_runtime=local_runtime
    )
    return tuple(
        RoutingCandidate(
            meta=meta_factory(f"routed-{index}"),
            candidate_id=adapter.adapter_id,
            provider=adapter.provider,
            model=adapter.model,
            local_execution=bool(adapter.capabilities().is_local),
            capabilities=_capabilities_tuple(adapter.capabilities()),
            estimated_cost=None,
            latency_ms=None,
            available=True,
        )
        for index, adapter in enumerate(chosen)
    )
