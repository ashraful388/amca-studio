"""Desktop collaboration request and decision contracts (milestone C1.1).

This module lets ordinary desktop Chat represent every AMCA collaboration
architecture while keeping two decisions structurally separate:

* WHO works - ``ModelSetDecision`` selects eligible models / model teams;
* HOW they work - ``ArchitectureDecision`` selects Solo, Joint,
  Independent->Discuss, Parallel Decomposition, or Adaptive Hybrid.

Participant count is a feasibility input only; it is never the classifier that
maps ">1 model" onto Joint.  ``TaskShape`` is deliberately lexical and
deterministic.  Runtime observations such as active critical objections or
parallel conflicts are carried by ``CollaborationSignals`` and must come from
canonical project state/events, never from user wording, so a sentence like
"explain what a critical objection means" cannot trigger Joint review.

The decision table below was reviewed against the AMCA master specifications
(2026-08-23 instruction file, Section 8) and approved as the C1.1 contract.
It is deterministic and auditable; benchmark calibration may adjust the
lexical thresholds later without changing this contract.

Frozen semantics (milestone review, 2026-08-23): ``conversation.mode`` and
``RunJob.mode`` always record the architecture that will actually execute.
A deferred Auto recommendation - Parallel before its planner lands - lives
only in the persisted ``collaboration_decision`` artifact (candidate rule,
triggers, reasons); the service fails closed before any conversation write,
so a non-executed architecture can never appear as a chat's mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from amca.domain import CollaborationMode, ComplexityLevel, SyncMode
from amca.modes import DecompositionPlan, WorkUnit, WorkUnitOutputContract
from amca.routing import ComplexityRouter

if TYPE_CHECKING:
    from amca.domain import ProjectState


SUPPORTED_ARCHITECTURES: frozenset[CollaborationMode] = frozenset(
    {
        CollaborationMode.SOLO,
        CollaborationMode.JOINT,
        CollaborationMode.PARALLEL,
        CollaborationMode.INDEPENDENT_DISCUSS,
        CollaborationMode.ADAPTIVE_HYBRID,
    }
)

_MULTI_PARTICIPANT_ARCHITECTURES: frozenset[CollaborationMode] = frozenset(
    SUPPORTED_ARCHITECTURES - {CollaborationMode.SOLO}
)

#: Default synchronization policy per architecture, mirroring the kernel's
#: SynchronizationRouter defaults so the desktop contract never contradicts it.
ARCHITECTURE_SYNC_DEFAULTS: Mapping[CollaborationMode, SyncMode] = {
    CollaborationMode.SOLO: SyncMode.SOFT,
    CollaborationMode.JOINT: SyncMode.BOUNDED_STALENESS,
    CollaborationMode.INDEPENDENT_DISCUSS: SyncMode.QUORUM,
    CollaborationMode.PARALLEL: SyncMode.SOFT,
    CollaborationMode.ADAPTIVE_HYBRID: SyncMode.BOUNDED_STALENESS,
}

#: Bounded work units each architecture spends per desktop run.  These bound
#: cost reservations; actual settlement always uses measured usage.
ARCHITECTURE_EXPECTED_ROUNDS: Mapping[CollaborationMode, int] = {
    CollaborationMode.SOLO: 1,
    CollaborationMode.JOINT: 2,
    CollaborationMode.INDEPENDENT_DISCUSS: 2,
    # Execution units plus the mandatory integration unit form two DAG levels.
    CollaborationMode.PARALLEL: 2,
    CollaborationMode.ADAPTIVE_HYBRID: 2,
}

#: Human-readable protocol summary persisted into the agent organization plan
#: so the desktop UI can describe what each bounded round does per mode.
ARCHITECTURE_ROUND_DESCRIPTIONS: Mapping[CollaborationMode, Mapping[str, str]] = {
    CollaborationMode.SOLO: {
        "round_1": "one model completes the bounded task and verifies the result",
        "round_2": "not required for Solo",
    },
    CollaborationMode.JOINT: {
        "round_1": "independent contribution into shared state",
        "round_2": (
            "inspect shared contributions, raise bounded objections or "
            "corrections, and revise"
        ),
    },
    CollaborationMode.INDEPENDENT_DISCUSS: {
        "round_1": "sealed independent attempts hidden from other participants",
        "round_2": (
            "reveal all attempts, compare results, resolve disagreements with "
            "evidence, and synthesize"
        ),
    },
    CollaborationMode.PARALLEL: {
        "round_1": "parallel work-unit branches run their assigned subproblems",
        "round_2": "integration gate merges verified branch outputs",
    },
    CollaborationMode.ADAPTIVE_HYBRID: {
        "round_1": "audited Adaptive supervisor selects the starting base mode",
        "round_2": (
            "base-mode execution with recorded transitions when task evidence "
            "changes"
        ),
    },
}

#: Architectures the desktop Chat executor can run today.  All five are
#: executable since the C1.6 deterministic decomposition planner arrived;
#: the kernel still fails closed if a plan is missing or references unknown
#: assignees.
DESKTOP_EXECUTABLE_ARCHITECTURES: frozenset[CollaborationMode] = frozenset(
    SUPPORTED_ARCHITECTURES
)

_REQUESTED_BY_AUTO = "auto"
_REQUESTED_BY_USER_LOCK = "user_lock"


def parse_requested_mode(value: str | None) -> CollaborationMode | None:
    """Parse an optional explicit architecture lock from a request body.

    ``None``, empty strings, and ``"auto"`` mean Auto Intelligence owns the
    architecture decision.  Every supported architecture value is valid at the
    contract level; unknown values are rejected with the supported list.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() == "auto":
        return None
    folded = text.casefold()
    try:
        mode = CollaborationMode(text)
    except ValueError:
        # Domain aliases such as ``joint_deliberation`` or ``hybrid`` are
        # member names rather than values, so resolve them explicitly.
        by_name = {
            name.casefold(): member
            for name, member in CollaborationMode.__members__.items()
            if member.name != member.value
        }
        mode = by_name.get(folded)
        if mode is None:
            raise ValueError(
                "unsupported collaboration mode "
                f"{text!r}; supported: "
                + ", ".join(sorted(item.value for item in SUPPORTED_ARCHITECTURES))
                + ", or omit/auto for Auto Intelligence"
            ) from None
    if mode not in SUPPORTED_ARCHITECTURES:
        raise ValueError(
            f"collaboration mode {mode.value!r} is not an executable architecture"
        )
    return mode


