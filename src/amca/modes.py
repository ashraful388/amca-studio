"""Typed records and deterministic validation for AMCA collaboration modes.

The objects in this module intentionally contain reportable conclusions and
digests, never hidden reasoning or provider credentials.  They are independent
of persistence so the kernel can use them for both in-memory and SQLite-backed
runs without adding another source of truth.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from amca.domain import CollaborationMode, SyncMode


_BASE_MODES = frozenset(
    {
        CollaborationMode.SOLO,
        CollaborationMode.JOINT,
        CollaborationMode.PARALLEL,
        CollaborationMode.INDEPENDENT_DISCUSS,
    }
)


def _freeze_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("mode records require finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mode-record object keys must be strings")
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(
        f"mode records require JSON-compatible values, got {type(value).__name__}"
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def canonical_digest(value: Any) -> str:
    """Return a reproducible SHA-256 digest for a JSON-compatible value."""

    encoded = json.dumps(
        _json_ready(_freeze_json(value)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SubmissionStatus(str, Enum):
    SEALED = "sealed"
    REVEALED = "revealed"
    RELEASED = "released"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndependentSubmissionReceipt:
    """Public receipt for a private submission.

    The response text is deliberately absent.  Before the reveal barrier, only
    this receipt may leave the kernel's private call collection.
    """

    participant_id: str
    base_state_version: int
    base_cognitive_revision: int
    content_digest: str | None
    status: SubmissionStatus
    error: str | None = None
    revealed_state_version: int | None = None

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise ValueError("independent submission requires participant_id")
        if self.base_state_version < 0 or self.base_cognitive_revision < 0:
            raise ValueError("submission base versions must be non-negative")
        if self.content_digest is not None and (
            len(self.content_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.content_digest)
        ):
            raise ValueError("content_digest must be a lowercase SHA-256 digest")
        if self.status in {SubmissionStatus.SEALED, SubmissionStatus.REVEALED}:
            if self.content_digest is None:
                raise ValueError("sealed and revealed submissions require a digest")
        if self.status is SubmissionStatus.REVEALED and self.revealed_state_version is None:
            raise ValueError("revealed submissions require revealed_state_version")

    def to_dict(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "base_state_version": self.base_state_version,
            "base_cognitive_revision": self.base_cognitive_revision,
            "content_digest": self.content_digest,
            "status": self.status.value,
            "error": self.error,
            "revealed_state_version": self.revealed_state_version,
        }


@dataclass(frozen=True, slots=True)
class AlternativePoint:
    kind: str
    content: str
    severity: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.kind or not self.content:
            raise ValueError("normalized alternative points require kind and content")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "content": self.content,
            "severity": self.severity,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class NormalizedAlternative:
    participant_id: str
    content_digest: str
    summary: str
    points: tuple[AlternativePoint, ...]
    criterion_ids: tuple[str, ...] = ()
    focus_resolved: bool = False

    def __post_init__(self) -> None:
        if not self.participant_id or not self.summary:
            raise ValueError("normalized alternatives require participant and summary")

    def to_dict(self) -> dict[str, object]:
        return {
            "participant_id": self.participant_id,
            "content_digest": self.content_digest,
            "summary": self.summary,
            "points": [point.to_dict() for point in self.points],
            "criterion_ids": list(self.criterion_ids),
            "focus_resolved": self.focus_resolved,
        }


@dataclass(frozen=True, slots=True)
class IndependentPhaseRecord:
    focus_digest: str
    context_digest: str
    required_participant_ids: tuple[str, ...]
    receipts: tuple[IndependentSubmissionReceipt, ...]
    reveal_state_version: int
    alternatives: tuple[NormalizedAlternative, ...] = ()
    anti_anchoring_reason: str | None = None
    synthesis_mode: CollaborationMode = CollaborationMode.JOINT

    def __post_init__(self) -> None:
        if len(set(self.required_participant_ids)) != len(self.required_participant_ids):
            raise ValueError("required independent participants must be unique")
        if self.synthesis_mode is not CollaborationMode.JOINT:
            raise ValueError("independent submissions must transition to Joint synthesis")
        receipt_ids = {receipt.participant_id for receipt in self.receipts}
        if receipt_ids != set(self.required_participant_ids):
            raise ValueError("every required participant needs an explicit receipt")
        if any(receipt.status is SubmissionStatus.SEALED for receipt in self.receipts):
            raise ValueError("a completed independent phase cannot retain sealed receipts")
        if self.anti_anchoring_reason is not None and not self.anti_anchoring_reason.strip():
            raise ValueError("anti_anchoring_reason cannot be blank")
        receipt_by_id = {receipt.participant_id: receipt for receipt in self.receipts}
        if len({item.participant_id for item in self.alternatives}) != len(
            self.alternatives
        ):
            raise ValueError("normalized independent alternatives must be unique")
        for alternative in self.alternatives:
            receipt = receipt_by_id.get(alternative.participant_id)
            if (
                receipt is None
                or receipt.status is not SubmissionStatus.REVEALED
                or receipt.content_digest != alternative.content_digest
            ):
                raise ValueError(
                    "normalized alternatives must match a revealed submission digest"
                )

    @property
    def revealed_participant_ids(self) -> tuple[str, ...]:
        return tuple(
            receipt.participant_id
            for receipt in self.receipts
            if receipt.status is SubmissionStatus.REVEALED
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "independent_phase",
            "focus_digest": self.focus_digest,
            "context_digest": self.context_digest,
            "required_participant_ids": list(self.required_participant_ids),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "reveal_state_version": self.reveal_state_version,
            "alternatives": [item.to_dict() for item in self.alternatives],
            "anti_anchoring_reason": self.anti_anchoring_reason,
            "synthesis_mode": self.synthesis_mode.value,
        }


@dataclass(frozen=True, slots=True)
class WorkUnitOutputContract:
    require_summary: bool = True
    require_focus_resolved: bool = False
    minimum_events: int = 0
    required_event_types: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.minimum_events < 0:
            raise ValueError("minimum_events must be non-negative")
        if any(not value for value in self.required_event_types):
            raise ValueError("required event types cannot be blank")
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("output artifact IDs must be unique")

    @classmethod
    def from_value(
        cls, value: WorkUnitOutputContract | Mapping[str, object]
    ) -> WorkUnitOutputContract:
        if isinstance(value, cls):
            return value
        return cls(
            require_summary=bool(value.get("require_summary", True)),
            require_focus_resolved=bool(value.get("require_focus_resolved", False)),
            minimum_events=int(value.get("minimum_events", 0)),
            required_event_types=tuple(
                str(item) for item in value.get("required_event_types", ())
            ),
            artifact_ids=tuple(str(item) for item in value.get("artifact_ids", ())),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "require_summary": self.require_summary,
            "require_focus_resolved": self.require_focus_resolved,
            "minimum_events": self.minimum_events,
            "required_event_types": list(self.required_event_types),
            "artifact_ids": list(self.artifact_ids),
        }


@dataclass(frozen=True, slots=True)
class WorkUnit:
    """A bounded DAG node.

    ``assignee_ids`` is ordered: the first participant is the execution owner;
    later participants are declared fallbacks/reviewers for a future retry
    policy.  This static slice never retries silently because that would alter
    cost and latency without a recorded policy decision.
    """

    id: str
    focus: str
    inputs: Mapping[str, object]
    output_contract: WorkUnitOutputContract | Mapping[str, object]
    dependencies: tuple[str, ...]
    assignee_ids: tuple[str, ...]
    integration_owner: str
    required: bool = True
    assumptions: Mapping[str, object] = field(default_factory=dict)
    artifact_owners: Mapping[str, str] = field(default_factory=dict)
    artifact_base_versions: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.focus.strip():
            raise ValueError("work units require non-empty id and focus")
        if not self.assignee_ids or any(not item for item in self.assignee_ids):
            raise ValueError("work units require at least one named assignee")
        if len(set(self.assignee_ids)) != len(self.assignee_ids):
            raise ValueError("work-unit assignees must be unique")
        if not self.integration_owner.strip():
            raise ValueError("work units require an integration owner")
        if self.id in self.dependencies:
            raise ValueError("a work unit cannot depend on itself")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("work-unit dependencies must be unique")
        contract = WorkUnitOutputContract.from_value(self.output_contract)
        object.__setattr__(self, "output_contract", contract)
        object.__setattr__(self, "inputs", _freeze_json(self.inputs))
        object.__setattr__(self, "assumptions", _freeze_json(self.assumptions))
        object.__setattr__(
            self, "artifact_owners", _freeze_json(self.artifact_owners)
        )
        object.__setattr__(
            self,
            "artifact_base_versions",
            _freeze_json(self.artifact_base_versions),
        )
        missing_owners = set(contract.artifact_ids) - set(self.artifact_owners)
        if missing_owners:
            raise ValueError(
                "each output artifact needs an owner: " + ", ".join(sorted(missing_owners))
            )
        if set(self.artifact_base_versions) - set(self.artifact_owners):
            raise ValueError("version-pinned artifacts require an explicit owner")
        if any(
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 0
            for version in self.artifact_base_versions.values()
        ):
            raise ValueError("artifact base versions must be non-negative integers")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "focus": self.focus,
            "inputs": _json_ready(self.inputs),
            "output_contract": self.output_contract.to_dict(),
            "dependencies": list(self.dependencies),
            "assignee_ids": list(self.assignee_ids),
            "integration_owner": self.integration_owner,
            "required": self.required,
            "assumptions": _json_ready(self.assumptions),
            "artifact_owners": _json_ready(self.artifact_owners),
            "artifact_base_versions": _json_ready(self.artifact_base_versions),
        }


@dataclass(frozen=True, slots=True)
class DecompositionPlan:
    work_units: tuple[WorkUnit, ...]
    integration_criteria: tuple[str, ...] = (
        "dependencies_satisfied",
        "output_contracts_pass",
        "assumptions_compatible",
        "artifact_ownership_valid",
    )
    artifact_merge_policies: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.work_units:
            raise ValueError("a decomposition plan needs at least one work unit")
        if not any(unit.required for unit in self.work_units):
            raise ValueError("a decomposition plan needs at least one required work unit")
        identifiers = [unit.id for unit in self.work_units]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("work-unit IDs must be unique")
        known = set(identifiers)
        for unit in self.work_units:
            missing = set(unit.dependencies) - known
            if missing:
                raise ValueError(
                    f"work unit {unit.id!r} has unknown dependencies: "
                    + ", ".join(sorted(missing))
                )
        if not self.integration_criteria or any(
            not criterion.strip() for criterion in self.integration_criteria
        ):
            raise ValueError("integration criteria must be explicit and non-empty")
        object.__setattr__(
            self,
            "artifact_merge_policies",
            _freeze_json(self.artifact_merge_policies),
        )
        # Traversal validates acyclicity.
        self.topological_levels()
        claims: dict[str, set[str]] = {}
        for unit in self.work_units:
            for artifact_id, owner in unit.artifact_owners.items():
                claims.setdefault(artifact_id, set()).add(owner)
        ambiguous = [
            artifact_id
            for artifact_id, owners in claims.items()
            if len(owners) > 1 and not self.artifact_merge_policies.get(artifact_id)
        ]
        if ambiguous:
            raise ValueError(
                "shared artifacts require a merge policy: "
                + ", ".join(sorted(ambiguous))
            )
        versions: dict[str, set[int]] = {}
        for unit in self.work_units:
            for artifact_id, version in unit.artifact_base_versions.items():
                versions.setdefault(artifact_id, set()).add(version)
        inconsistent = [
            artifact_id
            for artifact_id, declared in versions.items()
            if len(declared) > 1
        ]
        if inconsistent:
            raise ValueError(
                "shared artifacts must use one pinned base version: "
                + ", ".join(sorted(inconsistent))
            )

    def unit(self, unit_id: str) -> WorkUnit:
        for unit in self.work_units:
            if unit.id == unit_id:
                return unit
        raise KeyError(unit_id)

    def topological_levels(self) -> tuple[tuple[WorkUnit, ...], ...]:
        remaining = {unit.id: unit for unit in self.work_units}
        completed: set[str] = set()
        levels: list[tuple[WorkUnit, ...]] = []
        while remaining:
            ready = tuple(
                sorted(
                    (
                        unit
                        for unit in remaining.values()
                        if set(unit.dependencies) <= completed
                    ),
                    key=lambda unit: unit.id,
                )
            )
            if not ready:
                raise ValueError("work-unit dependency graph contains a cycle")
            levels.append(ready)
            for unit in ready:
                remaining.pop(unit.id)
                completed.add(unit.id)
        return tuple(levels)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "work_units": [unit.to_dict() for unit in self.work_units],
            "integration_criteria": list(self.integration_criteria),
            "artifact_merge_policies": _json_ready(self.artifact_merge_policies),
        }


class WorkUnitStatus(str, Enum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WorkUnitResult:
    work_unit_id: str
    participant_id: str
    status: WorkUnitStatus
    base_state_version: int
    base_cognitive_revision: int
    content_digest: str | None = None
    summary: str = ""
    event_types: tuple[str, ...] = ()
    focus_resolved: bool = False
    artifact_ids: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "work_unit_id": self.work_unit_id,
            "participant_id": self.participant_id,
            "status": self.status.value,
            "base_state_version": self.base_state_version,
            "base_cognitive_revision": self.base_cognitive_revision,
            "content_digest": self.content_digest,
            "summary": self.summary,
            "event_types": list(self.event_types),
            "focus_resolved": self.focus_resolved,
            "artifact_ids": list(self.artifact_ids),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class IntegrationReport:
    passed: bool
    criterion_results: Mapping[str, bool]
    conflicts: tuple[str, ...] = ()
    rationale: str = ""
    evaluator: str = "deterministic_builtin"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "criterion_results", MappingProxyType(dict(self.criterion_results))
        )
        if self.passed and (
            self.conflicts or not all(self.criterion_results.values())
        ):
            raise ValueError("a passing integration report cannot contain failed criteria")

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "criterion_results": dict(self.criterion_results),
            "conflicts": list(self.conflicts),
            "rationale": self.rationale,
            "evaluator": self.evaluator,
        }


def deterministic_integration_report(
    plan: DecompositionPlan, results: Sequence[WorkUnitResult]
) -> IntegrationReport:
    """Evaluate the minimum integration gate without trusting model votes."""

    by_unit = {result.work_unit_id: result for result in results}
    completed = {
        unit_id
        for unit_id, result in by_unit.items()
        if result.status is WorkUnitStatus.COMPLETED
    }
    conflicts: list[str] = []
    required_units = {unit.id for unit in plan.work_units if unit.required}
    dependencies_ok = all(
        set(unit.dependencies) <= completed
        for unit in plan.work_units
        if unit.id in completed
    )
    contracts_ok = True
    for unit in plan.work_units:
        result = by_unit.get(unit.id)
        if result is None or result.status is not WorkUnitStatus.COMPLETED:
            if unit.required:
                contracts_ok = False
                conflicts.append(f"required work unit {unit.id!r} did not complete")
            continue
        if result.status is not WorkUnitStatus.COMPLETED:
            continue
        contract = unit.output_contract
        valid = (
            (not contract.require_summary or bool(result.summary.strip()))
            and (not contract.require_focus_resolved or result.focus_resolved)
            and len(result.event_types) >= contract.minimum_events
            and set(contract.required_event_types) <= set(result.event_types)
            and set(contract.artifact_ids) <= set(result.artifact_ids)
        )
        if not valid:
            contracts_ok = False
            conflicts.append(f"work unit {unit.id!r} failed its output contract")

    assumptions_by_key: dict[str, dict[str, str]] = {}
    for unit in plan.work_units:
        if unit.id not in completed:
            continue
        for key, value in unit.assumptions.items():
            assumptions_by_key.setdefault(str(key), {})[unit.id] = canonical_digest(value)
    assumptions_ok = True
    for key, by_unit_digest in assumptions_by_key.items():
        if len(set(by_unit_digest.values())) > 1:
            assumptions_ok = False
            conflicts.append(
                f"declared assumption {key!r} conflicts across "
                + ", ".join(sorted(by_unit_digest))
            )

    ownership_ok = True
    owners: dict[str, set[str]] = {}
    for unit in plan.work_units:
        if unit.id not in completed:
            continue
        for artifact_id, owner in unit.artifact_owners.items():
            owners.setdefault(artifact_id, set()).add(owner)
    for artifact_id, values in owners.items():
        if len(values) > 1 and not plan.artifact_merge_policies.get(artifact_id):
            ownership_ok = False
            conflicts.append(f"artifact {artifact_id!r} has conflicting owners")

    if not required_units <= completed:
        dependencies_ok = False
    semantic_conflict = any(
        "critical_conflict" in result.event_types or "blocker" in result.event_types
        for result in results
        if result.status is WorkUnitStatus.COMPLETED
    )
    if semantic_conflict:
        conflicts.append("parallel results contain a reported blocker or critical conflict")

    builtins = {
        "dependencies_satisfied": dependencies_ok,
        "output_contracts_pass": contracts_ok,
        "assumptions_compatible": assumptions_ok,
        "artifact_ownership_valid": ownership_ok,
    }
    criteria = {
        criterion: builtins.get(criterion, False)
        for criterion in plan.integration_criteria
    }
    if semantic_conflict:
        criteria["semantic_conflicts_absent"] = False
    passed = all(criteria.values()) and not conflicts
    return IntegrationReport(
        passed=passed,
        criterion_results=criteria,
        conflicts=tuple(conflicts),
        rationale=(
            "All required work units and deterministic integration criteria passed."
            if passed
            else "Parallel completion is provisional; integration requires Joint review."
        ),
    )


@dataclass(frozen=True, slots=True)
class AdaptiveFeatures:
    weakly_coupled: bool = False
    novelty_required: bool = False
    stagnating: bool = False
    high_disagreement: bool = False
    parallel_conflict: bool = False
    routine_residual: bool = False
    agreement_established: bool = False
    no_active_disagreement: bool = False
    ambiguous_mixed_signals: bool = False
    cost_pressure: bool = False
    time_pressure: bool = False
    estimated_cost_usd: float = 0.0
    estimated_latency_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not all(
            math.isfinite(value) and value >= 0
            for value in (
                self.estimated_cost_usd,
                self.estimated_latency_seconds,
            )
        ):
            raise ValueError("adaptive cost and latency estimates must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "weakly_coupled": self.weakly_coupled,
            "novelty_required": self.novelty_required,
            "stagnating": self.stagnating,
            "high_disagreement": self.high_disagreement,
            "parallel_conflict": self.parallel_conflict,
            "routine_residual": self.routine_residual,
            "agreement_established": self.agreement_established,
            "no_active_disagreement": self.no_active_disagreement,
            "ambiguous_mixed_signals": self.ambiguous_mixed_signals,
            "cost_pressure": self.cost_pressure,
            "time_pressure": self.time_pressure,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_latency_seconds": self.estimated_latency_seconds,
        }


@dataclass(frozen=True, slots=True)
class AdaptiveTransitionRecord:
    from_mode: CollaborationMode
    selected_mode: CollaborationMode
    selected_sync_mode: SyncMode
    observed_triggers: tuple[str, ...]
    alternative_modes: tuple[CollaborationMode, ...]
    policy_rule: str
    state_version: int
    feature_digest: str
    phase_index: int = 0
    state_version_before: int = 0
    state_version_after: int | None = None
    phase_stop_reason: str | None = None
    timestamp: str = ""
    estimated_cost_usd: float = 0.0
    estimated_latency_seconds: float = 0.0
    user_lock: CollaborationMode | None = None
    manual_instruction: CollaborationMode | None = None
    safety_override: bool = False

    def __post_init__(self) -> None:
        if self.selected_mode not in _BASE_MODES:
            raise ValueError("Adaptive Hybrid must select exactly one base mode")
        if self.user_lock is not None and self.user_lock not in _BASE_MODES:
            raise ValueError("user mode locks must identify a base mode")
        if self.manual_instruction is not None and self.manual_instruction not in _BASE_MODES:
            raise ValueError("manual transitions must identify a base mode")
        if self.state_version < 0 or self.state_version_before < 0:
            raise ValueError("transition state version must be non-negative")
        if self.phase_index < 0:
            raise ValueError("phase_index must be non-negative")
        if self.state_version_before == 0 and self.state_version > 0:
            object.__setattr__(self, "state_version_before", self.state_version)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "adaptive_transition",
            "phase_index": self.phase_index,
            "from_mode": self.from_mode.value,
            "selected_mode": self.selected_mode.value,
            "selected_sync_mode": self.selected_sync_mode.value,
            "observed_triggers": list(self.observed_triggers),
            "alternative_modes": [mode.value for mode in self.alternative_modes],
            "policy_rule": self.policy_rule,
            "state_version": self.state_version,
            "state_version_before": self.state_version_before,
            "state_version_after": self.state_version_after,
            "phase_stop_reason": self.phase_stop_reason,
            "timestamp": self.timestamp,
            "feature_digest": self.feature_digest,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_latency_seconds": self.estimated_latency_seconds,
            "user_lock": self.user_lock.value if self.user_lock else None,
            "manual_instruction": (
                self.manual_instruction.value if self.manual_instruction else None
            ),
            "safety_override": self.safety_override,
        }


__all__ = [
    "AdaptiveFeatures",
    "AdaptiveTransitionRecord",
    "AlternativePoint",
    "DecompositionPlan",
    "IndependentPhaseRecord",
    "IndependentSubmissionReceipt",
    "IntegrationReport",
    "NormalizedAlternative",
    "SubmissionStatus",
    "WorkUnit",
    "WorkUnitOutputContract",
    "WorkUnitResult",
    "WorkUnitStatus",
    "canonical_digest",
    "deterministic_integration_report",
]
