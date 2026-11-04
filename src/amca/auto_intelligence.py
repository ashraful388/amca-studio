"""Auto Intelligence — the meta-controller.

Work Order: "Auto is a meta-controller.  It does NOT get its own Model Router,
its own permission system, its own recovery subsystem, its own Agent->Model
binder."

What this module is: the layer that DERIVES an execution configuration from a
goal and hands it to the canonical components.  What it deliberately is not:

* it does not rank models — it builds a ``ModelSelectionRequest`` and calls the
  canonical router, so every selected model is traceable to a router decision;
* it does not pair agents to models — it calls the canonical binder;
* it does not handle failures — governed recovery does, through the router;
* it does not decide permissions — the Tool Fabric does.

The one thing it owns is the DECISION: what this run's configuration is, and
why.  That is recorded in a single ``AutoDecision`` that references the canonical
decisions rather than copying them, and projected into a short user-facing
explanation that names no implementation class.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


class TaskComplexity(str, enum.Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class AutoOutcome(str, enum.Enum):
    """The Goal Contract is the completion authority, not the call count."""

    COMPLETE = "complete"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskAnalysis:
    """What Auto understood about the task.  Derived, not configured."""

    complexity: TaskComplexity
    required_capabilities: frozenset[str] = frozenset()
    preferred_capabilities: frozenset[str] = frozenset()
    requires_tools: bool = False
    requires_structured_output: bool = False
    requires_vision: bool = False
    required_context: int = 0
    needs_browser: bool = False
    reasons: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "required_capabilities": sorted(self.required_capabilities),
            "preferred_capabilities": sorted(self.preferred_capabilities),
            "requires_tools": self.requires_tools,
            "requires_structured_output": self.requires_structured_output,
            "requires_vision": self.requires_vision,
            "required_context": self.required_context,
            "needs_browser": self.needs_browser,
            "reasons": list(self.reasons),
        }


#: Vocabulary that indicates a substantive capability is genuinely required.
#: Kept small and explicit: an over-eager vocabulary would spawn specialists for
#: tasks that do not need them, which the directive names as a failure mode.
_CAPABILITY_SIGNALS: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("code", "function", "script", "program", "implement", "refactor", "class",
      "module", "algorithm", "compile", "debug", "bug", "crash"), "coding", True),
    (("test", "pytest", "coverage", "assert"), "coding", False),
    (("review", "audit the code", "pull request"), "reasoning", False),
    (("security", "vulnerab", "exploit", "injection", "threat"), "reasoning", False),
    (("research", "literature", "cite", "citation", "sources", "summarise the",
      "summarize the", "compare the"), "long_context", False),
    (("documentation", "readme", "changelog", "user guide", "write the docs"),
      "long_context", False),
    (("data", "spreadsheet", "csv", "statistics", "table"), "json_mode", False),
    (("image", "screenshot", "photo", "picture", "diagram"), "vision", True),
    (("browser", "website", "web page", "url"), "tools", True),
)

#: Signals that a task is not simple.  Complexity drives TEAM SIZE, which the
#: directive makes mandatory: one model, two models, or three or more depending
#: on actual need.
_COMPLEXITY_SIGNALS: tuple[tuple[tuple[str, ...], TaskComplexity], ...] = (
    (("and then", "after that", "followed by", "multi-step", "pipeline",
      "end to end", "end-to-end", "workflow"), TaskComplexity.COMPLEX),
    (("compare", "contrast", "trade-off", "tradeoff", "evaluate", "design",
      "architecture", "plan", "migrate"), TaskComplexity.COMPLEX),
    (("analyse", "analyze", "investigate", "research", "review", "audit"),
     TaskComplexity.MODERATE),
    (("write", "implement", "create", "add", "build", "fix", "test"),
     TaskComplexity.MODERATE),
)


def analyse_task(goal: str, *, attachment_names: Sequence[str] = ()) -> TaskAnalysis:
    """Derive what the task needs.  No model or agent is chosen here."""

    text = str(goal or "").casefold()
    reasons: list[str] = []
    required: set[str] = set()
    preferred: set[str] = set()

    for keywords, capability, hard in _CAPABILITY_SIGNALS:
        if any(keyword in text for keyword in keywords):
            (required if hard else preferred).add(capability)
            reasons.append(f"matched {capability} vocabulary")

    if any(str(name).casefold().endswith((".png", ".jpg", ".jpeg", ".webp"))
           for name in attachment_names):
        required.add("vision")
        reasons.append("an image attachment requires vision")

    complexity = TaskComplexity.SIMPLE
    for keywords, level in _COMPLEXITY_SIGNALS:
        if any(keyword in text for keyword in keywords):
            complexity = level
            reasons.append(f"complexity raised to {level.value} by task vocabulary")
            break

    # A goal long enough to carry several instructions is not simple, even if no
    # single keyword matched.
    if complexity is TaskComplexity.SIMPLE and len(text.split()) > 60:
        complexity = TaskComplexity.MODERATE
        reasons.append("the goal is long enough to carry several instructions")

    needs_browser = "tools" in required and any(
        keyword in text for keyword in ("browser", "website", "web page", "url")
    )

    return TaskAnalysis(
        complexity=complexity,
        required_capabilities=frozenset(required),
        preferred_capabilities=frozenset(preferred - required),
        requires_tools="tools" in required,
        requires_structured_output="json_mode" in required,
        requires_vision="vision" in required,
        needs_browser=needs_browser,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def team_size_for(analysis: TaskAnalysis) -> int:
    """How many models this task justifies.

    Dynamic team sizing is mandatory: a simple task runs one model, and models
    that are not needed stay INACTIVE rather than being populated because they
    are configured.
    """

    if analysis.complexity is TaskComplexity.SIMPLE:
        return 1
    if analysis.complexity is TaskComplexity.MODERATE:
        return 2
    return 3


def roles_for(analysis: TaskAnalysis) -> tuple[str, ...]:
    """Only the substantive roles this task justifies.

    "Do not spawn all eight agents by default.  Agent count is not a measure of
    intelligence."  A simple task gets the minimum; a complex task gets the
    roles its capabilities imply, and nothing is added for symmetry.
    """

    roles: list[str] = []
    capabilities = analysis.required_capabilities | analysis.preferred_capabilities

    if "coding" in capabilities:
        roles.append("specialist.coding")
        # Testing and review are justified for coding only when the task is not
        # trivially simple.
        if analysis.complexity is not TaskComplexity.SIMPLE:
            roles.append("specialist.unit-testing")
        if analysis.complexity is TaskComplexity.COMPLEX:
            roles.append("specialist.code-review")
    if "long_context" in capabilities:
        roles.append("specialist.deep-research")
        if analysis.complexity is TaskComplexity.COMPLEX:
            roles.append("specialist.technical-writing")
    if "reasoning" in capabilities and analysis.complexity is TaskComplexity.COMPLEX:
        roles.append("specialist.security-analysis")

    # Verification is justified whenever more than one substantive role ran:
    # something must check the combined result.
    if len(roles) > 1:
        roles.append("core.verification-agent")

    return tuple(dict.fromkeys(roles))


@dataclass
class AutoDecision:
    """One authoritative Auto provenance record.

    It REFERENCES the canonical decisions — the router's SelectionDecision, the
    binder's participants, the recovery records — rather than copying them, so
    there is one source of truth per fact.
    """

    run_id: str
    goal: str
    analysis: TaskAnalysis
    requested_team_size: int = 1
    agent_roles: tuple[str, ...] = ()
    router_decisions: tuple[Mapping[str, Any], ...] = ()
    active_participants: tuple[Mapping[str, Any], ...] = ()
    inactive_models: tuple[str, ...] = ()
    binding_decisions: tuple[Mapping[str, Any], ...] = ()
    collaboration_mode: str = ""
    collaboration_reason: str = ""
    synchronization: str = ""
    verification_strategy: str = ""
    recovery: tuple[Mapping[str, Any], ...] = ()
    outcome: AutoOutcome = AutoOutcome.FAILED
    goal_contract_satisfied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "analysis": self.analysis.describe(),
            "requested_team_size": self.requested_team_size,
            "agent_roles": list(self.agent_roles),
            "router_decisions": [dict(item) for item in self.router_decisions],
            "active_participants": [dict(item) for item in self.active_participants],
            "inactive_models": list(self.inactive_models),
            "binding_decisions": [dict(item) for item in self.binding_decisions],
            "collaboration_mode": self.collaboration_mode,
            "collaboration_reason": self.collaboration_reason,
            "synchronization": self.synchronization,
            "verification_strategy": self.verification_strategy,
            "recovery": [dict(item) for item in self.recovery],
            "outcome": self.outcome.value,
            "goal_contract_satisfied": self.goal_contract_satisfied,
        }


def explain(decision: AutoDecision, *, model_names: Mapping[str, str] | None = None) -> str:
    """A concise user-facing projection.

    "Do not expose implementation class names by default."  Model refs are
    rendered through ``model_names`` when the caller can supply readable names,
    and the roles are shown as their human titles rather than agent ids.
    """

    names = model_names or {}

    def readable(ref: str) -> str:
        return str(names.get(ref, ref))

    lines = ["Auto Intelligence"]
    active = [readable(str(item.get("model_ref", ""))) for item in decision.active_participants]
    if active:
        lines.append("Models: " + " + ".join(active))
    if decision.agent_roles:
        lines.append("Agents: " + ", ".join(_role_title(role) for role in decision.agent_roles))
    if decision.collaboration_mode:
        lines.append("Strategy: " + _mode_title(decision.collaboration_mode))
    lines.append("Status: " + _outcome_title(decision.outcome))
    if decision.inactive_models:
        lines.append(
            f"{len(decision.inactive_models)} configured model(s) were not needed for this task"
        )
    for record in decision.recovery:
        if record.get("fallback_requested"):
            lines.append(
                "Primary model failed; AMCA continued with an eligible alternate model."
            )
        elif record.get("failure_reason"):
            lines.append(
                "Primary model failed and no eligible alternate was available."
            )
    return "\n".join(lines)


_ROLE_TITLES = {
    "specialist.coding": "Coding",
    "specialist.unit-testing": "Testing",
    "specialist.code-review": "Review",
    "specialist.security-analysis": "Security",
    "specialist.deep-research": "Research",
    "specialist.technical-writing": "Documentation",
    "core.verification-agent": "Validation",
}


def _role_title(agent_id: str) -> str:
    return _ROLE_TITLES.get(str(agent_id), str(agent_id))


def _mode_title(mode: str) -> str:
    return {
        "solo": "Single model",
        "joint": "Joint collaboration",
        "independent_discuss": "Independent analysis then discussion",
        "parallel": "Parallel work",
        "adaptive_hybrid": "Adaptive",
    }.get(str(mode), str(mode))


def _outcome_title(outcome: AutoOutcome) -> str:
    return {
        AutoOutcome.COMPLETE: "Completed",
        AutoOutcome.DEGRADED: "Completed with limitations",
        AutoOutcome.FAILED: "Failed",
        AutoOutcome.CANCELLED: "Cancelled",
    }.get(outcome, str(outcome))


def choose_collaboration(analysis: TaskAnalysis, team_size: int) -> tuple[str, str]:
    """Choose a mode FROM the task analysis, with a stated reason.

    "Do not simply map complex task -> Adaptive Hybrid without supporting
    reasons."  Each branch states why.
    """

    if team_size <= 1:
        return "solo", "one model is sufficient for this task's complexity"
    if analysis.complexity is TaskComplexity.COMPLEX:
        return (
            "independent_discuss",
            "a complex task benefits from independent attempts before discussion, "
            "so disagreement is visible rather than averaged away",
        )
    return (
        "joint",
        "several models contribute to one result, which suits a task with "
        "interdependent steps",
    )


def choose_synchronization(mode: str, team_size: int) -> tuple[str, str]:
    """Synchronization policy, using the mechanisms the kernel already has."""

    if team_size <= 1:
        return "async", "a single participant has nothing to synchronize with"
    if mode == "independent_discuss":
        return (
            "hard_barrier",
            "independent attempts must complete before the reveal, or the "
            "discussion would be anchored by whoever finished first",
        )
    return "soft", "participants proceed without waiting unless a conflict appears"



@dataclass(frozen=True)
class ParticipantCounts:
    """The five participation facts, kept SEPARATE.

    The manager's correction: an earlier report said "team=3" and separately that
    the complex case used all four models, which conflated distinct facts.  Four
    configured models, three active, five specialist roles and one inactive is a
    valid and self-consistent state — so each count is its own field and
    ``team_size`` means ACTIVE PARTICIPANTS only.
    """

    configured: int = 0
    active: int = 0
    specialist_roles: int = 0
    general_participants: int = 0
    inactive: int = 0

    @property
    def team_size(self) -> int:
        """Active participants.  Never the configured count, never the role count."""

        return self.active

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "active": self.active,
            "specialist_roles": self.specialist_roles,
            "general_participants": self.general_participants,
            "inactive": self.inactive,
            "team_size": self.team_size,
        }


def counts_from(
    participants: Sequence[Mapping[str, Any]],
    agent_roles: Sequence[str],
    *,
    configured: int | None = None,
) -> ParticipantCounts:
    """Derive the five counts from the canonical participant records.

    ``specialist_roles`` counts ROLES, not models: one model may serve several
    roles, and conflating the two is what produced the earlier inconsistency.
    """

    active = [item for item in participants if item.get("status") != "inactive"]
    general = [
        item for item in active
        if item.get("status") == "active_general" or not str(item.get("agent_id") or "").strip()
    ]
    return ParticipantCounts(
        configured=len(participants) if configured is None else int(configured),
        active=len(active),
        specialist_roles=len(agent_roles),
        general_participants=len(general),
        inactive=len(participants) - len(active),
    )


@dataclass
class ExecutionConfiguration:
    """The ONE translation boundary from decision to execution.

    The manager's section 5: this transports canonical decisions into execution
    and does NOTHING else.  It does not rerank models, rebind agents, reinterpret
    capabilities or change permissions — every one of those was already decided
    by the canonical component that owns it, and re-deciding here would create a
    second authority.
    """

    run_id: str
    goal: str
    goal_contract_satisfied: bool = False
    active_participants: tuple[str, ...] = ()
    inactive_participants: tuple[str, ...] = ()
    agent_bindings: tuple[Mapping[str, Any], ...] = ()
    selection_decisions: tuple[Mapping[str, Any], ...] = ()
    collaboration_mode: str = "solo"
    synchronization: str = "async"
    verification_strategy: str = ""
    permission_context: str = ""
    counts: ParticipantCounts = field(default_factory=ParticipantCounts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "goal_contract_satisfied": self.goal_contract_satisfied,
            "active_participants": list(self.active_participants),
            "inactive_participants": list(self.inactive_participants),
            "agent_bindings": [dict(item) for item in self.agent_bindings],
            "selection_decisions": [dict(item) for item in self.selection_decisions],
            "collaboration_mode": self.collaboration_mode,
            "synchronization": self.synchronization,
            "verification_strategy": self.verification_strategy,
            "permission_context": self.permission_context,
            "counts": self.counts.to_dict(),
        }


def to_execution_configuration(
    decision: "AutoDecision",
    *,
    permission_context: str = "",
) -> ExecutionConfiguration:
    """Translate a decision into what the runtime consumes.

    A pure transport: every value comes from the decision, which came from the
    canonical router, binder and analysis.  There is deliberately no branch in
    here that could pick a different model or role — if this function could
    disagree with the decision, the decision would not be authoritative.
    """

    # Section 7: the runtime receives ACTIVE_BOUND and ACTIVE_GENERAL only.  An
    # entry recorded with status "inactive" must not reach execution even when
    # it appears in the participant list, or a model the run did not select
    # would be given work.
    active = tuple(
        str(item.get("model_ref", ""))
        for item in decision.active_participants
        if str(item.get("status", "") or "").strip().casefold() != "inactive"
    )
    inactive = tuple(str(ref) for ref in decision.inactive_models)
    return ExecutionConfiguration(
        run_id=decision.run_id,
        goal=decision.goal,
        goal_contract_satisfied=decision.goal_contract_satisfied,
        active_participants=active,
        inactive_participants=inactive,
        agent_bindings=decision.binding_decisions,
        selection_decisions=decision.router_decisions,
        collaboration_mode=decision.collaboration_mode or "solo",
        synchronization=decision.synchronization or "async",
        verification_strategy=decision.verification_strategy,
        permission_context=permission_context,
        counts=counts_from(
            # An inactive model may appear in BOTH the participant records and
            # the inactive list, depending on which path built the decision.
            # Counting it twice would overstate the configured set, so the
            # inactive entries are added only for models not already recorded.
            tuple(decision.active_participants)
            + tuple(
                {"model_ref": ref, "status": "inactive"}
                for ref in inactive
                if ref not in {
                    str(item.get("model_ref", "")) for item in decision.active_participants
                }
            ),
            decision.agent_roles,
        ),
    )


__all__ = [
    "AutoDecision",
    "ExecutionConfiguration",
    "ParticipantCounts",
    "AutoOutcome",
    "TaskAnalysis",
    "TaskComplexity",
    "analyse_task",
    "choose_collaboration",
    "choose_synchronization",
    "counts_from",
    "explain",
    "roles_for",
    "team_size_for",
    "to_execution_configuration",
]