@dataclass(frozen=True, slots=True)
class TaskShape:
    """Deterministic lexical characteristics of one bounded task.

    Deliberately excludes runtime/project-state observations: those belong to
    ``CollaborationSignals``.  Nothing here is derived from how many models
    happen to be configured.
    """

    complexity: ComplexityLevel
    coupled: bool
    decomposable: bool
    verification_heavy: bool
    novelty_required: bool

    @classmethod
    def from_goal(cls, goal: str) -> "TaskShape":
        """Classify goal text with transparent lexical cues.

        Thresholds are intentionally conservative and remain open to
        benchmark calibration; classification never calls a model.
        """

        text = goal.casefold()
        verification_heavy = any(
            term in text
            for term in (
                "verify",
                "fact-check",
                "cross-check",
                "double-check",
                "validate",
                "prove",
            )
        )
        novelty_required = any(
            term in text
            for term in (
                "novel",
                "original",
                "creative",
                "invent",
                "unprecedented",
                "radical",
                "breakthrough",
            )
        )
        domains = sum(
            any(keyword in text for keyword in keywords)
            for keywords in (
                ("code", "repository", "git", "debug", "test ", "software"),
                ("data", "spreadsheet", "csv", "statistic", "analy"),
                ("research", "literature", "source", "citation", "evidence"),
                ("document", "report", "manuscript", "presentation", "pdf"),
            )
        )
        sequenced = any(
            cue in text
            for cue in ("then ", "step 1", "first ", "after that", "followed by")
        ) or any(f"{index}." in text for index in range(1, 6))
        shared_deliverable = any(
            cue in text
            for cue in (
                "single report",
                "one report",
                "one document",
                "a single document",
                "combined into",
                "merged into",
            )
        )
        decomposable = sequenced or domains >= 2
        coupled = (not decomposable) or shared_deliverable
        complexity = ComplexityRouter().assess(goal, novelty_required=novelty_required)
        return cls(
            complexity=complexity,
            coupled=coupled,
            decomposable=decomposable,
            verification_heavy=verification_heavy,
            novelty_required=novelty_required,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "complexity": self.complexity.value,
            "coupled": self.coupled,
            "decomposable": self.decomposable,
            "verification_heavy": self.verification_heavy,
            "novelty_required": self.novelty_required,
        }


