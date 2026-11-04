"""Provider-neutral cost-accounting and budget-enforcement core (v0.6.0).

Built on top of the existing AMCA types:

  * :class:`amca.models.base.TokenUsage`            (per-call usage envelope)
  * :class:`amca.domain.CostState`                  (project aggregate)
  * :class:`amca.intelligence.contracts.CostMetadata` (preflight/estimated/measured)
  * :class:`amca.model_hub.PricingStatus`, :class:`amca.model_hub.CostStatus`
  * :class:`amca.call_batching.CostBoundKind`, :class:`amca.call_batching.CostReservation`
  * :class:`amca.intelligence.contracts.ModelCallResponse` (per-call settled facts)

This module is the v0.6.0 M2 cost/budget backend core. It does not perform
network I/O, does not depend on any single provider, and does not require a
new database schema. Persistence is intentionally delegated to the existing
``model_hub_task_costs`` and ``model_cost_ledgers`` tables — both of which
already ship in the v0.5.1 release.

Design goals (per the M2 work order):

  * Reuse existing usage/cost/pricing metadata; do not create a second
    cost ledger.
  * Local models (Ollama, local deterministic fallback) are zero API
    cost; we never pretend to measure electricity/hardware cost.
  * Unknown-cost API execution is **never** silently coerced to zero.
    Under a strict monetary budget, unknown-cost API calls fail closed.
  * Run-level budgets: ``BudgetEnforcer.evaluate(...)`` returns
    ALLOW or BLOCK with a structured reason before another billable
    model call is dispatched.
  * Project-level budget foundation: the project state already holds a
    per-project aggregate (``CostState``); the per-project monetary
    budget cap is stored in-memory on the accumulator and surfaced by
    the read API. No DB migration is required for this milestone.
  * No retry/fallback double counting. Only settled usage is
    recorded; cancellations and provider-failures-before-usage do not
    fabricate cost.
  * Privacy: evidence shape never carries API keys, authorization
    headers, session tokens, raw prompts, or raw model responses.

Currency: USD is the canonical currency of the v0.6.0 cost core. No
FX conversion is performed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum

# Local imports are deferred to runtime to keep the module importable
# even when amca is not yet installed editable (e.g. for unit tests
# that exercise the cost core in isolation).


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------


class BudgetVerdict(str, Enum):
    """Outcome of a budget evaluation for a prospective model call."""

    ALLOW = "allow"
    BLOCK = "block"


class BudgetBlockReason(str, Enum):
    """Why a budget evaluator refused a prospective call."""

    RUN_BUDGET_EXCEEDED = "run_budget_exceeded"
    PROJECT_BUDGET_EXCEEDED = "project_budget_exceeded"
    UNKNOWN_COST_STRICT_BUDGET = "unknown_cost_strict_budget"
    INSUFFICIENT_BUDGET_FOR_BOUND = "insufficient_budget_for_bound"


class CostSource(str, Enum):
    """Where a recorded cost figure came from."""

    PROVIDER_REPORTED = "provider_reported"  # exact: provider reported cost
    AMCA_LEDGER = "amca_ledger"             # exact: amca charged ledger
    CALCULATED_FROM_USAGE = "calculated_from_usage"  # estimate from measured tokens
    UNKNOWN = "unknown"                      # caller could not determine cost


# ---------------------------------------------------------------------------
# Local-model classifier
# ---------------------------------------------------------------------------


_LOCAL_PROVIDER_PREFIXES = (
    "ollama",
    "local-",
    "local/",
    "local:",
    "deterministic",
    "fallback",
    "stub",
)


def is_local_provider(model_id: str | None) -> bool:
    """Return True if a model identifier is local-first (Ollama, stub, fallback).

    The classifier is deliberately conservative: it is invoked by the
    budget enforcer, the cost accumulator, and the read API. If a
    provider/model pair is classified as local, its API cost is treated
    as exactly zero (we never pretend to measure electricity/hardware
    cost).
    """

    if not model_id:
        return False
    text = str(model_id).strip().lower()
    if not text:
        return False
    for prefix in _LOCAL_PROVIDER_PREFIXES:
        if text.startswith(prefix):
            return True
    # Common Ollama convention: "ollama:<model>".
    if ":" in text:
        head = text.split(":", 1)[0]
        if head in {"ollama", "local", "deterministic", "fallback"}:
            return True
    return False


# ---------------------------------------------------------------------------
# Budget policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Bounded budget policy for a single run.

    All fields are optional; the policy is permissive when ``None``.
    Set ``strict_monetary=True`` to make unknown-cost API calls fail
    closed even when the run has plenty of headroom.
    """

    run_budget_usd: float | None = None
    project_budget_usd: float | None = None
    warning_threshold_usd: float | None = None
    strict_monetary: bool = False

    def __post_init__(self) -> None:
        for name in (
            "run_budget_usd",
            "project_budget_usd",
            "warning_threshold_usd",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative number")


# ---------------------------------------------------------------------------
# Run cost accumulator
# ---------------------------------------------------------------------------


def _finite_non_negative(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite, non-negative number")
    return float(value)


@dataclass(slots=True)
class _CallRecord:
    """One settled model call inside a run."""

    call_id: str
    model_id: str
    provider: str
    local: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_source: CostSource
    pricing_known: bool


@dataclass(slots=True)
class RunCostAccumulator:
    """Per-run cost ledger.

    Idempotent semantics: a given ``call_id`` is recorded at most once.
    This is the foundation for "no retry double count".
    """

    run_id: str
    project_id: str | None = None
    calls: dict[str, _CallRecord] = field(default_factory=dict)

    # ---------------- mutation ----------------
    def record_provider_reported(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> _CallRecord:
        """Record an exact cost the provider reported.

        Use this when the adapter/runner returns a concrete cost value
        from the upstream (e.g. an OpenAI usage object with
        ``cost_usd``).
        """

        return self._record(
            call_id=call_id,
            model_id=model_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=_finite_non_negative(float(cost_usd), "cost_usd"),
            cost_source=CostSource.PROVIDER_REPORTED,
            pricing_known=True,
        )

    def record_calculated(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> _CallRecord:
        """Record a cost that was *calculated* from measured token usage
        against a known price table (e.g. AMCA local model_hub pricing).
        """

        return self._record(
            call_id=call_id,
            model_id=model_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=_finite_non_negative(float(cost_usd), "cost_usd"),
            cost_source=CostSource.CALCULATED_FROM_USAGE,
            pricing_known=True,
        )

    def record_unknown(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> _CallRecord:
        """Record a call whose cost is unknown.

        The accumulator never invents a cost for an unknown call:
        the cost figure is ``0.0`` but ``pricing_known=False`` is
        preserved so the read API can surface "known vs unknown".
        """

        return self._record(
            call_id=call_id,
            model_id=model_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            cost_source=CostSource.UNKNOWN,
            pricing_known=False,
        )

    def record_local(
        self,
        *,
        call_id: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> _CallRecord:
        """Record a local-compute call. Local API cost is exactly 0."""

        return self._record(
            call_id=call_id,
            model_id=model_id,
            provider="local",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=0.0,
            cost_source=CostSource.AMCA_LEDGER,
            pricing_known=True,
            local=True,
        )

    def _record(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cost_source: CostSource,
        pricing_known: bool,
        local: bool | None = None,
    ) -> _CallRecord:
        if call_id in self.calls:
            # Idempotent: re-recording the same call_id returns the
            # existing record. This is the foundation for "no retry
            # double count".
            return self.calls[call_id]
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if local is None:
            local = is_local_provider(model_id) or is_local_provider(provider)
        record = _CallRecord(
            call_id=call_id,
            model_id=str(model_id),
            provider=str(provider),
            local=bool(local),
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost_usd=float(cost_usd),
            cost_source=cost_source,
            pricing_known=bool(pricing_known),
        )
        self.calls[call_id] = record
        return record

    # ---------------- aggregation ----------------
    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls.values())

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls.values())

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_known_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls.values() if c.pricing_known)

    @property
    def total_unknown_calls(self) -> int:
        return sum(1 for c in self.calls.values() if not c.pricing_known)

    @property
    def total_local_calls(self) -> int:
        return sum(1 for c in self.calls.values() if c.local)

    @property
    def total_calls(self) -> int:
        return len(self.calls)

    def per_model_cost(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self.calls.values():
            out[c.model_id] = out.get(c.model_id, 0.0) + c.cost_usd
        return out

    def snapshot(self) -> dict:
        """Return a JSON-safe snapshot of the run cost state."""

        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "total_calls": self.total_calls,
            "total_local_calls": self.total_local_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_known_cost_usd": self.total_known_cost_usd,
            "total_unknown_calls": self.total_unknown_calls,
            "per_model_cost_usd": self.per_model_cost(),
            "currency": "USD",
        }


# ---------------------------------------------------------------------------
# Budget enforcer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Result of a single budget evaluation."""

    verdict: BudgetVerdict
    reason: BudgetBlockReason | None
    remaining_run_budget_usd: float | None
    remaining_project_budget_usd: float | None
    estimated_call_cost_usd: float | None
    cost_source: CostSource
    pricing_known: bool
    warning: bool

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason.value if self.reason is not None else None,
            "remaining_run_budget_usd": self.remaining_run_budget_usd,
            "remaining_project_budget_usd": self.remaining_project_budget_usd,
            "estimated_call_cost_usd": self.estimated_call_cost_usd,
            "cost_source": self.cost_source.value,
            "pricing_known": self.pricing_known,
            "warning": self.warning,
        }


class BudgetEnforcer:
    """Evaluate whether the next call is allowed under a budget policy.

    The enforcer is stateless: it consumes an immutable
    :class:`BudgetPolicy`, the current :class:`RunCostAccumulator`,
    and an optional project-cost aggregate. It returns a
    :class:`BudgetDecision`; the caller is responsible for honouring the
    verdict.
    """

    def __init__(self, policy: BudgetPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> BudgetPolicy:
        return self._policy

    def evaluate(
        self,
        *,
        accumulator: RunCostAccumulator,
        estimated_call_cost_usd: float | None,
        pricing_known: bool,
        project_spent_usd: float = 0.0,
        is_local: bool = False,
    ) -> BudgetDecision:
        policy = self._policy
        # Local compute is always free at the API-cost layer.
        if is_local:
            return BudgetDecision(
                verdict=BudgetVerdict.ALLOW,
                reason=None,
                remaining_run_budget_usd=policy.run_budget_usd,
                remaining_project_budget_usd=policy.project_budget_usd,
                estimated_call_cost_usd=0.0,
                cost_source=CostSource.AMCA_LEDGER,
                pricing_known=True,
                warning=False,
            )

        # Unknown cost under a strict monetary budget: fail closed.
        if not pricing_known and policy.strict_monetary:
            return BudgetDecision(
                verdict=BudgetVerdict.BLOCK,
                reason=BudgetBlockReason.UNKNOWN_COST_STRICT_BUDGET,
                remaining_run_budget_usd=policy.run_budget_usd,
                remaining_project_budget_usd=policy.project_budget_usd,
                estimated_call_cost_usd=None,
                cost_source=CostSource.UNKNOWN,
                pricing_known=False,
                warning=False,
            )

        # Without an estimate we cannot reason about budgets. Allow
        # only if no budget is configured and policy is not strict.
        if estimated_call_cost_usd is None:
            if policy.run_budget_usd is None and policy.project_budget_usd is None:
                return BudgetDecision(
                    verdict=BudgetVerdict.ALLOW,
                    reason=None,
                    remaining_run_budget_usd=None,
                    remaining_project_budget_usd=None,
                    estimated_call_cost_usd=None,
                    cost_source=CostSource.UNKNOWN,
                    pricing_known=False,
                    warning=False,
                )
            return BudgetDecision(
                verdict=BudgetVerdict.BLOCK,
                reason=BudgetBlockReason.INSUFFICIENT_BUDGET_FOR_BOUND,
                remaining_run_budget_usd=policy.run_budget_usd,
                remaining_project_budget_usd=policy.project_budget_usd,
                estimated_call_cost_usd=None,
                cost_source=CostSource.UNKNOWN,
                pricing_known=False,
                warning=False,
            )

        cost = _finite_non_negative(float(estimated_call_cost_usd), "estimated_call_cost_usd")
        spent_run = accumulator.total_known_cost_usd
        spent_project = _finite_non_negative(float(project_spent_usd), "project_spent_usd")

        # Run budget
        if policy.run_budget_usd is not None:
            if spent_run + cost > policy.run_budget_usd + 1e-9:
                return BudgetDecision(
                    verdict=BudgetVerdict.BLOCK,
                    reason=BudgetBlockReason.RUN_BUDGET_EXCEEDED,
                    remaining_run_budget_usd=max(0.0, policy.run_budget_usd - spent_run),
                    remaining_project_budget_usd=(
                        None
                        if policy.project_budget_usd is None
                        else max(0.0, policy.project_budget_usd - spent_project)
                    ),
                    estimated_call_cost_usd=cost,
                    cost_source=CostSource.CALCULATED_FROM_USAGE if pricing_known else CostSource.UNKNOWN,
                    pricing_known=pricing_known,
                    warning=False,
                )

        # Project budget
        if policy.project_budget_usd is not None:
            if spent_project + cost > policy.project_budget_usd + 1e-9:
                return BudgetDecision(
                    verdict=BudgetVerdict.BLOCK,
                    reason=BudgetBlockReason.PROJECT_BUDGET_EXCEEDED,
                    remaining_run_budget_usd=(
                        None
                        if policy.run_budget_usd is None
                        else max(0.0, policy.run_budget_usd - spent_run)
                    ),
                    remaining_project_budget_usd=max(0.0, policy.project_budget_usd - spent_project),
                    estimated_call_cost_usd=cost,
                    cost_source=CostSource.CALCULATED_FROM_USAGE if pricing_known else CostSource.UNKNOWN,
                    pricing_known=pricing_known,
                    warning=False,
                )

        # Warning threshold (advisory only)
        warning = False
        if policy.warning_threshold_usd is not None:
            if policy.run_budget_usd is not None:
                if spent_run + cost >= policy.warning_threshold_usd:
                    warning = True
            elif policy.project_budget_usd is not None:
                if spent_project + cost >= policy.warning_threshold_usd:
                    warning = True

        return BudgetDecision(
            verdict=BudgetVerdict.ALLOW,
            reason=None,
            remaining_run_budget_usd=(
                None
                if policy.run_budget_usd is None
                else max(0.0, policy.run_budget_usd - spent_run - cost)
            ),
            remaining_project_budget_usd=(
                None
                if policy.project_budget_usd is None
                else max(0.0, policy.project_budget_usd - spent_project - cost)
            ),
            estimated_call_cost_usd=cost,
            cost_source=CostSource.CALCULATED_FROM_USAGE if pricing_known else CostSource.UNKNOWN,
            pricing_known=pricing_known,
            warning=warning,
        )


# ---------------------------------------------------------------------------
# Pricing helper (token → cost)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Per-million-token price entry for a model (provider-reported)."""

    input_per_million_usd: float
    output_per_million_usd: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.input_per_million_usd, (int, float))
            or not math.isfinite(self.input_per_million_usd)
            or self.input_per_million_usd < 0
        ):
            raise ValueError("input_per_million_usd must be finite and non-negative")
        if (
            not isinstance(self.output_per_million_usd, (int, float))
            or not math.isfinite(self.output_per_million_usd)
            or self.output_per_million_usd < 0
        ):
            raise ValueError("output_per_million_usd must be finite and non-negative")

    def calculate(self, input_tokens: int, output_tokens: int) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        return (
            input_tokens * self.input_per_million_usd / 1_000_000.0
            + output_tokens * self.output_per_million_usd / 1_000_000.0
        )


class PricingRegistry:
    """In-memory price registry (USD per million tokens).

    The registry is intentionally pluggable: the read API can resolve
    pricing from ``amca.model_hub.ModelHubStore`` if it is available,
    or fall back to a static dict for tests. The cost core itself does
    not depend on a specific backing store.

    The M2.1 closeout pins this contract: ``PricingRegistry`` is an
    *evaluation/index* layer over the existing ``model_hub_pricing``
    table. It never carries a hard-coded provider price catalog.
    """

    def __init__(self, prices: Mapping[str, ModelPrice] | None = None) -> None:
        self._prices: dict[str, ModelPrice] = dict(prices or {})

    def set(self, model_id: str, price: ModelPrice) -> None:
        self._prices[str(model_id)] = price

    def get(self, model_id: str) -> ModelPrice | None:
        return self._prices.get(str(model_id))

    def calculate(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> tuple[float, bool]:
        """Return ``(cost_usd, pricing_known)``.

        If the model is unknown to the registry the cost is 0.0 and
        ``pricing_known=False``. The caller must surface that, never
        silently coerce it to a known value.
        """

        price = self.get(model_id)
        if price is None:
            return 0.0, False
        return price.calculate(input_tokens, output_tokens), True

    @classmethod
    def from_model_hub_records(
        cls,
        records: Iterable[Mapping[str, object]],
        *,
        statuses: tuple[str, ...] = ("current", "user_override", "unverified"),
        currency: str = "USD",
    ) -> "PricingRegistry":
        """Build a registry from the existing ``model_hub_pricing`` rows.

        This is the M2.1 authoritative pricing-source path: the caller
        passes whatever ``ModelHubStore.list_pricing()`` returned, and
        the registry is built as a pure *evaluation/index* layer on
        top. The existing ``model_hub_pricing`` table remains the only
        authoritative pricing store; the registry never persists and
        never has its own hard-coded catalog.

        Records are matched by ``"{provider}/{logical_model_id}"`` and
        only rows with status in ``statuses`` and a matching currency
        are admitted. Records with missing or non-finite rates are
        silently skipped (the cost core falls back to "unknown cost"
        semantics for those models).
        """

        prices: dict[str, ModelPrice] = {}
        for record in records:
            if str(record.get("status", "")).lower() not in statuses:
                continue
            if str(record.get("currency", currency)).upper() != currency.upper():
                continue
            provider = record.get("provider")
            model_id = record.get("logical_model_id")
            if not provider or not model_id:
                continue
            input_rate = record.get("input_per_1m")
            output_rate = record.get("output_per_1m")
            if input_rate is None or output_rate is None:
                continue
            try:
                inp = float(input_rate)
                out = float(output_rate)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(inp) or not math.isfinite(out):
                continue
            if inp < 0 or out < 0:
                continue
            key = f"{provider}/{model_id}"
            prices[key] = ModelPrice(inp, out)
        return cls(prices)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostReadState:
    """Bounded read-only cost state for the existing /api surface."""

    run_id: str | None
    project_id: str | None
    currency: str
    total_known_cost_usd: float
    total_unknown_calls: int
    total_calls: int
    total_local_calls: int
    total_input_tokens: int
    total_output_tokens: int
    per_model_cost_usd: dict[str, float]
    project_aggregate_known_cost_usd: float | None
    project_aggregate_unknown_calls: int | None
    run_budget_usd: float | None
    project_budget_usd: float | None
    remaining_run_budget_usd: float | None
    remaining_project_budget_usd: float | None
    warning_threshold_usd: float | None
    strict_monetary: bool
    known: bool

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "currency": self.currency,
            "total_known_cost_usd": self.total_known_cost_usd,
            "total_unknown_calls": self.total_unknown_calls,
            "total_calls": self.total_calls,
            "total_local_calls": self.total_local_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "per_model_cost_usd": dict(self.per_model_cost_usd),
            "project_aggregate_known_cost_usd": self.project_aggregate_known_cost_usd,
            "project_aggregate_unknown_calls": self.project_aggregate_unknown_calls,
            "run_budget_usd": self.run_budget_usd,
            "project_budget_usd": self.project_budget_usd,
            "remaining_run_budget_usd": self.remaining_run_budget_usd,
            "remaining_project_budget_usd": self.remaining_project_budget_usd,
            "warning_threshold_usd": self.warning_threshold_usd,
            "strict_monetary": self.strict_monetary,
            "known": self.known,
        }


def build_read_state(
    *,
    accumulator: RunCostAccumulator | None,
    policy: BudgetPolicy | None,
    project_aggregate_known_cost_usd: float | None = None,
    project_aggregate_unknown_calls: int | None = None,
    project_spent_usd: float | None = None,
) -> CostReadState:
    """Build a :class:`CostReadState` snapshot for the read API.

    ``accumulator`` may be ``None`` if the caller has no in-flight run
    yet; the read API still returns a valid empty state. ``policy`` may
    be ``None`` if no budget policy is in effect.
    """

    if accumulator is None:
        return CostReadState(
            run_id=None,
            project_id=None,
            currency="USD",
            total_known_cost_usd=0.0,
            total_unknown_calls=0,
            total_calls=0,
            total_local_calls=0,
            total_input_tokens=0,
            total_output_tokens=0,
            per_model_cost_usd={},
            project_aggregate_known_cost_usd=project_aggregate_known_cost_usd,
            project_aggregate_unknown_calls=project_aggregate_unknown_calls,
            run_budget_usd=None,
            project_budget_usd=None,
            remaining_run_budget_usd=None,
            remaining_project_budget_usd=None,
            warning_threshold_usd=None,
            strict_monetary=False,
            known=(
                project_aggregate_known_cost_usd is not None
                and project_aggregate_unknown_calls is not None
                and project_aggregate_unknown_calls == 0
            ),
        )

    spent_run = accumulator.total_known_cost_usd
    if policy is not None:
        remaining_run = (
            None
            if policy.run_budget_usd is None
            else max(0.0, policy.run_budget_usd - spent_run)
        )
        remaining_project = (
            None
            if policy.project_budget_usd is None
            else max(
                0.0,
                policy.project_budget_usd
                - (
                    project_spent_usd
                    if project_spent_usd is not None
                    else (project_aggregate_known_cost_usd or 0.0)
                ),
            )
        )
    else:
        remaining_run = None
        remaining_project = None

    known = (
        accumulator.total_unknown_calls == 0
        and (
            project_aggregate_unknown_calls is None
            or project_aggregate_unknown_calls == 0
        )
    )

    return CostReadState(
        run_id=accumulator.run_id,
        project_id=accumulator.project_id,
        currency="USD",
        total_known_cost_usd=spent_run,
        total_unknown_calls=accumulator.total_unknown_calls,
        total_calls=accumulator.total_calls,
        total_local_calls=accumulator.total_local_calls,
        total_input_tokens=accumulator.total_input_tokens,
        total_output_tokens=accumulator.total_output_tokens,
        per_model_cost_usd=accumulator.per_model_cost(),
        project_aggregate_known_cost_usd=project_aggregate_known_cost_usd,
        project_aggregate_unknown_calls=project_aggregate_unknown_calls,
        run_budget_usd=None if policy is None else policy.run_budget_usd,
        project_budget_usd=None if policy is None else policy.project_budget_usd,
        remaining_run_budget_usd=remaining_run,
        remaining_project_budget_usd=remaining_project,
        warning_threshold_usd=None if policy is None else policy.warning_threshold_usd,
        strict_monetary=False if policy is None else policy.strict_monetary,
        known=bool(known),
    )


# ---------------------------------------------------------------------------
# Evidence sanitiser
# ---------------------------------------------------------------------------

# Field names that must never appear in a cost evidence record, even
# when serialised for the read API or stored to a log.
_FORBIDDEN_EVIDENCE_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "x-amca-session",
    "session_token",
    "amca_session_token",
    "password",
    "secret",
    "private_key",
    "aws_secret_access_key",
    "prompt",
    "raw_prompt",
    "raw_response",
    "response_text",
    "raw_model_output",
}

