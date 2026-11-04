"""Run-scoped runtime health.

Work Order 09, sections 1.8 and 6.4.  Two facts must stay separate:

* **Capability** — what a model can do.  Slow, provider-specific, recorded in
  the Model Registry with provenance.
* **Runtime health** — what is happening to it *right now*.

A timeout is evidence about health.  It must never turn ``supports_tools`` from
true to false.  This module is the run-scoped half: it accumulates what actually
happened during ONE run, and disappears with that run.

It deliberately does NOT persist.  ``model_health.py`` already owns
configuration-level health — whether a configured entry is usable at all — and
that is a different question with a different lifetime.  Reusing it here would
conflate "this model is misconfigured" with "this model was slow five minutes
ago", and would let a transient fault survive the run that observed it.

Nothing in this module can write to the Model Registry.  Health can rank a
candidate or exclude it from *this* run; it can never change what the registry
says the model is.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: How many recent outcomes per model are considered.  Bounded so a long run
#: cannot grow the tracker without limit, and recent-weighted so a model that
#: recovered stops being penalised for an early fault.
MAX_OUTCOMES_PER_MODEL = 20


class RuntimeHealth(str, enum.Enum):
    """What recent execution evidence says about a model, right now."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    TIMEOUT_PRONE = "timeout_prone"
    UNKNOWN = "unknown"


#: Lower ranks better.  Used to order candidates, never to exclude capability.
_RANK = {
    RuntimeHealth.HEALTHY: 0,
    RuntimeHealth.UNKNOWN: 1,
    RuntimeHealth.DEGRADED: 2,
    RuntimeHealth.TIMEOUT_PRONE: 3,
    RuntimeHealth.RATE_LIMITED: 4,
    RuntimeHealth.TEMPORARILY_UNAVAILABLE: 5,
}

#: Health states that make a model a poor choice for THIS run.  They do not
#: change the model's capabilities and they do not outlive the run.
_EXCLUDING = frozenset({
    RuntimeHealth.RATE_LIMITED,
    RuntimeHealth.TEMPORARILY_UNAVAILABLE,
})


def classify_outcome(failure: Any) -> RuntimeHealth:
    """Map one observed failure to a health signal.

    Uses the same failure classification as recovery, so a denial is not
    misread as the provider being unhealthy: a permission refusal says nothing
    about whether the model is working.
    """

    from .governed_recovery import RecoveryClass, classify_failure

    if classify_failure(failure) is not RecoveryClass.RETRIABLE_ON_ANOTHER_MODEL:
        # Not a health signal at all.  A denial, a cancellation or an invalid
        # request is not evidence the model is unwell.
        return RuntimeHealth.UNKNOWN

    reason = str(failure if not isinstance(failure, BaseException) else failure).casefold()
    name = type(failure).__name__ if isinstance(failure, BaseException) else ""
    if "RateLimit" in name or "rate limit" in reason:
        return RuntimeHealth.RATE_LIMITED
    if "Timeout" in name or "timed out" in reason or "timeout" in reason:
        return RuntimeHealth.TIMEOUT_PRONE
    if "Unavailable" in name or "unavailable" in reason or "could not reach" in reason:
        return RuntimeHealth.TEMPORARILY_UNAVAILABLE
    return RuntimeHealth.DEGRADED


@dataclass
class RunScopedHealth:
    """Runtime health observed during ONE run.  Never persisted.

    Deliberately has no reference to the Model Registry: there is no code path
    from here that could mutate a capability fact even by mistake.
    """

    _outcomes: dict[str, list[RuntimeHealth]] = field(default_factory=dict)

    def record_success(self, model_ref: str) -> None:
        self._append(model_ref, RuntimeHealth.HEALTHY)

    def record_failure(self, model_ref: str, failure: Any) -> RuntimeHealth:
        signal = classify_outcome(failure)
        self._append(model_ref, signal)
        return signal

    def _append(self, model_ref: str, signal: RuntimeHealth) -> None:
        key = str(model_ref or "")
        if not key:
            return
        history = self._outcomes.setdefault(key, [])
        history.append(signal)
        if len(history) > MAX_OUTCOMES_PER_MODEL:
            del history[: len(history) - MAX_OUTCOMES_PER_MODEL]

    def health_of(self, model_ref: str) -> RuntimeHealth:
        """The current signal for a model.

        A model with no observations is UNKNOWN, not HEALTHY: absence of
        evidence is not evidence of health, and claiming otherwise would let an
        untested model outrank a proven one.
        """

        history = self._outcomes.get(str(model_ref or ""))
        if not history:
            return RuntimeHealth.UNKNOWN
        # The most recent observation decides, with one exception: a model that
        # has succeeded since its last failure is HEALTHY again, so a transient
        # fault does not penalise it for the rest of the run.
        return history[-1]

    def ranking_key(self, model_ref: str) -> int:
        """Sort key for candidate ordering.  Lower is better."""

        return _RANK.get(self.health_of(model_ref), _RANK[RuntimeHealth.UNKNOWN])

    def temporarily_excluded(self, model_ref: str) -> tuple[bool, str]:
        """Whether this run should avoid the model, and why.

        Excludes only for the run, and only for a reason that is about the
        moment — a rate limit or an outage.  A timeout-prone model is ranked
        lower, not excluded: it may still be the only capable candidate.
        """

        health = self.health_of(model_ref)
        if health in _EXCLUDING:
            return True, f"runtime health is {health.value} for this run"
        return False, ""

    def order_candidates(
        self, candidates: Iterable[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        """Rank candidates by health, best first, keeping the input order as a tiebreak.

        Ranking only.  Capability eligibility is decided elsewhere and is not
        affected by anything recorded here.
        """

        items = list(candidates)
        return sorted(
            items,
            key=lambda item: self.ranking_key(
                str(item.get("model_ref") or item.get("name") or "")
            ),
        )

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            model_ref: {
                "health": self.health_of(model_ref).value,
                "observations": len(history),
            }
            for model_ref, history in sorted(self._outcomes.items())
        }


__all__ = [
    "MAX_OUTCOMES_PER_MODEL",
    "RunScopedHealth",
    "RuntimeHealth",
    "classify_outcome",
]
