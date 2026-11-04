"""Capability-based agent -> model binding.

Work Order 09 section 6, and the architecture directive's "Agent role ->
Task requirements -> Capability requirements -> Model Router -> Model Registry
-> eligible models -> selection policy -> bound model".

What this replaces: assignment by ROUND-ROBIN INDEX.  The previous code paired
the Nth adapter with the Nth selected role (`selected_roles[index % len(...)]`),
so a coding agent could be bound to whatever model happened to sit at that
offset — no capability matching at all.  The directive is explicit that this
must not be hard-coded or arbitrary:

    Do NOT hard-code: Research Agent -> qwen3, Coding Agent -> model X

Two halves:

* **Role descriptors** — each role declares what it REQUIRES and what it
  PREFERS, in capability vocabulary.  Requirements exclude; preferences rank.
* **Binding** — for each role, ask the canonical Model Router with that role's
  requirements.  One selection pipeline, so a binding cannot contradict what
  Auto or the CLI would choose.

Only capabilities the Model Registry actually represents are used.  Nothing here
invents a capability, and an unmeasured one stays unknown rather than being
assumed.
"""

from __future__ import annotations

import enum

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class AgentRoleRequirements:
    """What a role needs from a model.  Requirements exclude, preferences rank.

    The directive's section 6.2 is explicit that these must be separated: a
    preference that silently excluded would make a role unbindable on a machine
    that lacks one nice-to-have capability.
    """

    agent_id: str
    required_capabilities: frozenset[str] = frozenset()
    preferred_capabilities: frozenset[str] = frozenset()
    required_tools: bool = False
    required_structured_output: bool = False
    required_vision: bool = False
    required_context: int = 0
    requires_local: bool = False
    rationale: str = ""


#: The capability vocabulary the Model Registry represents today.  A role
#: descriptor may only use these; inventing a capability would make a role
#: permanently unbindable while looking reasonable.
KNOWN_CAPABILITIES = frozenset({
    "coding", "tools", "vision", "audio", "json_mode", "streaming",
    "system_messages", "reasoning", "long_context", "retrieval",
})

#: Role descriptors.  Deliberately expressed as REQUIREMENTS, not model names.
#: A role with no hard requirement can bind to any eligible model, which is the
#: honest position when nobody has measured role-specific strengths.
ROLE_REQUIREMENTS: dict[str, AgentRoleRequirements] = {
    "specialist.coding": AgentRoleRequirements(
        agent_id="specialist.coding",
        preferred_capabilities=frozenset({"coding", "tools"}),
        rationale="needs to write and reason about code",
    ),
    "specialist.code-review": AgentRoleRequirements(
        agent_id="specialist.code-review",
        preferred_capabilities=frozenset({"coding", "reasoning"}),
        rationale="reads code and argues about correctness",
    ),
    "specialist.unit-testing": AgentRoleRequirements(
        agent_id="specialist.unit-testing",
        preferred_capabilities=frozenset({"coding", "tools"}),
        rationale="writes tests and may run them",
    ),
    "specialist.bug-reproduction": AgentRoleRequirements(
        agent_id="specialist.bug-reproduction",
        preferred_capabilities=frozenset({"coding", "reasoning"}),
        rationale="reproduces a defect from a description",
    ),
    "specialist.security-analysis": AgentRoleRequirements(
        agent_id="specialist.security-analysis",
        preferred_capabilities=frozenset({"coding", "reasoning"}),
        rationale="adversarial analysis of code and configuration",
    ),
    "specialist.deep-research": AgentRoleRequirements(
        agent_id="specialist.deep-research",
        preferred_capabilities=frozenset({"long_context", "retrieval"}),
        rationale="reads many sources and holds them together",
    ),
    "specialist.technical-writing": AgentRoleRequirements(
        agent_id="specialist.technical-writing",
        preferred_capabilities=frozenset({"long_context"}),
        rationale="produces structured prose",
    ),
    "specialist.data-analysis": AgentRoleRequirements(
        agent_id="specialist.data-analysis",
        preferred_capabilities=frozenset({"tools", "json_mode"}),
        rationale="works with structured data",
    ),
    "specialist.report-writing": AgentRoleRequirements(
        agent_id="specialist.report-writing",
        preferred_capabilities=frozenset({"long_context", "json_mode"}),
        rationale="produces a structured document",
    ),
    "specialist.browser": AgentRoleRequirements(
        agent_id="specialist.browser",
        preferred_capabilities=frozenset({"tools"}),
        required_tools=True,
        rationale="drives a governed browser through tools",
    ),
    "core.verification-agent": AgentRoleRequirements(
        agent_id="core.verification-agent",
        preferred_capabilities=frozenset({"reasoning", "json_mode"}),
        rationale="checks a claim against evidence",
    ),
}