# Pattern for direct "key=value" / "key: value" forms that would
# accidentally leak a credential.
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{20,}"),
)


def sanitize_evidence(record: Mapping[str, object]) -> dict:
    """Return a JSON-safe evidence record with credentials stripped.

    Forbidden field names (case-insensitive) are removed. If a remaining
    string value matches a known high-entropy credential pattern, the
    value is replaced with a sentinel ``"[REDACTED]"``. Numbers and
    booleans are preserved unchanged.
    """

    cleaned: dict = {}
    for key, value in record.items():
        if str(key).strip().lower() in _FORBIDDEN_EVIDENCE_FIELDS:
            continue
        if isinstance(value, str):
            if any(p.search(value) for p in _FORBIDDEN_VALUE_PATTERNS):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = value
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[key] = value
        elif isinstance(value, Mapping):
            cleaned[key] = sanitize_evidence(value)
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [
                sanitize_evidence(v) if isinstance(v, Mapping)
                else ("[REDACTED]" if isinstance(v, str) and any(p.search(v) for p in _FORBIDDEN_VALUE_PATTERNS) else v)
                for v in value
            ]
        else:
            # Fall back to repr to guarantee no accidental credential
            # passes through, at the cost of fidelity.
            cleaned[key] = repr(value)
    return cleaned