@dataclass(frozen=True, slots=True)
class CollaborationSignals:
    """Runtime observations from canonical state/events - never lexical.

    ``has_critical_objection`` mirrors ``ProjectState.has_critical_objection``
    and ``high_disagreement`` mirrors severe unresolved disagreements.
    ``parallel_conflict`` is supplied by callers that observed conflicting
    parallel results; the desktop chat path leaves it unset until Parallel
    Decomposition execution lands.
    """

    has_critical_objection: bool = False
    parallel_conflict: bool = False
    high_disagreement: bool = False

    @classmethod
    def from_state(cls, state: "ProjectState") -> "CollaborationSignals":
        from amca.domain import Severity

        return cls(
            has_critical_objection=bool(state.has_critical_objection),
            high_disagreement=any(
                disagreement.severity in {Severity.HIGH, Severity.CRITICAL}
                for disagreement in state.unresolved_disagreements
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "has_critical_objection": self.has_critical_objection,
            "parallel_conflict": self.parallel_conflict,
            "high_disagreement": self.high_disagreement,
        }


@dataclass(frozen=True, slots=True)
class ModelSetDecision:
    """WHO works: the eligible model / model-team selection."""

    policy: str
    model_refs: tuple[str, ...]
    rationale: str
    confidence: str
    expected_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if not self.policy:
            raise ValueError("model-set decision requires a selection policy")
        if any(not ref for ref in self.model_refs):
            raise ValueError("model-set decision references cannot be blank")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy,
            "model_refs": list(self.model_refs),
            "rationale": self.rationale,
            "confidence": self.confidence,
            "expected_cost_usd": self.expected_cost_usd,
        }