def requirements_for(agent_id: str) -> AgentRoleRequirements:
    """A role's requirements, or an empty set for a role with no descriptor.

    An unknown role gets NO hard requirements rather than invented ones: it may
    bind to any eligible model, and the binding records that nothing specific
    was asked of it.
    """

    known = ROLE_REQUIREMENTS.get(str(agent_id or ""))
    if known is not None:
        return known
    return AgentRoleRequirements(agent_id=str(agent_id or ""), rationale="no specific requirements declared")


class ParticipantStatus(str, enum.Enum):
    """Whether a configured model takes part in a run, and how.

    The manager's correction: a model MAY legitimately be inactive.  AMCA
    supports dynamic team size, Solo, partial Model Teams and Auto-selected
    subsets, so "every configured adapter must have a role assignment" is an
    architecturally wrong invariant — and it is what caused the KeyError that
    was papered over by inventing assignments.
    """

    ACTIVE_BOUND = "active_bound"        # taking part, with a capability-bound role
    ACTIVE_GENERAL = "active_general"    # taking part, no specialised role claimed it
    INACTIVE = "inactive"                # configured but not in this run


#: Roles that govern a run.  They must NEVER be used as a sink for a model that
#: simply needed an assignment: that would collapse the control plane into the
#: worker plane, which the directive forbids by name.
CONTROL_ROLES = frozenset({
    "core.agent-governor",
    "core.permission-agent",
    "core.tool-router",
    "core.collaboration-router",
})


@dataclass
class ParticipantRecord:
    """One configured model's participation in a run."""

    model_ref: str
    status: ParticipantStatus
    agent_id: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_ref": self.model_ref,
            "status": self.status.value,
            "agent_id": self.agent_id,
            "rationale": self.rationale,
        }


@dataclass
class AgentBinding:
    """One agent bound to one model, with the reason recorded."""

    agent_id: str
    model_ref: str
    rationale: str = ""
    required_capabilities: tuple[str, ...] = ()
    preferred_capabilities: tuple[str, ...] = ()
    bound: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "model_ref": self.model_ref,
            "rationale": self.rationale,
            "required_capabilities": list(self.required_capabilities),
            "preferred_capabilities": list(self.preferred_capabilities),
            "bound": self.bound,
        }


@dataclass
class BindingResult:
    bindings: tuple[AgentBinding, ...] = ()
    unbound: tuple[tuple[str, str], ...] = ()   # (agent_id, reason)
    decisions: tuple[Mapping[str, Any], ...] = ()
    participants: tuple[ParticipantRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "bindings": [item.to_dict() for item in self.bindings],
            "unbound": [{"agent_id": a, "reason": r} for a, r in self.unbound],
            "decisions": [dict(item) for item in self.decisions],
            "participants": [item.to_dict() for item in self.participants],
        }

    @property
    def general_model_refs(self) -> tuple[str, ...]:
        """Explicitly chosen models taking part without a specialised role."""

        return tuple(
            item.model_ref for item in self.participants
            if item.status is ParticipantStatus.ACTIVE_GENERAL
        )

    @property
    def active_model_refs(self) -> tuple[str, ...]:
        """Models taking part in this run.  Inactive ones are simply not here."""

        return tuple(
            item.model_ref for item in self.participants
            if item.status is not ParticipantStatus.INACTIVE
        )

    @property
    def inactive_model_refs(self) -> tuple[str, ...]:
        return tuple(
            item.model_ref for item in self.participants
            if item.status is ParticipantStatus.INACTIVE
        )

    def model_for(self, agent_id: str) -> str:
        for item in self.bindings:
            if item.agent_id == str(agent_id):
                return item.model_ref
        return ""