# ---------------------------------------------------------------------------
# Authoritative-ledger reconciliation (M2.1 closeout)
# ---------------------------------------------------------------------------


class LedgerConflictError(RuntimeError):
    """Raised when a CostCore record disagrees with the authoritative ledger.

    The cost/budget backend core does NOT persist a parallel ledger. When
    an authoritative source is supplied (typically ``amca.model_hub.ModelHubStore``
    or a function that returns an :class:`AuthoritativeCost` for a given
    call_id), the core consults it before recording and refuses to silently
    disagree. This is the M2.1 architectural safety check.
    """


@dataclass(frozen=True, slots=True)
class AuthoritativeCost:
    """Canonical per-call cost record sourced from the existing ledger.

    Mirrors the shape already exposed by
    ``amca.model_hub.ModelHubStore.record_task_cost`` /
    ``amca.model_hub.ModelHubStore.task_cost`` for the per-call row in
    ``model_hub_task_costs``. The fields here are the *minimum* needed by
    the cost core to verify a candidate record; other ledger fields
    (breakdown, pricing_record_refs, ...) are intentionally not mirrored.
    """

    call_id: str
    model_id: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    pricing_known: bool


# Type alias: a callable that returns the authoritative record for a
# call_id, or None if no authoritative record exists yet. This is the
# smallest possible integration surface — the existing model_hub
# adapter supplies a thin wrapper, the core never opens a second
# connection.
AuthoritativeLedger = "Callable[[str], AuthoritativeCost | None]"