@dataclass(frozen=True, slots=True)
class ArchitectureDecision:
    """HOW they work: the collaboration architecture decision record.

    ``candidate_mode`` records the pre-feasibility table outcome and
    ``degradation_reason`` explains any fallback to Solo, so feasibility
    handling stays visible instead of silently rewriting history.
    """

    mode: CollaborationMode
    sync_mode: SyncMode
    policy_rule: str
    triggers: tuple[str, ...]
    reasons: tuple[str, ...]
    requested_by: str
    expected_rounds: int
    candidate_mode: CollaborationMode | None = None
    degradation_reason: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", CollaborationMode(self.mode))
            if self.candidate_mode is not None:
                object.__setattr__(
                    self, "candidate_mode", CollaborationMode(self.candidate_mode)
                )
        except ValueError:
            raise ValueError(
                "architecture decision mode must be a supported collaboration mode"
            ) from None
        if self.mode not in SUPPORTED_ARCHITECTURES:
            raise ValueError(
                f"architecture decision mode {self.mode.value!r} is not supported"
            )
        if self.requested_by not in {_REQUESTED_BY_AUTO, _REQUESTED_BY_USER_LOCK}:
            raise ValueError(
                f"architecture requested_by {self.requested_by!r} is not recognized"
            )
        if not self.policy_rule:
            raise ValueError("architecture decision requires an auditable policy rule")
        if self.expected_rounds < 1:
            raise ValueError("architecture expected_rounds must be positive")
        if self.candidate_mode is None and self.degradation_reason is not None:
            raise ValueError("degradation_reason requires a recorded candidate_mode")

    @property
    def degraded(self) -> bool:
        return self.degradation_reason is not None

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "ArchitectureDecision":
        candidate = document.get("candidate_mode")
        return cls(
            mode=CollaborationMode(str(document["mode"])),
            sync_mode=SyncMode(str(document["sync_mode"])),
            policy_rule=str(document["policy_rule"]),
            triggers=tuple(str(item) for item in document["triggers"]),
            reasons=tuple(str(item) for item in document["reasons"]),
            requested_by=str(document["requested_by"]),
            expected_rounds=int(document["expected_rounds"]),
            candidate_mode=CollaborationMode(str(candidate)) if candidate else None,
            degradation_reason=(
                str(document["degradation_reason"])
                if document.get("degradation_reason")
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "sync_mode": self.sync_mode.value,
            "policy_rule": self.policy_rule,
            "triggers": list(self.triggers),
            "reasons": list(self.reasons),
            "requested_by": self.requested_by,
            "expected_rounds": self.expected_rounds,
            "candidate_mode": (
                self.candidate_mode.value if self.candidate_mode is not None else None
            ),
            "degradation_reason": self.degradation_reason,
        }


@dataclass(frozen=True, slots=True)
class CollaborationDecision:
    """One auditable record joining WHO and HOW for a single desktop run."""

    model_set: ModelSetDecision
    architecture: ArchitectureDecision
    eligible_model_count: int
    task_shape: TaskShape
    signals: CollaborationSignals

    def __post_init__(self) -> None:
        if self.eligible_model_count < 0:
            raise ValueError("eligible_model_count must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": 1,
            "model_set": self.model_set.to_dict(),
            "architecture": self.architecture.to_dict(),
            "eligible_model_count": self.eligible_model_count,
            "task_shape": self.task_shape.to_dict(),
            "signals": self.signals.to_dict(),
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "CollaborationDecision":
        if int(document.get("contract_version", 0)) != 1:
            raise ValueError("unsupported collaboration decision contract version")
        model_set = document["model_set"]
        architecture = document["architecture"]
        assert isinstance(model_set, Mapping) and isinstance(architecture, Mapping)
        shape = document["task_shape"]
        signals = document["signals"]
        assert isinstance(shape, Mapping) and isinstance(signals, Mapping)
        cost = document["model_set"].get("expected_cost_usd")
        return cls(
            model_set=ModelSetDecision(
                policy=str(model_set["policy"]),
                model_refs=tuple(str(item) for item in model_set["model_refs"]),
                rationale=str(model_set["rationale"]),
                confidence=str(model_set["confidence"]),
                expected_cost_usd=finite_cost(cost),
            ),
            architecture=ArchitectureDecision.from_dict(architecture),
            eligible_model_count=int(document["eligible_model_count"]),
            task_shape=TaskShape(
                complexity=ComplexityLevel(str(shape["complexity"])),
                coupled=bool(shape["coupled"]),
                decomposable=bool(shape["decomposable"]),
                verification_heavy=bool(shape["verification_heavy"]),
                novelty_required=bool(shape["novelty_required"]),
            ),
            signals=CollaborationSignals(
                has_critical_objection=bool(signals["has_critical_objection"]),
                parallel_conflict=bool(signals["parallel_conflict"]),
                high_disagreement=bool(signals["high_disagreement"]),
            ),
        )


class DesktopCollaborationRouter:
    """Deterministic, ordered decision table reviewed for C1.1.

    Order matters and each outcome names its rule so every decision can be
    reconstructed later:

    1. Runtime safety (canonical state only) -> JOINT with HARD sync.
    2. Explicit user lock -> the locked architecture.
    3. Verification-heavy or novelty-required tasks -> INDEPENDENT_DISCUSS
       (sealed independence reduces anchoring before discussion).
    4. Decomposable, weakly coupled tasks -> PARALLEL (execution additionally
       requires a decomposition plan; supplied once the planner lands).
    5. High/critical coupled complexity -> JOINT deliberation.
    6. Medium complexity with conflicting decomposition evidence -> ADAPTIVE
       (the kernel AdaptiveRouter owns base-mode choice and transitions).
    7. Otherwise SOLO.

    Feasibility applies after selection: a multi-participant architecture
    chosen while fewer than two models are eligible degrades to SOLO with the
    original candidate and reason recorded.  Participant count never promotes
    a Solo-classified task into a collaborative architecture.
    """

    def decide(
        self,
        shape: TaskShape,
        signals: CollaborationSignals,
        *,
        requested_mode: CollaborationMode | None = None,
        sync_override: SyncMode | None = None,
        eligible_model_count: int,
    ) -> ArchitectureDecision:
        eligible_model_count = int(eligible_model_count)
        if eligible_model_count < 1:
            raise ValueError("at least one eligible model is required")
        triggers: list[str] = [f"complexity:{shape.complexity.value}"]
        if shape.verification_heavy:
            triggers.append("verification_heavy")
        if shape.novelty_required:
            triggers.append("novelty_required")
        if shape.decomposable:
            triggers.append("decomposable")
        if shape.coupled:
            triggers.append("coupled")
        if signals.has_critical_objection:
            triggers.append("critical_objection")
        if signals.parallel_conflict:
            triggers.append("parallel_conflict")
        if signals.high_disagreement:
            triggers.append("high_disagreement")

        if signals.has_critical_objection or signals.parallel_conflict:
            return self._outcome(
                CollaborationMode.JOINT,
                SyncMode.HARD,
                "safety.canonical_conflict_to_joint",
                triggers,
                [
                    "Canonical state reports an unresolved critical conflict; "
                    "Joint deliberation with hard synchronization is mandatory."
                ],
                requested_by=_REQUESTED_BY_AUTO,
                eligible_model_count=eligible_model_count,
                allow_degradation=False,
            )

        if requested_mode is not None:
            reasons = ["The user explicitly locked this architecture."]
            if sync_override is not None:
                reasons.append("The user requested a specific synchronization policy.")
            return self._outcome(
                requested_mode,
                sync_override
                if sync_override is not None
                else ARCHITECTURE_SYNC_DEFAULTS[requested_mode],
                "user.mode_lock",
                triggers,
                reasons,
                requested_by=_REQUESTED_BY_USER_LOCK,
                eligible_model_count=eligible_model_count,
                allow_degradation=False,
            )

        if shape.verification_heavy or shape.novelty_required:
            candidate, rule = (
                CollaborationMode.INDEPENDENT_DISCUSS,
                "task.verification_or_novelty_to_independent",
            )
            reason = (
                "Sealed independent attempts protect against anchoring and "
                "correlated errors before structured discussion."
            )
        elif shape.decomposable and not shape.coupled:
            candidate, rule = (
                CollaborationMode.PARALLEL,
                "task.decomposable_weak_coupling_to_parallel",
            )
            reason = (
                "Independent subproblems dominate; parallel branches integrate "
                "through deterministic evidence gates."
            )
        elif shape.complexity in {ComplexityLevel.HIGH, ComplexityLevel.CRITICAL}:
            candidate, rule = (
                CollaborationMode.JOINT,
                "task.high_complexity_to_joint",
            )
            reason = (
                "Strongly coupled high-complexity work needs shared-focus "
                "deliberation rather than independent attempts."
            )
        elif (
            shape.decomposable
            and shape.coupled
            and shape.complexity is ComplexityLevel.MEDIUM
        ):
            candidate, rule = (
                CollaborationMode.ADAPTIVE_HYBRID,
                "task.ambiguous_decomposition_to_adaptive",
            )
            reason = (
                "Decomposition and coupling evidence conflict; the audited "
                "Adaptive supervisor chooses and switches base modes as task "
                "evidence develops."
            )
        else:
            candidate, rule = (
                CollaborationMode.SOLO,
                "task.routine_single_focus_to_solo",
            )
            reason = "One adequate model handles this focused routine task."

        return self._outcome(
            candidate,
            sync_override
            if sync_override is not None
            else ARCHITECTURE_SYNC_DEFAULTS[candidate],
            rule,
            triggers,
            [reason],
            requested_by=_REQUESTED_BY_AUTO,
            allow_degradation=True,
            eligible_model_count=eligible_model_count,
        )

    @staticmethod
    def _outcome(
        candidate: CollaborationMode,
        sync_mode: SyncMode,
        rule: str,
        triggers: list[str],
        reasons: list[str],
        *,
        requested_by: str,
        eligible_model_count: int,
        allow_degradation: bool,
    ) -> ArchitectureDecision:
        mode = candidate
        degradation_reason: str | None = None
        if (
            allow_degradation
            and candidate in _MULTI_PARTICIPANT_ARCHITECTURES
            and eligible_model_count < 2
        ):
            mode = CollaborationMode.SOLO
            degradation_reason = (
                f"{candidate.value} requires at least two eligible models; "
                f"only {eligible_model_count} satisfied the quality/capability gate."
            )
            reasons.append(degradation_reason)
            reasons.append(
                "Fell back to Solo instead of fabricating additional participants."
            )
        return ArchitectureDecision(
            mode=mode,
            sync_mode=sync_mode,
            policy_rule=rule,
            triggers=tuple(triggers),
            reasons=tuple(reasons),
            requested_by=requested_by,
            expected_rounds=ARCHITECTURE_EXPECTED_ROUNDS[mode],
            candidate_mode=candidate if degradation_reason is not None else None,
            degradation_reason=degradation_reason,
        )


def finite_cost(value: Any) -> float | None:
    """Normalize an untrusted cost estimate to a finite float or None."""

    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


_DOMAIN_UNIT_TEMPLATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "coding",
        ("code", "repository", "git", "debug", "test ", "software", "script"),
    ),
    (
        "data analysis",
        ("data", "spreadsheet", "csv", "statistic", "analy"),
    ),
    (
        "research",
        ("research", "literature", "source", "citation", "evidence"),
    ),
    (
        "document drafting",
        ("document", "report", "manuscript", "presentation", "pdf"),
    ),
)

