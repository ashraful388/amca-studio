"""The canonical Model Router — the ONE place that selects a model.

Work Order 09, Task F, manager decision 2026-09-19.  The audit found three live
selectors answering the same question differently: Auto ranked by a model-NAME
quality heuristic, the CLI router ranked by locality and compute policy, and
`AutoIntelligenceRouter.resolve` ranked by cost under its own default.  Same
goal, same models, three answers.  That is not a tuning difference; it is
multiple production authorities.

This module replaces them.  The architecture is:

    Model Registry = FACTS
    Model Router   = SELECTION

Two rules the manager was explicit about, and which this module enforces:

1. **Name-based quality and reliability do not influence routing.**  "opus",
   "pro", "mini", "flash", a parameter count in the model id — none of these
   are measurements.  They are removed, not converted into Registry facts,
   because converting a guess into a fact does not make it measured.  Where
   quality is unknown it stays UNKNOWN.
2. **Cost policy is explicit.**  A hidden boolean default is not a product
   policy.  The caller states a policy, or one documented global default
   applies — never one default here and another there.

Selection runs in the order the directive fixed: hard filters FIRST, then soft
ranking, then a neutral deterministic tie-break that never claims a winner is
"higher quality" without evidence.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


class CostPolicy(str, enum.Enum):
    """What the caller wants cost to do.  Always explicit, never defaulted per-caller."""

    ECONOMY = "economy"                 # prefer the cheapest adequate model
    BALANCED = "balanced"               # cost is one factor among several
    COST_INSENSITIVE = "cost_insensitive"  # ignore cost entirely


#: The ONE documented global default, used only when a caller states nothing.
#: The manager's rule: not one default in Auto and another in CLI.
DEFAULT_COST_POLICY = CostPolicy.BALANCED


class TieBreak(str, enum.Enum):
    """Why the winner won.  Never "higher quality" without evidence."""

    MEASURED_DIFFERENTIATION = "measured_differentiation"
    INSUFFICIENT_MEASURED_DIFFERENTIATION = "insufficient_measured_differentiation"



class MetricUnit(str, enum.Enum):
    """The unit a measurement is in.  Unlike metrics are never merged."""

    TOKENS_PER_SECOND = "tokens/sec"
    MILLISECONDS = "ms"
    SECONDS = "s"
    BOOLEAN = "bool"
    UNKNOWN = "unknown"


#: Canonical metric names.  Adding one is a deliberate act, not a string.
METRIC_GENERATION_THROUGHPUT = "generation_throughput"
METRIC_TIME_TO_FIRST_TOKEN = "time_to_first_token"
METRIC_REQUEST_LATENCY = "request_latency"
METRIC_MODEL_LOAD_SUCCESS = "model_load_success"


@dataclass(frozen=True)
class PerformanceEvidence:
    """One MEASURED fact about how a model behaved in a stated environment.

    The manager's requirements, section 1: evidence must be generic enough that
    local and remote measurements use the same architecture, and must carry its
    SCOPE — a measurement on this machine is not a universal fact about the
    model.  Section 5: an unmeasured metric is None (UNKNOWN), never 0, so a
    model without evidence is not falsely labelled slow.
    """

    model_id: str
    metric: str
    value: float | bool | None = None      # None = UNKNOWN, never a stand-in
    unit: MetricUnit = MetricUnit.UNKNOWN
    source: str = "measured"
    measured_at: str = ""
    environment_scope: str = ""
    sample_count: int = 0
    benchmark: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit.value if isinstance(self.unit, MetricUnit) else self.unit,
            "source": self.source,
            "measured_at": self.measured_at,
            "environment_scope": self.environment_scope,
            "sample_count": self.sample_count,
            "benchmark": self.benchmark,
        }


def throughput_of(evidence: Iterable[PerformanceEvidence]) -> float | None:
    """Measured generation throughput, or None when nobody measured it."""

    for item in evidence or ():
        if item.metric == METRIC_GENERATION_THROUGHPUT and item.known:
            try:
                return float(item.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
    return None


def latency_ms_of(evidence: Iterable[PerformanceEvidence]) -> float | None:
    """Measured request latency in milliseconds, normalised only within its own unit.

    A seconds measurement is converted to milliseconds because that is the same
    metric in a different unit.  Throughput is NOT converted into latency: those
    are unlike metrics, and section 3 forbids merging them into a score.
    """

    for item in evidence or ():
        if item.metric == METRIC_REQUEST_LATENCY and item.known:
            try:
                value = float(item.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            return value * 1000.0 if item.unit is MetricUnit.SECONDS else value
        if item.metric == METRIC_TIME_TO_FIRST_TOKEN and item.known:
            try:
                value = float(item.value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            return value * 1000.0 if item.unit is MetricUnit.SECONDS else value
    return None


def load_failed(evidence: Iterable[PerformanceEvidence]) -> bool:
    """Whether evidence establishes the model cannot load at all.

    Section 2: an established inability to load or fit is an
    availability/executability fact, not a performance preference.
    """

    for item in evidence or ():
        if item.metric == METRIC_MODEL_LOAD_SUCCESS and item.known and item.value is False:
            return True
    return False


@dataclass(frozen=True)
class ModelSelectionRequest:
    """One structured request.  Every caller translates its inputs into this.

    Compatibility shims at the existing entry points build one of these rather
    than ranking anything themselves, which is what removes the contradiction:
    the old functions keep their signatures but their selection logic is gone.
    """

    task_type: str = ""
    required_capabilities: frozenset[str] = frozenset()
    preferred_capabilities: frozenset[str] = frozenset()
    required_tools: bool = False
    required_structured_output: bool = False
    required_vision: bool = False
    required_context: int = 0
    requires_local: bool = False
    requires_remote: bool = False
    allowed_models: frozenset[str] = frozenset()
    excluded_models: frozenset[str] = frozenset()
    manual_single: str = ""
    cost_policy: CostPolicy = DEFAULT_COST_POLICY
    max_cost_usd: float | None = None
    #: Prefer a local model when cost and health are otherwise equal.  This was
    #: previously an IMPLICIT rule buried in the ranking, which is how the three
    #: selectors came to disagree.  It is now an explicit opt-in the caller
    #: states, defaulting OFF so nothing prefers local by accident.  It is a
    #: policy, not a measurement, and it never overrides a hard filter.
    prefer_local: bool = False
    minimum_participants: int = 1
    maximum_participants: int = 1
    runtime_health: Any = None          # RunScopedHealth, duck-typed
    goal_summary: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "required_capabilities": sorted(self.required_capabilities),
            "preferred_capabilities": sorted(self.preferred_capabilities),
            "required_tools": self.required_tools,
            "required_structured_output": self.required_structured_output,
            "required_vision": self.required_vision,
            "required_context": self.required_context,
            "requires_local": self.requires_local,
            "requires_remote": self.requires_remote,
            "cost_policy": self.cost_policy.value,
            "prefer_local": self.prefer_local,
            "minimum_participants": self.minimum_participants,
            "maximum_participants": self.maximum_participants,
        }


@dataclass
class ModelCandidateFacts:
    """What is known about one candidate.

    Every field is either a Registry fact, an explicit user constraint, or
    evidence measured during a run.  There is deliberately NO quality or
    reliability field: those were the name heuristics, and they are gone.
    """

    model_ref: str
    provider: str = ""
    capabilities: frozenset[str] = frozenset()
    supports_tools: bool = False
    supports_json: bool = False
    supports_vision: bool = False
    context_window: int = 0             # 0 = unestablished, not "zero"
    is_local: bool = False
    visible: bool = True
    available: bool = True
    expected_cost_usd: float | None = None   # None = unknown, not free
    measured_latency_ms: float | None = None
    capability_source: str = ""
    #: Generic measured evidence.  Throughput, latency and load success all
    #: travel here so the router never learns a local-runtime special case.
    performance: tuple[PerformanceEvidence, ...] = ()
    #: True = runs here, False = established it cannot, None = unknown.  Only
    #: an established False is a hard ineligibility (section 2).
    hardware_fit: bool | None = None

    def facts_for_ranking(self) -> dict[str, Any]:
        return {
            "measured_latency_ms": self.measured_latency_ms,
            "expected_cost_usd": self.expected_cost_usd,
        }


@dataclass
class ExcludedModel:
    model_ref: str
    reason: str


@dataclass
class SelectionDecision:
    """An auditable selection.  Feeds Auto explanations and Execution History."""

    selected_model: str
    selected_models: tuple[str, ...] = ()
    eligible_models: tuple[str, ...] = ()
    excluded_models: tuple[ExcludedModel, ...] = ()
    ranked_candidates: tuple[str, ...] = ()
    hard_constraints_satisfied: tuple[str, ...] = ()
    soft_factors_used: tuple[str, ...] = ()
    unknown_factors: tuple[str, ...] = ()
    health_state: str = ""
    cost_evidence: str = ""
    performance_evidence: str = ""
    tie_break_reason: str = ""
    cost_policy: str = ""
    request_summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_model": self.selected_model,
            "selected_models": list(self.selected_models),
            "eligible_models": list(self.eligible_models),
            "excluded_models": [
                {"model_ref": item.model_ref, "reason": item.reason}
                for item in self.excluded_models
            ],
            "ranked_candidates": list(self.ranked_candidates),
            "hard_constraints_satisfied": list(self.hard_constraints_satisfied),
            "soft_factors_used": list(self.soft_factors_used),
            "unknown_factors": list(self.unknown_factors),
            "health_state": self.health_state,
            "cost_evidence": self.cost_evidence,
            "performance_evidence": self.performance_evidence,
            "tie_break_reason": self.tie_break_reason,
            "cost_policy": self.cost_policy,
            "request_summary": dict(self.request_summary),
        }

    def explain(self) -> str:
        """A short human explanation.  Never claims quality without evidence."""

        lines = [f"Selected {self.selected_model}."]
        if self.hard_constraints_satisfied:
            lines.append("Satisfied: " + ", ".join(self.hard_constraints_satisfied))
        if self.excluded_models:
            lines.append(
                "Excluded: "
                + "; ".join(f"{item.model_ref} ({item.reason})" for item in self.excluded_models)
            )
        if self.soft_factors_used:
            lines.append("Ranked on: " + ", ".join(self.soft_factors_used))
        if self.unknown_factors:
            lines.append("Unknown: " + ", ".join(self.unknown_factors))
        if self.tie_break_reason == TieBreak.INSUFFICIENT_MEASURED_DIFFERENTIATION.value:
            lines.append(
                "No measured evidence differentiated the candidates, so a neutral "
                "deterministic order decided. This is not a quality judgement."
            )
        return " ".join(lines)


def _hard_filter(
    candidate: ModelCandidateFacts, request: ModelSelectionRequest
) -> str:
    """Return an exclusion reason, or an empty string when the candidate passes.

    Runs BEFORE any ranking.  An incapable healthy model must not beat a
    capable degraded one, which is only true if capability is settled first.
    """

    if request.allowed_models and candidate.model_ref not in request.allowed_models:
        return "not in the caller's allowed set"
    if candidate.model_ref in request.excluded_models:
        return "excluded by the caller"
    if not candidate.visible:
        return "hidden by the user"
    if not candidate.available:
        return "not currently available"
    if candidate.hardware_fit is False:
        return "established that this model cannot run in the current environment"
    if load_failed(candidate.performance):
        return "measured evidence shows this model fails to load"
    if request.requires_local and not candidate.is_local:
        return "a local model is required"
    if request.requires_remote and candidate.is_local:
        return "a remote model is required"

    missing = request.required_capabilities - candidate.capabilities
    if missing:
        return "missing required capability " + ", ".join(sorted(missing))
    if request.required_tools and not candidate.supports_tools:
        return "tool support is required but not available"
    if request.required_structured_output and not candidate.supports_json:
        return "structured output is required but not available"
    if request.required_vision and not candidate.supports_vision:
        return "vision is required but not available"

    # A recorded window that is too small refuses; an unestablished window (0)
    # does not, because excluding every model nobody has measured would disable
    # routing entirely.  The same rule the CostQualityGate already documents.
    if request.required_context and candidate.context_window and (
        candidate.context_window < request.required_context
    ):
        return (
            f"context window {candidate.context_window} is below the required "
            f"{request.required_context}"
        )

    if request.max_cost_usd is not None:
        if candidate.expected_cost_usd is None:
            # A hard maximum cannot be checked against an unknown, so the
            # candidate is INELIGIBLE rather than assumed to be within budget.
            # This is deliberately different from the soft ECONOMY preference,
            # where an unknown cost stays unknown and merely ranks last: a limit
            # is a promise to the user about what may be spent, and an unpriced
            # model cannot be promised against.
            return "cost is unknown and a hard cost limit was set"
        if candidate.expected_cost_usd > request.max_cost_usd:
            return "cost exceeds the caller's limit"
    return ""


def _soft_key(
    candidate: ModelCandidateFacts,
    request: ModelSelectionRequest,
    health: Any,
) -> tuple[Any, ...]:
    """Staged ranking key, in the order the manager fixed (section 7):

        runtime availability / health
        -> explicit user / policy preferences
        -> measured performance, where relevant
        -> cost, according to the explicit cost policy
        -> other measured evidence
        -> neutral deterministic tie-break

    Deliberately a TUPLE of stages rather than one opaque weighted score, so
    the decision can say which factor actually mattered.  Unlike metrics are
    never merged: throughput is compared only with throughput, latency only with
    latency, and a model with no measurement sorts after one with a measurement
    rather than being scored as if it were slow (section 5).
    """

    # Stage 1 — runtime health for this run.
    health_rank = health.ranking_key(candidate.model_ref) if health is not None else 1

    # Stage 2 — explicit policy: a stated local preference, then stated
    # capability preferences.  Policy outranks measurement, as the order says.
    local_rank = (0 if candidate.is_local else 1) if request.prefer_local else 0
    preference_miss = len(request.preferred_capabilities - candidate.capabilities)

    # Stage 3 — measured performance, where relevant.  Higher throughput is
    # better; unmeasured sorts last within the stage.
    throughput = throughput_of(candidate.performance)
    throughput_rank = (0, -throughput) if throughput is not None else (1, 0.0)

    # Stage 4 — cost, only when the policy says cost matters.  Unknown cost is
    # not zero: it sorts after known cost rather than winning on a fake zero.
    if request.cost_policy is CostPolicy.COST_INSENSITIVE:
        cost_rank: tuple[Any, ...] = (0, 0.0)
    elif candidate.expected_cost_usd is None:
        cost_rank = (1, 0.0)
    else:
        cost_rank = (0, float(candidate.expected_cost_usd))

    # Stage 5 — other measured evidence: latency, preferring the lower figure.
    latency = candidate.measured_latency_ms
    if latency is None:
        latency = latency_ms_of(candidate.performance)
    latency_rank = (0, latency) if latency is not None else (1, 0.0)

    # Stage 6 — neutral deterministic tie-break.  Not a quality judgement.
    return (
        health_rank,
        local_rank,
        preference_miss,
        throughput_rank,
        cost_rank,
        latency_rank,
        candidate.model_ref,
    )


def _factors_that_mattered(
    chosen: ModelCandidateFacts,
    run_eligible: Sequence[ModelCandidateFacts],
    request: ModelSelectionRequest,
    health: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Which factors actually distinguished the winner, and which were unknown.

    The manager's requirement: the SelectionDecision must explain which factors
    mattered, rather than presenting a score.
    """

    used: list[str] = []
    unknown: list[str] = []

    if health is not None and len({health.ranking_key(c.model_ref) for c in run_eligible}) > 1:
        used.append("runtime_health")
    if request.prefer_local and len({c.is_local for c in run_eligible}) > 1:
        used.append("explicit_local_preference")
    if request.preferred_capabilities and len(
        {len(request.preferred_capabilities - c.capabilities) for c in run_eligible}
    ) > 1:
        used.append("preferred_capabilities")

    measured = [throughput_of(c.performance) for c in run_eligible]
    if any(value is not None for value in measured):
        used.append("measured_throughput")
    if any(value is None for value in measured):
        unknown.append("throughput_unknown_for_some_candidates")

    if request.cost_policy is not CostPolicy.COST_INSENSITIVE:
        costs = [c.expected_cost_usd for c in run_eligible]
        if any(value is not None for value in costs):
            used.append("known_cost")
        if any(value is None for value in costs):
            unknown.append("cost_unknown_for_some_candidates")

    latencies = [
        c.measured_latency_ms if c.measured_latency_ms is not None
        else latency_ms_of(c.performance)
        for c in run_eligible
    ]
    if any(value is not None for value in latencies):
        used.append("measured_latency")

    if chosen.hardware_fit is None:
        unknown.append("hardware_fit_unestablished")
    return tuple(used), tuple(unknown)


