"""Runtime integration of the v0.6.0 M2 cost/budget core (AUTOCLAW-AMCA-V0.6.0-M2.3-RUNTIME-BUDGET-ENFORCEMENT-INTEGRATION-01).

This module is the runtime bridge between the accepted M2 cost
backend and the actual model-call execution path. It wraps
``amca.cost_budget.CostCore`` and exposes the small surface that
``CollaborationKernel`` (and any other call site) needs to:

  * **Pre-dispatch enforcement** — ask ``enforce_pre_dispatch`` whether a
    prospective billable call is allowed under the active
    session/run budget. A hard-budget BLOCK causes the caller to
    *not* dispatch the adapter at all.
  * **Post-dispatch recording** — call ``record_post_dispatch`` with
    the settled ``ModelCallResponse`` to record the cost exactly
    once. Idempotent on ``call_id``.

Critical guarantees:

  * When no budget policy is configured, the enforcer returns
    ALLOW with no side effects — model execution behavior is
    identical to the pre-integration baseline.
  * Local compute (Ollama and similar) is always allowed and
    recorded at zero API cost. AMCA does not measure
    electricity/hardware cost.
  * Unknown-cost API calls under a strict monetary policy fail
    closed with ``BudgetBlockReason.UNKNOWN_COST_STRICT_BUDGET`` —
    the adapter is **never** called in that case.
  * Cancellation, provider-fail-before-usage, and retries with
    the same ``call_id`` are all recorded truthfully and only once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Mapping

from .cost_budget import (
    AuthoritativeCost,
    BudgetBlockReason,
    BudgetEnforcer,
    BudgetPolicy,
    BudgetVerdict,
    CostCore,
    LedgerConflictError,
    PricingRegistry,
    RunCostAccumulator,
    is_local_provider,
)


# Re-export the enums we need downstream so the integration site
# only has to import one module.
__all__ = [
    "BudgetBlockReason",
    "BudgetVerdict",
    "RuntimeBudgetEnforcer",
    "PreDispatchDecision",
]


class PreDispatchDecision:
    """Outcome of a pre-dispatch budget check.

    A BLOCK decision carries a ``reason`` that the runtime can
    surface to the user without leaking credential material or
    raw prompts/responses.
    """

    __slots__ = ("verdict", "reason", "estimated_call_cost_usd", "pricing_known", "detail")

    def __init__(
        self,
        verdict: BudgetVerdict,
        reason: BudgetBlockReason | None = None,
        *,
        estimated_call_cost_usd: float | None = None,
        pricing_known: bool = False,
        detail: str = "",
    ) -> None:
        self.verdict = verdict
        self.reason = reason
        self.estimated_call_cost_usd = estimated_call_cost_usd
        self.pricing_known = pricing_known
        self.detail = detail

    @property
    def allowed(self) -> bool:
        return self.verdict == BudgetVerdict.ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict == BudgetVerdict.BLOCK

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason.value if self.reason is not None else None,
            "estimated_call_cost_usd": self.estimated_call_cost_usd,
            "pricing_known": self.pricing_known,
            "detail": self.detail,
        }


class RuntimeBudgetEnforcer:
    """Wraps :class:`CostCore` and exposes the runtime contract.

    Single instance per ``ServiceContext`` (or per collaboration run)
    is sufficient: it carries the per-run accumulator and the
    session policy, and is safe to share across call sites within
    the same process.
    """

    def __init__(
        self,
        *,
        run_id: str,
        project_id: str | None = None,
        policy: BudgetPolicy | None = None,
        pricing: PricingRegistry | None = None,
        authoritative_ledger: "Callable[[str], AuthoritativeCost | None] | None" = None,
    ) -> None:
        self._core = CostCore(
            run_id=run_id,
            project_id=project_id,
            policy=policy,
            pricing=pricing,
            authoritative_ledger=authoritative_ledger,
        )

    @property
    def core(self) -> CostCore:
        return self._core

    @property
    def accumulator(self) -> RunCostAccumulator:
        return self._core.accumulator

    @property
    def policy(self) -> BudgetPolicy | None:
        return self._core.policy

    def has_active_policy(self) -> bool:
        """True iff a budget is configured (i.e. enforce_pre_dispatch
        might ever BLOCK). When False, model execution must remain
        unchanged per the M2.3 work order.
        """

        return self._core.policy is not None

    def enforce_pre_dispatch(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        estimated_call_cost_usd: float | None = None,
        pricing_known: bool | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> PreDispatchDecision:
        """Ask whether the next dispatch is allowed.

        The contract is:

        * No policy set → always ALLOW, never inspect pricing.
        * Local provider (Ollama, local-*, etc.) → always ALLOW,
          regardless of policy.
        * Unknown-cost API under strict_monetary → BLOCK with
          ``UNKNOWN_COST_STRICT_BUDGET``. The adapter is never
          called.
        * Cost would exceed run_budget_usd / project_budget_usd
          → BLOCK with the appropriate reason.
        * Otherwise ALLOW.
        """

        # No policy = unchanged behavior. The pre-integration
        # baseline never blocked anything.
        if self._core.policy is None:
            return PreDispatchDecision(
                verdict=BudgetVerdict.ALLOW,
                detail="No budget policy; pre-dispatch check is a no-op.",
            )

        # Local = always allowed, no API cost.
        if is_local_provider(model_id) or is_local_provider(provider):
            return PreDispatchDecision(
                verdict=BudgetVerdict.ALLOW,
                detail="Local provider; pre-dispatch check is a no-op.",
            )

        # Resolve pricing: caller may pass an estimate; otherwise
        # consult the pricing registry for a quick back-of-the-
        # envelope estimate.
        if (
            estimated_call_cost_usd is None
            and pricing_known is not False
            and input_tokens is not None
            and output_tokens is not None
        ):
            cost, known = self._core.pricing.calculate(
                model_id, input_tokens, output_tokens
            )
            if known:
                estimated_call_cost_usd = cost
                pricing_known = True
            else:
                pricing_known = False

        # Soft-mode optimism: when the cost is unknown and the policy
        # is not strict, treat the call as zero-cost for the purpose
        # of the budget check. The post-dispatch record will still
        # mark the call pricing_known=False so the read API can
        # surface it. This is the M2.3 layer's interpretation of
        # "warning mode permits execution but records the warning".
        if (
            estimated_call_cost_usd is None
            and pricing_known is False
            and not self._core.policy.strict_monetary
        ):
            estimated_call_cost_usd = 0.0

        decision = BudgetEnforcer(self._core.policy).evaluate(
            accumulator=self._core.accumulator,
            estimated_call_cost_usd=estimated_call_cost_usd,
            pricing_known=bool(pricing_known) if pricing_known is not None else False,
            project_spent_usd=0.0,
            is_local=False,
        )
        return PreDispatchDecision(
            verdict=decision.verdict,
            reason=decision.reason,
            estimated_call_cost_usd=decision.estimated_call_cost_usd,
            pricing_known=decision.pricing_known,
            detail=(
                f"run_remaining={decision.remaining_run_budget_usd} "
                f"project_remaining={decision.remaining_project_budget_usd} "
                f"warning={decision.warning}"
            ),
        )

    def record_post_dispatch(
        self,
        *,
        call_id: str,
        model_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        pricing_known: bool,
    ) -> None:
        """Record a settled call exactly once.

        Idempotent on ``call_id``: a duplicate call is silently
        ignored (the accumulator returns the existing record). The
        authoritative-ledger integration (M2.1) is honoured when
        configured.
        """

        try:
            self._core.record_from_response(
                call_id=call_id,
                model_id=model_id,
                provider=provider,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                pricing_known=pricing_known,
            )
        except LedgerConflictError:
            # The M2.1 contract is fail-closed on a disagreement
            # with the authoritative ledger. We surface this as
            # a logged condition; the runtime never silently
            # overwrites an authoritative row. Callers may consult
            # the accumulator afterwards.
            raise