# ---------------------------------------------------------------------------
# Convenience façade
# ---------------------------------------------------------------------------


class CostCore:
    """Convenience façade bundling accumulator + enforcer + registry.

    Most callers will instantiate one ``CostCore`` per request/run and
    use it as their single entry point.

    When ``authoritative_ledger`` is supplied, every
    :meth:`record_from_response` call consults the existing canonical
    record for the same ``call_id`` and refuses to silently disagree
    (raising :class:`LedgerConflictError`). The core never persists a
    parallel ledger; the authoritative source remains the existing
    ``model_hub_task_costs`` row that the caller already owns.
    """

    def __init__(
        self,
        *,
        run_id: str,
        project_id: str | None = None,
        policy: BudgetPolicy | None = None,
        pricing: PricingRegistry | None = None,
        authoritative_ledger: "AuthoritativeLedger | None" = None,
    ) -> None:
        self.accumulator = RunCostAccumulator(run_id=run_id, project_id=project_id)
        self.policy = policy
        self.pricing = pricing or PricingRegistry()
        self._authoritative_ledger = authoritative_ledger

    def evaluate(
        self,
        *,
        model_id: str,
        estimated_call_cost_usd: float | None = None,
        pricing_known: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        project_spent_usd: float = 0.0,
    ) -> BudgetDecision:
        if self.policy is None:
            # Without a policy we always allow.
            return BudgetDecision(
                verdict=BudgetVerdict.ALLOW,
                reason=None,
                remaining_run_budget_usd=None,
                remaining_project_budget_usd=None,
                estimated_call_cost_usd=estimated_call_cost_usd,
                cost_source=CostSource.UNKNOWN if pricing_known is False else CostSource.AMCA_LEDGER,
                pricing_known=bool(pricing_known) if pricing_known is not None else True,
                warning=False,
            )
        local = is_local_provider(model_id)
        if estimated_call_cost_usd is None and not local and pricing_known is not False:
            # Try the pricing registry.
            if input_tokens is not None and output_tokens is not None:
                cost, known = self.pricing.calculate(model_id, input_tokens, output_tokens)
                if known:
                    estimated_call_cost_usd = cost
                    pricing_known = True
                else:
                    pricing_known = False
            else:
                price = self.pricing.get(model_id)
                if price is not None:
                    pricing_known = True
                else:
                    pricing_known = False
        return BudgetEnforcer(self.policy).evaluate(
            accumulator=self.accumulator,
            estimated_call_cost_usd=estimated_call_cost_usd,
            pricing_known=bool(pricing_known) if pricing_known is not None else False,
            project_spent_usd=project_spent_usd,
            is_local=local,
        )

    def record_from_response(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        pricing_known: bool,
    ) -> _CallRecord:
        # M2.1: if an authoritative ledger is wired, consult it for the
        # same call_id and refuse to silently disagree.
        if self._authoritative_ledger is not None:
            existing = self._authoritative_ledger(call_id)
            if existing is not None:
                self._verify_authoritative(
                    existing=existing,
                    call_id=call_id,
                    model_id=model_id,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    pricing_known=pricing_known,
                )
        if is_local_provider(model_id) or is_local_provider(provider):
            return self.accumulator.record_local(
                call_id=call_id,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if cost_usd is not None and pricing_known:
            return self.accumulator.record_provider_reported(
                call_id=call_id,
                model_id=model_id,
                provider=provider,
                cost_usd=float(cost_usd),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        if cost_usd is None and pricing_known:
            # Calculate from local pricing registry.
            cost, known = self.pricing.calculate(model_id, input_tokens, output_tokens)
            if known:
                return self.accumulator.record_calculated(
                    call_id=call_id,
                    model_id=model_id,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
        return self.accumulator.record_unknown(
            call_id=call_id,
            model_id=model_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _verify_authoritative(
        self,
        *,
        existing: "AuthoritativeCost",
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        pricing_known: bool,
    ) -> None:
        """Refuse to record a call that disagrees with the existing ledger.

        The core is not allowed to silently disagree. If the existing
        record disagrees on tokens, model, or provider, we raise
        :class:`LedgerConflictError`; on cost_usd we only fail when
        *both* sides are known (an "unknown" record on either side
        is allowed to upgrade to a known value but not the reverse).
        """

        if existing.input_tokens != input_tokens:
            raise LedgerConflictError(
                f"input_tokens conflict for {call_id=}: "
                f"existing={existing.input_tokens} candidate={input_tokens}"
            )
        if existing.output_tokens != output_tokens:
            raise LedgerConflictError(
                f"output_tokens conflict for {call_id=}: "
                f"existing={existing.output_tokens} candidate={output_tokens}"
            )
        if existing.model_id != model_id:
            raise LedgerConflictError(
                f"model_id conflict for {call_id=}: "
                f"existing={existing.model_id} candidate={model_id}"
            )
        if existing.provider != provider:
            raise LedgerConflictError(
                f"provider conflict for {call_id=}: "
                f"existing={existing.provider} candidate={provider}"
            )
        if existing.cost_usd is not None and cost_usd is not None:
            if existing.pricing_known and pricing_known:
                if abs(existing.cost_usd - cost_usd) > 1e-9:
                    raise LedgerConflictError(
                        f"cost_usd conflict for {call_id=}: "
                        f"existing={existing.cost_usd} candidate={cost_usd}"
                    )
        # If the existing record marked pricing unknown but the
        # candidate has a known value, the candidate is a strict
        # upgrade. Accept it (the ledger row will be overwritten by
        # the caller; the core just refuses to disagree).

    def read_state(
        self,
        *,
        project_aggregate_known_cost_usd: float | None = None,
        project_aggregate_unknown_calls: int | None = None,
        project_spent_usd: float | None = None,
    ) -> CostReadState:
        return build_read_state(
            accumulator=self.accumulator,
            policy=self.policy,
            project_aggregate_known_cost_usd=project_aggregate_known_cost_usd,
            project_aggregate_unknown_calls=project_aggregate_unknown_calls,
            project_spent_usd=project_spent_usd,
        )


__all__ = [
    "AuthoritativeCost",
    "AuthoritativeLedger",
    "BudgetBlockReason",
    "BudgetDecision",
    "BudgetEnforcer",
    "BudgetPolicy",
    "BudgetVerdict",
    "CostCore",
    "CostReadState",
    "CostSource",
    "LedgerConflictError",
    "ModelPrice",
    "PricingRegistry",
    "RunCostAccumulator",
    "build_read_state",
    "is_local_provider",
    "sanitize_evidence",
]