def select_model(
    candidates: Sequence[ModelCandidateFacts],
    request: ModelSelectionRequest,
) -> SelectionDecision:
    """The canonical selection.  Every subsystem calls this and nothing else."""

    excluded: list[ExcludedModel] = []
    eligible: list[ModelCandidateFacts] = []
    for candidate in candidates:
        reason = _hard_filter(candidate, request)
        if reason:
            excluded.append(ExcludedModel(candidate.model_ref, reason))
        else:
            eligible.append(candidate)

    if not eligible:
        return SelectionDecision(
            selected_model="",
            excluded_models=tuple(excluded),
            tie_break_reason="",
            cost_policy=request.cost_policy.value,
            request_summary=request.describe(),
        )

    health = request.runtime_health
    ordered = sorted(eligible, key=lambda item: _soft_key(item, request, health))

    # A momentary outage excludes a candidate from THIS run; it does not change
    # anything about what the model can do.
    run_eligible: list[ModelCandidateFacts] = []
    for candidate in ordered:
        if health is not None:
            skip, why = health.temporarily_excluded(candidate.model_ref)
            if skip:
                excluded.append(ExcludedModel(candidate.model_ref, why))
                continue
        run_eligible.append(candidate)
    if not run_eligible:
        return SelectionDecision(
            selected_model="",
            eligible_models=tuple(item.model_ref for item in ordered),
            excluded_models=tuple(excluded),
            cost_policy=request.cost_policy.value,
            request_summary=request.describe(),
        )

    soft_factors, unknown_factors = _factors_that_mattered(
        run_eligible[0], run_eligible, request, health
    )
    keys = [_soft_key(item, request, health) for item in run_eligible]
    # Measured differentiation means the ranking key differs on something other
    # than the final neutral tie-break component.
    differentiated = len({key[:-1] for key in keys}) > 1
    tie_break = (
        TieBreak.MEASURED_DIFFERENTIATION
        if differentiated
        else TieBreak.INSUFFICIENT_MEASURED_DIFFERENTIATION
    )

    count = max(1, min(request.maximum_participants, len(run_eligible)))
    chosen = run_eligible[:count]

    return SelectionDecision(
        selected_model=chosen[0].model_ref,
        selected_models=tuple(item.model_ref for item in chosen),
        eligible_models=tuple(item.model_ref for item in run_eligible),
        excluded_models=tuple(excluded),
        ranked_candidates=tuple(item.model_ref for item in run_eligible),
        hard_constraints_satisfied=tuple(
            name for name, on in (
                ("required_capabilities", bool(request.required_capabilities)),
                ("required_tools", request.required_tools),
                ("required_structured_output", request.required_structured_output),
                ("required_vision", request.required_vision),
                ("required_context", bool(request.required_context)),
                ("locality", request.requires_local or request.requires_remote),
                ("visibility", True),
                ("availability", True),
            ) if on
        ),
        soft_factors_used=tuple(soft_factors),
        unknown_factors=tuple(unknown_factors),
        health_state=(
            health.health_of(chosen[0].model_ref).value if health is not None else "unknown"
        ),
        cost_evidence=(
            "known" if chosen[0].expected_cost_usd is not None else "unknown"
        ),
        performance_evidence=(
            "measured" if chosen[0].measured_latency_ms is not None else "unknown"
        ),
        tie_break_reason=tie_break.value,
        cost_policy=request.cost_policy.value,
        request_summary=request.describe(),
    )