_SEQUENCING_SPLIT = re.compile(r",?\s+then\b\s*|;\s*|\bstep\s+\d+:?\s*", re.I)
_NUMBERED_SPLIT = re.compile(r"(?:^|\s)\d+\.\s+")


class DeterministicDecompositionPlanner:
    """Deterministic lexical planner for desktop Parallel Decomposition (C1.6).

    The planner never calls a model and never invents scope: it slices the
    user's own goal text into bounded work units, assigns each unit one model,
    and appends a mandatory integration unit that depends on every execution
    unit.  Splitting is deliberately conservative - explicit sequencing cues
    first ("then", semicolons, numbered steps), then distinct domain keyword
    groups, and otherwise a single execution unit followed by the integration
    review.  The kernel remains the authority: it validates assignees,
    dependency order, output contracts, and the deterministic integration
    report regardless of what this planner produced.
    """

    MAX_WORK_UNITS = 6

    def plan(
        self,
        goal: str,
        participant_ids: Sequence[str],
    ) -> tuple[DecompositionPlan, dict[str, object]]:
        participants = tuple(
            dict.fromkeys(str(item).strip() for item in participant_ids)
        )
        if not participants or any(not item for item in participants):
            raise ValueError("decomposition planning requires named participants")
        focuses, basis = self._split_focus(goal)
        if len(focuses) > self.MAX_WORK_UNITS:
            head = focuses[: self.MAX_WORK_UNITS - 1]
            tail = focuses[self.MAX_WORK_UNITS - 1 :]
            focuses = [*head, "Remaining steps from the parent goal: " + " ".join(tail)]
        # 05B Part B: a parallel plan must give every admitted participant a
        # genuinely independent execution branch.  A single-focus goal used to
        # produce one unit, so participants[1:] were never dispatched and the
        # run degraded to a solo run labelled Parallel.  When the split yields
        # fewer units than participants, append independent cross-attempt units
        # (own reasoning, no reuse of other branches' text); the deterministic
        # integration report still gates the merged result.
        if len(focuses) < len(participants):
            basis = basis + "+independent-attempts"
            for extra in range(len(participants) - len(focuses)):
                focuses = (
                    *focuses,
                    (
                        "Independently attempt the parent goal from your own "
                        "reasoning without reusing any other branch's text; "
                        f"cross-attempt {extra + 1}."
                    ),
                )
        execution_units = tuple(
            WorkUnit(
                id=f"unit-{index + 1}",
                focus=focus,
                inputs={"parent_goal": goal},
                output_contract=WorkUnitOutputContract(),
                dependencies=(),
                assignee_ids=(participants[index % len(participants)],),
                integration_owner=participants[0],
            )
            for index, focus in enumerate(focuses)
        )
        integration_unit = WorkUnit(
            id="integration",
            focus=(
                "Integrate every completed work unit into one verified answer "
                "for the parent goal; state any unresolved conflicts explicitly "
                "instead of hiding them."
            ),
            inputs={"parent_goal": goal},
            # 05B Part C: the integration gate no longer trusts the model's
            # self-reported focus_resolved flag — small local models virtually
            # never assert it, which silently degraded every Parallel/Hybrid run
            # to 'partial'.  Completion is gated deterministically instead: a
            # non-empty integration summary, all dependency units completed, and
            # the deterministic integration report.  The integration prompt
            # still requires stating unresolved conflicts explicitly.
            output_contract=WorkUnitOutputContract(),
            dependencies=tuple(unit.id for unit in execution_units),
            assignee_ids=(participants[0],),
            integration_owner=participants[0],
        )
        plan = DecompositionPlan(
            work_units=(*execution_units, integration_unit)
        )
        meta: dict[str, object] = {
            "basis": basis,
            "work_unit_count": len(execution_units),
            "levels": len(plan.topological_levels()),
            "units": [
                {
                    "id": unit.id,
                    "focus": unit.focus,
                    "assignee_ids": list(unit.assignee_ids),
                    "dependencies": list(unit.dependencies),
                }
                for unit in plan.work_units
            ],
        }
        return plan, meta

    @staticmethod
    def _split_focus(goal: str) -> tuple[tuple[str, ...], str]:
        sequenced = [
            segment.strip(" ,.;")
            for segment in _SEQUENCING_SPLIT.split(goal)
            if segment and segment.strip(" ,.;")
        ]
        if len(sequenced) >= 2:
            return tuple(sequenced), "sequencing"
        numbered = [
            segment.strip()
            for segment in _NUMBERED_SPLIT.split(goal)
            if segment and segment.strip()
        ]
        if len(numbered) >= 2:
            return tuple(numbered), "numbered_steps"
        lowered = goal.casefold()
        domains = tuple(
            label
            for label, keywords in _DOMAIN_UNIT_TEMPLATES
            if any(keyword in lowered for keyword in keywords)
        )
        if len(domains) >= 2:
            return (
                tuple(
                    f"Cover the {label} dimension of this task: {goal}"
                    for label in domains
                )
            ), "domains"
        return (
            (
                f"Complete the whole bounded task and verify the result: {goal}",
            ),
            "single",
        )


