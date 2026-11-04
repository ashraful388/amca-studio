"""Governed failure recovery policy for agent tasks.

Manager architecture decision, 2026-09-19.  The directive is explicit that
recovery must NOT be "task failed, rerun it on another model":

    Recovery must be governed by failure type, permissions, tool side effects
    and the Goal Contract.

This module is the POLICY layer.  ``agent_recovery.py`` is the durable-state
layer that journals attempts and reservations for crash resumption; this module
decides whether a failure may be recovered at all, on what, and from where.

Four rules enforced here:

1. **Classify before recovering.**  A timeout, a rate limit or a provider
   outage may be retried elsewhere.  A permission denial, a tool denial, an
   approval denial, a cancelled run, an invalid request or a policy violation
   must NOT be — retrying those elsewhere would either bypass a control or
   spend a call on something no model can fix.
2. **Bound the recovery.**  Per-model retries happen first; after those are
   exhausted exactly one alternate-model step is permitted by default, so a
   provider outage cannot become a retry storm.
3. **Respect side effects.**  If a task already performed a side-effecting tool
   action, rerunning it from the start could repeat that effect.  Recovery
   reads the checkpoint and either resumes from a safe point or fails
   truthfully without replaying.
4. **Never mutate capability because of a runtime fault.**  A timeout is
   evidence about health, not about what a model can do.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


class RecoveryClass(str, enum.Enum):
    """Whether a failure may be recovered on a different model."""

    RETRIABLE_ON_ANOTHER_MODEL = "retriable_on_another_model"
    NOT_FALLBACK_ELIGIBLE = "not_fallback_eligible"


class RunOutcome(str, enum.Enum):
    """The three distinct terminal states the directive requires.

    It is not acceptable to always return a "partial result": a run in which
    nothing substantive succeeded must say so rather than presenting an empty
    answer as a degraded one.
    """

    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"


class RecoveryAction(str, enum.Enum):
    """What recovery should DO, in the directive's order of preference.

    Work Order 09 section 3:

        Can safely continue from existing verified state?
          yes -> RESUME
          no  -> Can task safely be replayed?
                   yes -> RETRY/FALLBACK
                   no  -> FAIL/DEGRADE truthfully

    Retrying is the *last* resort, not the first: replaying a task that already
    produced verified work throws that work away and pays for it twice.
    """

    RESUME = "resume"
    REPLAY = "replay"
    FAIL = "fail"


#: Failure names that MAY be recovered on another model.
_RETRIABLE_NAMES = frozenset({
    "ModelTimeoutError", "TimeoutError",
    "ModelRateLimitError",
    "ModelProviderUnavailableError", "ModelUnavailableError",
    "ModelProtocolError",           # malformed provider response; retry is safe
    "URLError", "ConnectionError", "ConnectionResetError", "OSError",
    "ModelError",                   # general case when nothing narrower applies
})

#: Failures that must NOT trigger a model switch.  Retrying these elsewhere
#: would either bypass a control or waste a call on something no model can fix.
_NOT_FALLBACK_NAMES = frozenset({
    "ModelCancelledError", "CancelledError",
    "PermissionError", "ToolDeniedError", "ApprovalDeniedError",
    "ModelAuthenticationError",     # a bad key is not fixed by another model
    "ModelInvalidRequestError",     # the request is wrong, not the model
    "ModelConfigurationError",
    "ValueError",                   # invalid task definition or internal plan
    "PolicyViolationError", "CapabilityUnavailableError",
    "DeterministicValidationError",
})

#: Reason substrings that mark a failure as NOT recoverable elsewhere.  Checked
#: first, so a permission denial that happens to mention a timeout still refuses.
_NOT_FALLBACK_REASONS = (
    "permission", "denied", "approval", "cancel", "policy",
    "not authorised", "not authorized", "invalid request", "invalid task",
    "impossible", "malformed internal plan",
)

_RETRIABLE_REASONS = (
    "timed out", "timeout", "rate limit", "temporarily unavailable",
    "unavailable", "could not reach", "network", "connection",
    "malformed", "not valid json",
)

#: One alternate-model step by default, after per-model attempts are spent.
MAX_ALTERNATE_MODEL_STEPS = 1

#: ONE total budget for the whole recovery envelope.  Letting each
#: dependency, tool and artifact consume its own maximum is how a
#: "bounded" envelope quietly becomes unbounded as a graph widens.
ENVELOPE_TOTAL_BUDGET = 6000


def decide_recovery(
    checkpoint: "TaskCheckpoint",
    executed_tools: Iterable[str] = (),
) -> tuple[RecoveryAction, str]:
    """Decide whether to RESUME, REPLAY or FAIL, in that order of preference.

    Resume wins when the task holds verified work and a named remaining step:
    continuing costs one call and keeps what was already achieved.  Replay is
    permitted only when nothing irreversible has happened.  Otherwise the task
    fails truthfully rather than repeating a side effect.

    Replay safety is asked of the Tool Fabric, never inferred from a tool name.
    """

    from .tool_effects import task_replay_safe

    if checkpoint.side_effects_performed and checkpoint.pending_step:
        return (
            RecoveryAction.RESUME,
            "verified work exists and a remaining step is named; continuing "
            "rather than replaying preserves it and avoids repeating the effect",
        )
    if checkpoint.completed_model_steps and checkpoint.pending_step:
        return (
            RecoveryAction.RESUME,
            "the task has completed model steps and a named remaining step",
        )

    safe, blocking = task_replay_safe(tuple(executed_tools))
    if safe:
        return (
            RecoveryAction.REPLAY,
            "nothing irreversible has happened, so the task may be replayed",
        )
    return (
        RecoveryAction.FAIL,
        "no verified state to resume from and the task is not safe to replay "
        f"({', '.join(blocking)}); failing truthfully instead of repeating the effect",
    )


@dataclass
class RecoveryEnvelope:
    """The bounded, authoritative context a recovering model receives.

    Work Order 09 section 4.  Recovery must NOT resend a transcript: the state
    is rebuilt from AMCA's own authoritative records — Goal Contract, shared
    state, task records, dependency results, ToolExecutionRecords and
    artifact/version records — and rendered as IDs, digests, references and
    short bounded excerpts.
    """

    run_id: str
    task_id: str
    goal_contract_summary: str = ""
    active_focus: str = ""
    task_requirements: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    original_model: str = ""
    failure_class: str = ""
    failure_reason: str = ""
    attempt_count: int = 0
    start_state_version: int = 0
    current_state_version: int = 0
    verified_completed_work: tuple[str, ...] = ()
    safe_tool_results: tuple[tuple[str, str], ...] = ()
    artifact_versions: tuple[tuple[str, int], ...] = ()
    pending_step: str = ""
    side_effects_performed: bool = False
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "goal_contract_summary": self.goal_contract_summary,
            "active_focus": self.active_focus,
            "task_requirements": list(self.task_requirements),
            "required_capabilities": list(self.required_capabilities),
            "original_model": self.original_model,
            "failure_class": self.failure_class,
            "failure_reason": self.failure_reason,
            "attempt_count": self.attempt_count,
            "start_state_version": self.start_state_version,
            "current_state_version": self.current_state_version,
            "verified_completed_work": list(self.verified_completed_work),
            "safe_tool_results": [list(item) for item in self.safe_tool_results],
            "artifact_versions": [list(item) for item in self.artifact_versions],
            "pending_step": self.pending_step,
            "side_effects_performed": self.side_effects_performed,
            "action": self.action,
        }

    def render(self, budget: int = ENVELOPE_TOTAL_BUDGET) -> str:
        """Render within ONE total budget, never per-item.

        A section that does not fit says so rather than silently consuming the
        rest of the budget, and the envelope always states whether repeating
        work is forbidden — that is the instruction the recovering model acts on.
        """

        lines = [
            f"run: {self.run_id}",
            f"task: {self.task_id}",
            f"action: {self.action}",
        ]
        if self.goal_contract_summary:
            lines.append(f"goal: {self.goal_contract_summary[:300]}")
        if self.active_focus:
            lines.append(f"active_focus: {self.active_focus[:300]}")
        if self.original_model:
            lines.append(f"original_model: {self.original_model}")
        if self.failure_class:
            lines.append(f"failure: {self.failure_class} - {self.failure_reason[:200]}")
        lines.append(f"attempts: {self.attempt_count}")
        if self.start_state_version or self.current_state_version:
            lines.append(
                f"state_version: {self.start_state_version} -> {self.current_state_version}"
            )
        if self.required_capabilities:
            lines.append("required_capabilities: " + ", ".join(self.required_capabilities))
        if self.task_requirements:
            lines.append("requirements: " + "; ".join(self.task_requirements[:6]))
        lines.append(f"side_effects_performed: {self.side_effects_performed}")

        # Reserve the tail FIRST.  Slicing the finished string to the budget
        # silently cut off whatever came last — including the notice that
        # content had been withheld, which is precisely the silent truncation
        # this envelope must not do.  Budgeting the tail up front means the
        # final slice is a last-resort guard, not the mechanism.
        tail: list[str] = []
        if self.pending_step:
            tail.append(f"pending_step: {self.pending_step[:200]}")
        if self.side_effects_performed:
            tail.append(
                "instruction: work already performed must not be repeated; continue "
                "from the preserved state above rather than starting over"
            )
        remaining = budget - sum(len(line) + 1 for line in lines) - sum(
            len(line) + 1 for line in tail
        )
        if remaining < 0:
            remaining = 0

        if self.verified_completed_work:
            lines.append("verified_completed_work:")
            for item in self.verified_completed_work:
                entry = f"  - {item[:200]}"
                if len(entry) > remaining:
                    lines.append("  - (further entries withheld: envelope budget exhausted)")
                    break
                lines.append(entry)
                remaining -= len(entry) + 1

        if self.safe_tool_results:
            lines.append("safe_tool_results:")
            for tool, digest in self.safe_tool_results:
                entry = f"  - {tool} [{digest}]"
                if len(entry) > remaining:
                    lines.append("  - (further results withheld: envelope budget exhausted)")
                    break
                lines.append(entry)
                remaining -= len(entry) + 1

        if self.artifact_versions:
            rendered = ", ".join(f"{name}@v{version}" for name, version in self.artifact_versions)
            lines.append(f"preserved_artifacts: {rendered[: max(0, remaining)]}")
            remaining -= len(lines[-1]) + 1

        lines.extend(tail)
        # The tail is reserved, so this should never bite; if a pathological
        # single field somehow exceeds the whole budget it still must not
        # produce a silent cut, so say so.
        text = "\n".join(lines)
        if len(text) > budget:
            marker = "\n(envelope truncated: content exceeded the budget)"
            return text[: max(0, budget - len(marker))] + marker
        return text


def _names_of(failure: Any) -> list[str]:
    if isinstance(failure, BaseException):
        return [type(failure).__name__, *(base.__name__ for base in type(failure).__mro__[1:])]
    return []


def failure_reason(failure: Any) -> str:
    """A bounded, human-readable reason for a failure."""

    if isinstance(failure, BaseException):
        text = str(failure).strip() or type(failure).__name__
    else:
        text = str(failure or "").strip()
    return text[:300]


def classify_failure(failure: Any) -> RecoveryClass:
    """Decide whether this failure may be recovered on another model."""

    reason = failure_reason(failure).casefold()

    for name in _names_of(failure):
        if name in _NOT_FALLBACK_NAMES:
            return RecoveryClass.NOT_FALLBACK_ELIGIBLE
    if any(marker in reason for marker in _NOT_FALLBACK_REASONS):
        return RecoveryClass.NOT_FALLBACK_ELIGIBLE

    for name in _names_of(failure):
        if name in _RETRIABLE_NAMES:
            return RecoveryClass.RETRIABLE_ON_ANOTHER_MODEL
    if any(marker in reason for marker in _RETRIABLE_REASONS):
        return RecoveryClass.RETRIABLE_ON_ANOTHER_MODEL

    # Unknown failures are not recovered: an unrecognised fault is not evidence
    # that another model would behave differently.
    return RecoveryClass.NOT_FALLBACK_ELIGIBLE


def is_fallback_eligible(failure: Any) -> bool:
    return classify_failure(failure) is RecoveryClass.RETRIABLE_ON_ANOTHER_MODEL


@dataclass(frozen=True)
class TaskCheckpoint:
    """What a task had already done when it failed.

    Recovery reads this before deciding whether a rerun is safe.  A task that
    has performed a side-effecting tool action cannot simply be replayed.
    """

    task_id: str
    completed_model_steps: int = 0
    completed_tool_executions: tuple[str, ...] = ()
    artifact_versions: tuple[tuple[str, int], ...] = ()
    side_effects_performed: bool = False
    pending_step: str = ""

    @property
    def safe_to_rerun(self) -> bool:
        """A rerun is safe only when nothing irreversible has happened yet."""

        return not self.side_effects_performed

    def recovery_context(self) -> str:
        """A bounded description of preserved work for a recovering model."""

        lines = [
            f"task: {self.task_id}",
            f"completed_model_steps: {self.completed_model_steps}",
            f"completed_tool_executions: {len(self.completed_tool_executions)}",
            f"side_effects_performed: {self.side_effects_performed}",
        ]
        if self.artifact_versions:
            rendered = ", ".join(f"{name}@v{version}" for name, version in self.artifact_versions)
            lines.append(f"preserved_artifacts: {rendered}")
        if self.pending_step:
            lines.append(f"pending_step: {self.pending_step}")
        if self.side_effects_performed:
            lines.append(
                "instruction: work already performed must not be repeated; "
                "continue from the preserved state above"
            )
        return "\n".join(lines)


@dataclass
class RecoveryAttempt:
    """One recorded recovery step.  Part of execution provenance."""

    task_id: str
    original_model: str
    failure_class: str
    failure_reason: str
    attempt_count: int
    fallback_requested: bool
    candidate_models: tuple[str, ...] = ()
    selected_fallback: str = ""
    selection_reason: str = ""
    fallback_result: str = ""
    final_task_status: str = ""
    #: Which recovery action was taken: resume, replay or fail.  Recorded so
    #: provenance distinguishes continuing from restarting.
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "original_model": self.original_model,
            "failure_class": self.failure_class,
            "failure_reason": self.failure_reason,
            "attempt_count": self.attempt_count,
            "fallback_requested": self.fallback_requested,
            "candidate_models": list(self.candidate_models),
            "selected_fallback": self.selected_fallback,
            "selection_reason": self.selection_reason,
            "fallback_result": self.fallback_result,
            "final_task_status": self.final_task_status,
            "action": self.action,
        }


def _satisfies(candidate: Mapping[str, Any], requirements: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether a candidate model meets every stated requirement."""

    for capability in requirements.get("required_capabilities", ()) or ():
        if capability not in (candidate.get("capabilities") or ()):
            return False, f"missing capability {capability!r}"
    for flag in ("supports_tools", "supports_vision", "supports_json"):
        if requirements.get(flag) and not candidate.get(flag):
            return False, f"{flag} required but not available"
    if requirements.get("requires_local") and not candidate.get("is_local"):
        return False, "a local model is required"
    if requirements.get("requires_remote") and candidate.get("is_local"):
        return False, "a remote model is required"
    needed = int(requirements.get("required_context", 0) or 0)
    window = int(candidate.get("context_window", 0) or 0)
    if needed and window and window < needed:
        return False, f"context window {window} is below the required {needed}"
    if candidate.get("visible") is False:
        return False, "model is hidden by the user"
    if candidate.get("available") is False:
        return False, "model is not currently available"
    max_cost = requirements.get("max_cost_usd")
    cost = candidate.get("expected_cost_usd")
    if max_cost is not None and cost is not None and float(cost) > float(max_cost):
        return False, "cost policy excludes this model"
    return True, ""