def evidence_from_local_runtime(snapshot: Any, provider: str, model: str) -> tuple[PerformanceEvidence, ...]:
    """Promote local-runtime measurements into canonical performance evidence.

    The manager's decision: the CLI should lose its independent ranking engine,
    but its REAL measurements must be promoted into canonical Router evidence —
    "local_runtime measurements -> canonical performance evidence -> Model
    Router", not "local_runtime -> special CLI-only ranking".

    This is a translation, not a ranking.  It reads what was measured and
    states it in the canonical form, including the environment scope, because a
    measurement taken on this machine is not a universal fact about the model
    (section 4).  A measurement that does not exist produces NO evidence rather
    than a zero (section 5).
    """

    out: list[PerformanceEvidence] = []
    if snapshot is None:
        return ()

    scope = str(getattr(snapshot, "hardware_profile_sha256", "") or "")[:32]
    try:
        measurement = snapshot.latest_successful_measurement(provider, model)
    except Exception:  # noqa: BLE001 - a snapshot that cannot answer yields no evidence
        measurement = None

    if measurement is not None:
        throughput = getattr(measurement, "tokens_per_second", None)
        if throughput:
            out.append(PerformanceEvidence(
                model_id=f"{provider}:{model}",
                metric=METRIC_GENERATION_THROUGHPUT,
                value=float(throughput),
                unit=MetricUnit.TOKENS_PER_SECOND,
                source="measured",
                measured_at=str(getattr(measurement, "measured_at", "") or ""),
                environment_scope=scope,
                sample_count=int(getattr(measurement, "sample_count", 0) or 0),
            ))
        if getattr(measurement, "success", None) is not None:
            out.append(PerformanceEvidence(
                model_id=f"{provider}:{model}",
                metric=METRIC_MODEL_LOAD_SUCCESS,
                value=bool(measurement.success),
                unit=MetricUnit.BOOLEAN,
                source="measured",
                environment_scope=scope,
            ))
    return tuple(out)


