"""HTTP route handlers for the v0.6.0 M2 cost/budget backend core.

Exposes three read/write endpoints behind the existing AMCA session and
host guards:

  GET  /api/cost/state       - read-only cost snapshot (current run,
                              project aggregate, budget status, known vs
                              unknown). No auth beyond the standard
                              loopback + host + CORS guard because the
                              response contains no credentials, raw
                              prompts, raw responses, or session tokens.
  GET  /api/cost/budget      - return the active BudgetPolicy (or
                              ``{ "policy": null }`` if none is set).
                              Session-auth required.
  POST /api/cost/budget      - set/replace the active BudgetPolicy.
                              Session-auth required. Hardened body
                              validation. No unauthenticated mutation
                              path.

The :func:`install_cost_routes` function is invoked from
``service.ServiceContext``'s HTTP handler factory (a single line change)
to wire the routes into the existing dispatcher. This keeps the change
additive: ``service.py`` is not rearchitected.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping

from .cost_budget import (
    BudgetPolicy,
    CostCore,
    PricingRegistry,
    RunCostAccumulator,
    build_read_state,
    sanitize_evidence,
)


# ---------------------------------------------------------------------------
# Per-context state holders
# ---------------------------------------------------------------------------


class _CostState:
    """Per-ServiceContext cost state. Owned by the ServiceContext."""

    def __init__(self) -> None:
        # The active run accumulator. None means "no run in progress yet".
        self.run_accumulator: RunCostAccumulator | None = None
        # The active budget policy for the running session.
        self.policy: BudgetPolicy | None = None
        # Pricing registry used by the enforcer's pricing fallback.
        self.pricing: PricingRegistry = PricingRegistry()
        # Project aggregate (USD + unknown count) from the existing
        # project ledger. None if the caller has not provided one.
        self.project_aggregate_known_cost_usd: float | None = None
        self.project_aggregate_unknown_calls: int | None = None

    def reset_run(self, run_id: str, project_id: str | None = None) -> None:
        self.run_accumulator = RunCostAccumulator(run_id=run_id, project_id=project_id)

    def core(self, run_id: str | None = None, project_id: str | None = None) -> CostCore:
        """Return a :class:`CostCore` bound to the current state.

        A new run is started only if ``run_id`` is provided and no
        accumulator is currently active. The same accumulator instance
        is reused across the lifetime of the ServiceContext (per the
        work order: avoid double counting).
        """

        if self.run_accumulator is None and run_id is not None:
            self.reset_run(run_id=run_id, project_id=project_id)
        if self.run_accumulator is None:
            # Caller did not specify a run yet; create a transient core
            # bound to no accumulator so evaluate() can still answer
            # (always ALLOW when policy is None, or BLOCK on strict).
            return CostCore(run_id="__unbound__", policy=self.policy, pricing=self.pricing)
        return CostCore(
            run_id=self.run_accumulator.run_id,
            project_id=self.run_accumulator.project_id,
            policy=self.policy,
            pricing=self.pricing,
        )


def get_cost_state(context: Any) -> _CostState:
    """Return the per-context :class:`_CostState`, creating it lazily.

    Falls back to a private ``__dict__`` lookup so that the function
    is safe against mock-like objects whose ``__getattr__`` returns a
    truthy MagicMock for any attribute name.
    """

    store = getattr(context, "__dict__", None)
    if store is not None and "_cost_state" in store:
        return store["_cost_state"]
    state = _CostState()
    if store is not None:
        store["_cost_state"] = state
    return state


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _check_session(handler: Any) -> bool:
    """Return True if the request is authenticated; otherwise respond 401."""

    if handler._authorized():
        return True
    handler._error(HTTPStatus.UNAUTHORIZED, "a valid desktop session is required")
    return False


def _parse_budget_payload(payload: Mapping[str, Any]) -> BudgetPolicy:
    """Validate and build a :class:`BudgetPolicy` from a request payload.

    Rejects unknown fields, negative numbers, and non-finite values.
    The whole payload must be a JSON object.
    """

    allowed = {
        "run_budget_usd",
        "project_budget_usd",
        "warning_threshold_usd",
        "strict_monetary",
    }
    unknown = set(payload.keys()) - allowed
    if unknown:
        raise ValueError(f"unknown budget fields: {sorted(unknown)}")

    def _opt_float(name: str) -> float | None:
        value = payload.get(name, None)
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a number, not a boolean")
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        f = float(value)
        import math

        if not math.isfinite(f) or f < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        return f

    strict = payload.get("strict_monetary", False)
    if not isinstance(strict, bool):
        raise ValueError("strict_monetary must be a boolean")

    return BudgetPolicy(
        run_budget_usd=_opt_float("run_budget_usd"),
        project_budget_usd=_opt_float("project_budget_usd"),
        warning_threshold_usd=_opt_float("warning_threshold_usd"),
        strict_monetary=bool(strict),
    )


def handle_cost_state(handler: Any) -> None:
    """GET /api/cost/state — read-only cost snapshot."""

    context = handler.context
    state = get_cost_state(context)
    snapshot = build_read_state(
        accumulator=state.run_accumulator,
        policy=state.policy,
        project_aggregate_known_cost_usd=state.project_aggregate_known_cost_usd,
        project_aggregate_unknown_calls=state.project_aggregate_unknown_calls,
    )
    payload = sanitize_evidence(snapshot.to_dict())
    handler._send_json(HTTPStatus.OK, payload)


def handle_cost_get_budget(handler: Any) -> None:
    """GET /api/cost/budget — return the active policy."""

    if not _check_session(handler):
        return
    state = get_cost_state(handler.context)
    if state.policy is None:
        handler._send_json(HTTPStatus.OK, {"policy": None})
        return
    handler._send_json(
        HTTPStatus.OK,
        {
            "policy": {
                "run_budget_usd": state.policy.run_budget_usd,
                "project_budget_usd": state.policy.project_budget_usd,
                "warning_threshold_usd": state.policy.warning_threshold_usd,
                "strict_monetary": state.policy.strict_monetary,
            }
        },
    )


def handle_cost_set_budget(handler: Any) -> None:
    """POST /api/cost/budget — set the active policy.

    Body schema (JSON object):
      {
        "run_budget_usd": <non-negative number | null>,
        "project_budget_usd": <non-negative number | null>,
        "warning_threshold_usd": <non-negative number | null>,
        "strict_monetary": <boolean>
      }

    Validation rejects unknown fields, negative values, NaN/Inf, and
    wrong types. On success the active policy is replaced.
    """

    if not _check_session(handler):
        return
    try:
        payload = handler._read_object()
        policy = _parse_budget_payload(payload)
    except (ValueError, TypeError) as error:
        handler._error(HTTPStatus.BAD_REQUEST, str(error))
        return

    state = get_cost_state(handler.context)
    state.policy = policy
    handler._send_json(
        HTTPStatus.OK,
        {
            "policy": {
                "run_budget_usd": policy.run_budget_usd,
                "project_budget_usd": policy.project_budget_usd,
                "warning_threshold_usd": policy.warning_threshold_usd,
                "strict_monetary": policy.strict_monetary,
            }
        },
    )


# ---------------------------------------------------------------------------
# Installation entry point
# ---------------------------------------------------------------------------


def install_cost_routes(handler: Any, path: str) -> bool:
    """Try to dispatch a request to a cost route; return True on match."""

    if path == "/api/cost/state":
        handle_cost_state(handler)
        return True
    if path == "/api/cost/budget":
        method = getattr(handler, "command", None)
        if method == "POST":
            handle_cost_set_budget(handler)
        else:
            handle_cost_get_budget(handler)
        return True
    return False