@dataclass(frozen=True, slots=True)
class CollaborationQualityMetrics:
    participant_count: int
    decomposition_depth: int
    model_diversity: int
    local_execution_ratio: float
    synthesis_complete: bool
    unresolved_conflicts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_count": self.participant_count,
            "decomposition_depth": self.decomposition_depth,
            "model_diversity": self.model_diversity,
            "local_execution_ratio": self.local_execution_ratio,
            "synthesis_complete": self.synthesis_complete,
            "unresolved_conflicts": self.unresolved_conflicts,
        }


@dataclass(frozen=True, slots=True)
class CollaborationSessionRecord:
    session_id: str
    project_id: str
    mode: CollaborationMode
    goal: str
    participants: tuple[str, ...]
    agent_roles: Mapping[str, str]
    metrics: CollaborationQualityMetrics
    decision_triggers: tuple[str, ...] = ()
    decision_reasons: tuple[str, ...] = ()
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "mode": self.mode.value if isinstance(self.mode, CollaborationMode) else str(self.mode),
            "goal": self.goal,
            "participants": list(self.participants),
            "agent_roles": dict(self.agent_roles),
            "metrics": self.metrics.to_dict(),
            "decision_triggers": list(self.decision_triggers),
            "decision_reasons": list(self.decision_reasons),
            "created_at": self.created_at,
        }