def hardware_fit_from_local_runtime(snapshot: Any, provider: str, model: str) -> bool | None:
    """Translate a local fit verdict into the canonical hardware-fit fact.

    Returns None when the snapshot cannot answer, because an unestablished fit
    is not the same as an established inability to run (section 2).
    """

    if snapshot is None:
        return None
    try:
        penalty = snapshot.fit_penalty(provider, model)
    except Exception:  # noqa: BLE001 - an unanswerable snapshot establishes nothing
        return None
    # The evaluator's verdict: 0 conservative fit, 1 unknown, 2 likely oversized.
    # Only an established "likely oversized" is a hard fact; unknown stays None.
    if penalty == 2:
        return False
    if penalty == 0:
        return True
    return None


__all__ = [
    "METRIC_GENERATION_THROUGHPUT",
    "METRIC_MODEL_LOAD_SUCCESS",
    "METRIC_REQUEST_LATENCY",
    "METRIC_TIME_TO_FIRST_TOKEN",
    "CostPolicy",
    "DEFAULT_COST_POLICY",
    "ExcludedModel",
    "ModelCandidateFacts",
    "ModelSelectionRequest",
    "MetricUnit",
    "PerformanceEvidence",
    "SelectionDecision",
    "TieBreak",
    "evidence_from_local_runtime",
    "hardware_fit_from_local_runtime",
    "select_model",
]