def bind_agents(
    agent_ids: Sequence[str],
    candidates: Sequence[Any],
    *,
    runtime_health: Any = None,
    prefer_local: bool = False,
    cost_policy: Any = None,
    required_participants: Sequence[str] = (),
) -> BindingResult:
    """Bind each agent to a model through the canonical Model Router.

    Each role asks for what it needs; the router applies hard filters, then
    ranking, then the neutral tie-break.  A role with no eligible model is
    reported as UNBOUND with the router's reason rather than being given an
    arbitrary model — an agent quietly bound to a model that cannot do its job
    is worse than an agent that says it could not be bound.

    Models already bound are not reused for a different role when an alternative
    exists, so a single model is not silently assigned to every agent.  Reuse is
    permitted when it is the only eligible option, and is recorded as such.

    ``required_participants`` carries an EXPLICIT participation constraint — the
    user chose a Model Team, so those models take part.  The directive is clear
    that this is different from Auto: a user-selected model becomes an
    ACTIVE_GENERAL participant when no specialised role claims it, and is never
    handed a governance role merely to have one.  A model that is neither bound
    nor explicitly chosen is INACTIVE, which is a normal outcome.
    """

    from .model_router import (
        CostPolicy,
        ModelSelectionRequest,
        select_model,
    )

    policy = cost_policy if cost_policy is not None else CostPolicy.BALANCED
    bindings: list[AgentBinding] = []
    unbound: list[tuple[str, str]] = []
    decisions: list[Mapping[str, Any]] = []
    used: dict[str, str] = {}
    mandated = {str(ref) for ref in required_participants if str(ref)}

    for agent_id in agent_ids:
        requirements = requirements_for(agent_id)

        # A role asks only about models not already claimed by another role.
        # It is NOT given a claimed model merely to avoid an unbound role: an
        # unbound role is an honest outcome, and reusing one model for every
        # agent would misrepresent the team that actually ran.
        available = [
            item for item in candidates
            if str(getattr(item, "model_ref", "")) not in set(used.values())
        ]
        reused = False
        if not available:
            available = list(candidates)
            reused = True

        request = ModelSelectionRequest(
            task_type=f"agent:{agent_id}",
            required_capabilities=requirements.required_capabilities,
            preferred_capabilities=requirements.preferred_capabilities,
            required_tools=requirements.required_tools,
            required_structured_output=requirements.required_structured_output,
            required_vision=requirements.required_vision,
            required_context=requirements.required_context,
            requires_local=requirements.requires_local,
            cost_policy=policy,
            prefer_local=prefer_local,
            runtime_health=runtime_health,
            maximum_participants=1,
        )
        decision = select_model(available, request)
        decisions.append({"agent_id": agent_id, **decision.to_dict()})

        if not decision.selected_model:
            reason = (
                decision.excluded_models[0].reason
                if decision.excluded_models
                else "no eligible model was available"
            )
            unbound.append((agent_id, reason))
            continue

        used[agent_id] = decision.selected_model
        bindings.append(
            AgentBinding(
                agent_id=agent_id,
                model_ref=decision.selected_model,
                rationale=(
                    f"{requirements.rationale}"
                    + (" (reused: no alternative model was eligible)" if reused else "")
                ),
                required_capabilities=tuple(sorted(requirements.required_capabilities)),
                preferred_capabilities=tuple(sorted(requirements.preferred_capabilities)),
            )
        )

    # Explicit participation for EVERY candidate, so a model that takes no part
    # is recorded as inactive rather than silently absent.
    bound_models = {item.model_ref: item for item in bindings}
    participants: list[ParticipantRecord] = []
    for candidate in candidates:
        ref = str(getattr(candidate, "model_ref", ""))
        bound = bound_models.get(ref)
        if bound is not None:
            participants.append(ParticipantRecord(
                model_ref=ref, status=ParticipantStatus.ACTIVE_BOUND,
                agent_id=bound.agent_id, rationale=bound.rationale,
            ))
        elif ref in mandated:
            # The user explicitly chose this model, so it takes part even though
            # no specialised role claimed it.  It is a general participant, not
            # a governance role, and not inactive.
            participants.append(ParticipantRecord(
                model_ref=ref, status=ParticipantStatus.ACTIVE_GENERAL,
                rationale=(
                    "explicitly selected by the user; taking part as a general "
                    "participant because no specialised role required this model"
                ),
            ))
        else:
            participants.append(ParticipantRecord(
                model_ref=ref, status=ParticipantStatus.INACTIVE,
                rationale="configured but not selected for this run",
            ))

    return BindingResult(
        bindings=tuple(bindings),
        unbound=tuple(unbound),
        decisions=tuple(decisions),
        participants=tuple(participants),
    )


__all__ = [
    "CONTROL_ROLES",
    "AgentBinding",
    "AgentRoleRequirements",
    "BindingResult",
    "ParticipantRecord",
    "ParticipantStatus",
    "KNOWN_CAPABILITIES",
    "ROLE_REQUIREMENTS",
    "bind_agents",
    "requirements_for",
]