def select_fallback(
    candidates: Sequence[Mapping[str, Any]],
    *,
    failed_model: str,
    requirements: Mapping[str, Any] | None = None,
    already_tried: Iterable[str] = (),
) -> tuple[Mapping[str, Any] | None, str, tuple[str, ...]]:
    """Choose one replacement model, or explain why none qualifies.

    Returns ``(model, reason, candidates_considered)``.  The failed model and
    anything already tried are excluded, so recovery cannot bounce between the
    same two providers.  A candidate must satisfy EVERY stated requirement —
    the directive forbids falling back to "any model that responds".
    """

    req = dict(requirements or {})
    excluded = {str(failed_model)} | {str(name) for name in already_tried}
    # Run-scoped health can order and temporarily exclude candidates, but it
    # cannot change what any model is capable of.  Excluding a model here
    # affects THIS selection only and is not persisted.
    health = requirements.get("runtime_health") if isinstance(requirements, Mapping) else None
    ordered = list(candidates)
    if health is not None:
        ordered = health.order_candidates(ordered)
    considered: list[str] = []
    for candidate in ordered:
        name = str(candidate.get("model_ref") or candidate.get("name") or "")
        if not name or name in excluded:
            continue
        considered.append(name)
        if health is not None:
            skip, why = health.temporarily_excluded(name)
            if skip:
                continue
        ok, _why = _satisfies(candidate, req)
        if ok:
            return candidate, f"eligible replacement for {failed_model!r}", tuple(considered)
    if not considered:
        return None, "no candidate model was available to try", tuple(considered)
    return None, "no eligible replacement satisfied the requirements", tuple(considered)