def calculate_collaboration_metrics(
    participants: Sequence[str],
    decomposition_steps: int,
    local_count: int,
    total_executions: int,
    synthesis_complete: bool,
    unresolved_conflicts: int = 0,
) -> CollaborationQualityMetrics:
    parts = tuple(dict.fromkeys(str(p).strip() for p in participants if str(p).strip()))
    local_ratio = 1.0 if total_executions <= 0 else round(min(1.0, max(0.0, local_count / total_executions)), 2)
    return CollaborationQualityMetrics(
        participant_count=len(parts),
        decomposition_depth=max(1, int(decomposition_steps)),
        model_diversity=len(set(parts)),
        local_execution_ratio=local_ratio,
        synthesis_complete=bool(synthesis_complete),
        unresolved_conflicts=max(0, int(unresolved_conflicts)),
    )


class CollaborationSessionStore:
    """Project-partitioned SQLite storage for CollaborationSessionRecords."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS collaboration_sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_collab_proj ON collaboration_sessions(project_id);"
            )

    def save_session(self, record: CollaborationSessionRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO collaboration_sessions VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.project_id,
                    record.mode.value if isinstance(record.mode, CollaborationMode) else str(record.mode),
                    record.goal,
                    json.dumps(record.to_dict()),
                    record.created_at or datetime.now(UTC).isoformat(timespec="microseconds"),
                ),
            )

    def list_sessions(self, project_id: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT record_json FROM collaboration_sessions WHERE project_id=? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        return [json.loads(str(r["record_json"])) for r in rows]


__all__ = [
    "ARCHITECTURE_EXPECTED_ROUNDS",
    "ARCHITECTURE_ROUND_DESCRIPTIONS",
    "ARCHITECTURE_SYNC_DEFAULTS",
    "ArchitectureDecision",
    "CollaborationDecision",
    "CollaborationQualityMetrics",
    "CollaborationSessionRecord",
    "CollaborationSessionStore",
    "CollaborationSignals",
    "DESKTOP_EXECUTABLE_ARCHITECTURES",
    "DesktopCollaborationRouter",
    "DeterministicDecompositionPlanner",
    "ModelSetDecision",
    "SUPPORTED_ARCHITECTURES",
    "TaskShape",
    "calculate_collaboration_metrics",
    "finite_cost",
    "parse_requested_mode",
]