def outcome_for(
    *,
    required_tasks: Sequence[str],
    completed_tasks: Sequence[str],
    cancelled_tasks: Sequence[str] = (),
) -> RunOutcome:
    """Classify a finished run as COMPLETE, DEGRADED or FAILED.

    The directive is explicit that a partial result must not be manufactured to
    avoid an empty screen: a run where nothing substantive succeeded is FAILED,
    and its failure message is the honest deliverable.
    """

    required = [str(name) for name in required_tasks]
    completed = {str(name) for name in completed_tasks}
    if required and all(name in completed for name in required):
        return RunOutcome.COMPLETE
    if any(name in completed for name in required):
        return RunOutcome.DEGRADED
    return RunOutcome.FAILED


def failure_report(
    *,
    run_status: str,
    failed_task: str = "",
    model: str = "",
    reason: str = "",
    attempts: int = 0,
    fallback_attempted: bool = False,
    fallback_model: str = "",
    completed_tasks: Sequence[str] = (),
    cancelled_tasks: Sequence[str] = (),
    artifacts_preserved: Sequence[str] = (),
) -> str:
    """A clean, user-visible failure summary.

    The directive requires that a failed run never resolves to an empty
    assistant message, and that the report is presentable rather than a raw
    debug dump.
    """

    lines = [f"Run {run_status}."]
    if failed_task:
        lines.append(f"Failed task: {failed_task}")
    if model:
        lines.append(f"Model: {model}")
    if reason:
        lines.append(f"Reason: {reason}")
    if attempts:
        lines.append(f"Attempts: {attempts}")
    lines.append(f"Fallback attempted: {'yes' if fallback_attempted else 'no'}")
    if fallback_model:
        lines.append(f"Fallback model: {fallback_model}")
    lines.append(
        f"Completed tasks: {len(completed_tasks)}"
        + (f" ({', '.join(completed_tasks)})" if completed_tasks else "")
    )
    lines.append(
        f"Cancelled dependent tasks: {len(cancelled_tasks)}"
        + (f" ({', '.join(cancelled_tasks)})" if cancelled_tasks else "")
    )
    if artifacts_preserved:
        lines.append(f"Artifacts preserved: {', '.join(artifacts_preserved)}")
    return "\n".join(lines)


__all__ = [
    "MAX_ALTERNATE_MODEL_STEPS",
    "ENVELOPE_TOTAL_BUDGET",
    "RecoveryAction",
    "RecoveryEnvelope",
    "RecoveryAttempt",
    "RecoveryClass",
    "RunOutcome",
    "TaskCheckpoint",
    "classify_failure",
    "decide_recovery",
    "failure_reason",
    "failure_report",
    "is_fallback_eligible",
    "outcome_for",
    "select_fallback",
]
