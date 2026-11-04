"""Validated local project definitions and durable lifecycle transitions.

This module is intentionally independent of the desktop/service layers.  It
provides a small API that the CLI and future front ends can share without
silently replacing an existing project or weakening its goal contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import re
from typing import Any

from .domain import (
    CognitiveEvent,
    ComputePolicy,
    CriterionStatus,
    DecisionClass,
    EvaluatorKind,
    EventType,
    GoalContract,
    GoalCriterion,
    PrivacyMode,
    ProjectState,
)
from .security import assert_no_sensitive_keys
from .recovery import (
    ProjectNotFoundError as RecoveryProjectNotFoundError,
    RecoveryReport,
    recover_project,
)
from .state import StateManager
from .storage import SQLiteStore


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}"
    for prefix in ("COM", "LPT")
    for number in range(1, 10)
}
_TERMINAL_STATUSES = frozenset(
    {"complete", "succeeded", "failed", "stopped", "budget_exhausted"}
)
_STARTABLE_STATUSES = frozenset({"created", "degraded", "waiting_for_approval"})
_STOPPABLE_STATUSES = frozenset(
    {"created", "active", "running", "degraded", "blocked", "waiting_for_approval"}
)
_DEFINITION_FIELDS = frozenset(
    {
        "project_id",
        "id",
        "goal",
        "objective",
        "criteria",
        "success_criteria",
        "constraints",
        "user_constraints",
        "budget",
        "max_iterations",
        "max_runtime_seconds",
        "max_api_cost_usd",
        "privacy_mode",
        "compute_policy",
    }
)
_CRITERION_FIELDS = frozenset(
    {
        "id",
        "criterion_id",
        "description",
        "required",
        "evaluator_kind",
        "expected_value",
        "tolerance",
        "evidence_requirement",
        "decision_class",
    }
)
_BUDGET_FIELDS = frozenset(
    {
        "max_iterations",
        "iteration_cap",
        "max_runtime_seconds",
        "wall_clock_seconds",
        "max_api_cost_usd",
        "api_cost_usd",
    }
)


class ProjectLifecycleError(ValueError):
    """Base error for rejected project lifecycle operations."""


class ProjectAlreadyExistsError(ProjectLifecycleError):
    """Raised when creation would replace a persisted project."""


class ProjectNotFoundError(ProjectLifecycleError):
    """Raised when a lifecycle target has no persisted snapshot."""


def validate_project_id(value: str) -> str:
    """Return a stable identifier that is safe as a Windows path component."""

    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(
            "project id must be 1-128 ASCII letters, digits, dots, underscores, "
            "or hyphens; it cannot contain path separators"
        )
    windows_stem = value.split(".", 1)[0].upper()
    if value.endswith(".") or windows_stem in _WINDOWS_RESERVED:
        raise ValueError("project id is not a valid Windows directory name")
    return value


def _bounded_text(name: str, value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be blank")
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} characters")
    return cleaned


def _reject_unknown(
    value: Mapping[str, Any], allowed: frozenset[str], *, path: str
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ValueError(f"{path} contains unsupported fields: {', '.join(unknown)}")


def _optional_budget(
    name: str,
    value: object,
    *,
    integer: bool = False,
    allow_zero: bool = False,
) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        converted: int | float = int(value) if integer else float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if integer and converted != value and not (
        isinstance(value, str) and value.strip() == str(converted)
    ):
        raise ValueError(f"{name} must be an integer")
    if not math.isfinite(float(converted)):
        raise ValueError(f"{name} must be finite")
    minimum_ok = converted >= 0 if allow_zero else converted > 0
    if not minimum_ok:
        qualifier = "non-negative" if allow_zero else "greater than zero"
        raise ValueError(f"{name} must be {qualifier}")
    return converted


def _mapping_alias(
    value: Mapping[str, Any], names: Sequence[str], *, path: str
) -> Any:
    present = [(name, value[name]) for name in names if name in value]
    if not present:
        return None
    first_name, first_value = present[0]
    for name, candidate in present[1:]:
        if candidate != first_value:
            raise ValueError(
                f"{path}.{first_name} conflicts with alias {path}.{name}"
            )
    return first_value


def criterion_from_mapping(
    value: Mapping[str, Any], *, index: int = 0
) -> GoalCriterion:
    """Parse one fresh criterion, rejecting result/evidence fields."""

    if not isinstance(value, Mapping):
        raise ValueError(f"criteria[{index}] must be an object")
    assert_no_sensitive_keys(value, path=f"criteria[{index}]")
    _reject_unknown(value, _CRITERION_FIELDS, path=f"criteria[{index}]")
    description = _bounded_text(
        f"criteria[{index}].description",
        value.get("description"),
        maximum=4_000,
    )
    criterion_id = _mapping_alias(
        value, ("id", "criterion_id"), path=f"criteria[{index}]"
    )
    if criterion_id is not None:
        criterion_id = validate_project_id(
            _bounded_text(
                f"criteria[{index}].id", criterion_id, maximum=128
            )
        )
    required = value.get("required", True)
    if not isinstance(required, bool):
        raise ValueError(f"criteria[{index}].required must be true or false")
    evidence_requirement = value.get("evidence_requirement", 1)
    if (
        not isinstance(evidence_requirement, int)
        or isinstance(evidence_requirement, bool)
        or evidence_requirement < 1
        or evidence_requirement > 100
    ):
        raise ValueError(
            f"criteria[{index}].evidence_requirement must be an integer from 1 to 100"
        )
    try:
        evaluator_kind = EvaluatorKind(
            value.get("evaluator_kind", EvaluatorKind.CUSTOM.value)
        )
    except ValueError as exc:
        choices = ", ".join(item.value for item in EvaluatorKind)
        raise ValueError(
            f"criteria[{index}].evaluator_kind must be one of: {choices}"
        ) from exc
    try:
        decision_class = DecisionClass(
            value.get("decision_class", DecisionClass.SIGNIFICANT.value)
        )
    except ValueError as exc:
        choices = ", ".join(item.value for item in DecisionClass)
        raise ValueError(
            f"criteria[{index}].decision_class must be one of: {choices}"
        ) from exc
    expected_value = value.get("expected_value")
    # Round-trip once to enforce JSON compatibility and finite floats before
    # the contract reaches a ProjectState snapshot.
    try:
        json.dumps(expected_value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"criteria[{index}].expected_value must be finite JSON data"
        ) from exc
    tolerance = value.get("tolerance")
    if tolerance is not None:
        tolerance = _optional_budget(
            f"criteria[{index}].tolerance", tolerance, allow_zero=True
        )
    if evaluator_kind is EvaluatorKind.NUMERIC_TOLERANCE:
        if isinstance(expected_value, bool) or not isinstance(
            expected_value, (int, float)
        ):
            raise ValueError(
                f"criteria[{index}] numeric_tolerance requires a numeric expected_value"
            )
        if tolerance is None:
            raise ValueError(
                f"criteria[{index}] numeric_tolerance requires tolerance"
            )
    if evaluator_kind is EvaluatorKind.EXACT_VALUE and "expected_value" not in value:
        raise ValueError(
            f"criteria[{index}] exact_value requires expected_value"
        )
    keyword = {"id": criterion_id} if criterion_id is not None else {}
    return GoalCriterion(
        description=description,
        required=required,
        evaluator_kind=evaluator_kind,
        expected_value=expected_value,
        tolerance=float(tolerance) if tolerance is not None else None,
        evidence_requirement=evidence_requirement,
        decision_class=decision_class,
        **keyword,
    )


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    """A fresh project's validated identity, contract, policy, and constraints."""

    project_id: str
    goal_contract: GoalContract
    constraints: tuple[str, ...] = ()
    privacy_mode: PrivacyMode = PrivacyMode.OFFLINE
    compute_policy: ComputePolicy = ComputePolicy.BALANCED

    def __post_init__(self) -> None:
        project_id = validate_project_id(self.project_id)
        if not isinstance(self.goal_contract, GoalContract):
            raise TypeError("goal_contract must be a GoalContract")
        contract = GoalContract.from_dict(self.goal_contract.to_dict())
        _bounded_text("goal", contract.goal, maximum=20_000)
        if not 1 <= len(contract.criteria) <= 100:
            raise ValueError("criteria must contain from 1 to 100 entries")
        if not any(item.required for item in contract.criteria):
            raise ValueError("at least one criterion must be required")
        if len({item.id for item in contract.criteria}) != len(contract.criteria):
            raise ValueError("criterion ids must be unique")
        validate_project_id(contract.id)
        canonical_criteria: list[GoalCriterion] = []
        for index, criterion in enumerate(contract.criteria):
            if (
                criterion.satisfied
                or criterion.status is not CriterionStatus.NOT_RUN
                or criterion.evidence
                or criterion.evidence_ids
                or criterion.decision_id is not None
                or criterion.satisfied_at is not None
                or criterion.waiver_authority is not None
            ):
                raise ValueError(
                    f"criteria[{index}] must not contain runtime result fields"
                )
            canonical_criteria.append(
                criterion_from_mapping(
                    {
                        "id": criterion.id,
                        "description": criterion.description,
                        "required": criterion.required,
                        "evaluator_kind": criterion.evaluator_kind.value,
                        "expected_value": criterion.expected_value,
                        "tolerance": criterion.tolerance,
                        "evidence_requirement": criterion.evidence_requirement,
                        "decision_class": criterion.decision_class.value,
                    },
                    index=index,
                )
            )
        _optional_budget(
            "max_iterations", contract.max_iterations, integer=True
        )
        _optional_budget("max_runtime_seconds", contract.max_runtime_seconds)
        _optional_budget(
            "max_api_cost_usd", contract.max_api_cost_usd, allow_zero=True
        )
        assert_no_sensitive_keys(contract.to_dict(), path="goal_contract")
        if len(self.constraints) > 100:
            raise ValueError("constraints must contain at most 100 entries")
        constraints = tuple(
            dict.fromkeys(
                _bounded_text(f"constraints[{index}]", item, maximum=2_000)
                for index, item in enumerate(self.constraints)
            )
        )
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(
            self,
            "goal_contract",
            GoalContract(
                id=contract.id,
                goal=contract.goal,
                criteria=canonical_criteria,
                max_iterations=contract.max_iterations,
                max_runtime_seconds=contract.max_runtime_seconds,
                max_api_cost_usd=contract.max_api_cost_usd,
                created_at=contract.created_at,
            ),
        )
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "privacy_mode", PrivacyMode(self.privacy_mode))
        object.__setattr__(self, "compute_policy", ComputePolicy(self.compute_policy))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        default_project_id: str | None = None,
        default_privacy_mode: PrivacyMode | str = PrivacyMode.OFFLINE,
        default_compute_policy: ComputePolicy | str = ComputePolicy.BALANCED,
    ) -> ProjectDefinition:
        """Parse the supported fresh-contract JSON representation."""

        if not isinstance(value, Mapping):
            raise ValueError("project definition must be a JSON object")
        assert_no_sensitive_keys(value, path="project_definition")
        _reject_unknown(value, _DEFINITION_FIELDS, path="project definition")

        supplied_project_id = value.get("project_id", default_project_id)
        if supplied_project_id is None:
            raise ValueError("project_id is required")
        project_id = validate_project_id(str(supplied_project_id))
        if (
            default_project_id is not None
            and "project_id" in value
            and project_id != validate_project_id(default_project_id)
        ):
            raise ValueError("command project_id conflicts with the contract file")

        goal = _mapping_alias(value, ("goal", "objective"), path="project_definition")
        goal = _bounded_text("goal", goal, maximum=20_000)
        criteria_value = _mapping_alias(
            value,
            ("criteria", "success_criteria"),
            path="project_definition",
        )
        if not isinstance(criteria_value, list):
            raise ValueError("criteria must be a JSON array")
        if not 1 <= len(criteria_value) <= 100:
            raise ValueError("criteria must contain from 1 to 100 entries")
        criteria = [
            criterion_from_mapping(item, index=index)
            for index, item in enumerate(criteria_value)
        ]
        criterion_ids = [item.id for item in criteria]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("criterion ids must be unique")
        if not any(item.required for item in criteria):
            raise ValueError("at least one criterion must be required")

        constraints_value = _mapping_alias(
            value,
            ("constraints", "user_constraints"),
            path="project_definition",
        )
        if constraints_value is None:
            constraints_value = []
        if not isinstance(constraints_value, list):
            raise ValueError("constraints must be a JSON array")
        if len(constraints_value) > 100:
            raise ValueError("constraints must contain at most 100 entries")
        constraints = tuple(
            dict.fromkeys(
                _bounded_text(f"constraints[{index}]", item, maximum=2_000)
                for index, item in enumerate(constraints_value)
            )
        )

        budget = value.get("budget", {})
        if budget is None:
            budget = {}
        if not isinstance(budget, Mapping):
            raise ValueError("budget must be a JSON object")
        _reject_unknown(budget, _BUDGET_FIELDS, path="budget")

        def budget_value(name: str, aliases: Sequence[str]) -> Any:
            nested = _mapping_alias(budget, aliases, path="budget")
            direct = value.get(name)
            if nested is not None and direct is not None and nested != direct:
                raise ValueError(f"{name} conflicts with budget.{aliases[0]}")
            return direct if direct is not None else nested

        max_iterations = _optional_budget(
            "max_iterations",
            budget_value("max_iterations", ("max_iterations", "iteration_cap")),
            integer=True,
        )
        max_runtime_seconds = _optional_budget(
            "max_runtime_seconds",
            budget_value(
                "max_runtime_seconds",
                ("max_runtime_seconds", "wall_clock_seconds"),
            ),
        )
        max_api_cost_usd = _optional_budget(
            "max_api_cost_usd",
            budget_value(
                "max_api_cost_usd", ("max_api_cost_usd", "api_cost_usd")
            ),
            allow_zero=True,
        )

        contract_id = value.get("id")
        contract_keyword: dict[str, str] = {}
        if contract_id is not None:
            contract_keyword["id"] = validate_project_id(
                _bounded_text("contract id", contract_id, maximum=128)
            )
        contract = GoalContract(
            goal=goal,
            criteria=criteria,
            max_iterations=(
                int(max_iterations) if max_iterations is not None else None
            ),
            max_runtime_seconds=(
                float(max_runtime_seconds)
                if max_runtime_seconds is not None
                else None
            ),
            max_api_cost_usd=(
                float(max_api_cost_usd) if max_api_cost_usd is not None else None
            ),
            **contract_keyword,
        )
        try:
            privacy_mode = PrivacyMode(
                value.get("privacy_mode", default_privacy_mode)
            )
            compute_policy = ComputePolicy(
                value.get("compute_policy", default_compute_policy)
            )
        except ValueError as exc:
            raise ValueError(f"invalid project privacy or compute policy: {exc}") from exc
        return cls(
            project_id=project_id,
            goal_contract=contract,
            constraints=constraints,
            privacy_mode=privacy_mode,
            compute_policy=compute_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "goal_contract": self.goal_contract.to_dict(),
            "constraints": list(self.constraints),
            "privacy_mode": self.privacy_mode.value,
            "compute_policy": self.compute_policy.value,
        }


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    """Small project-list record; no transcript or artifact content is exposed."""

    project_id: str
    status: str
    goal: str
    version: int
    progress: float
    required_criteria: int
    satisfied_required_criteria: int
    privacy_mode: str
    compute_policy: str
    updated_at: str
    budget: Mapping[str, int | float | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "goal": self.goal,
            "version": self.version,
            "progress": self.progress,
            "required_criteria": self.required_criteria,
            "satisfied_required_criteria": self.satisfied_required_criteria,
            "privacy_mode": self.privacy_mode,
            "compute_policy": self.compute_policy,
            "updated_at": self.updated_at,
            "budget": dict(self.budget),
        }

    @classmethod
    def from_state(cls, state: ProjectState) -> ProjectSummary:
        contract = state.goal_contract
        required = (
            [item for item in contract.criteria if item.required]
            if contract is not None
            else []
        )
        return cls(
            project_id=state.project_id,
            status=state.status,
            goal=state.goal,
            version=state.version,
            progress=state.progress,
            required_criteria=len(required),
            satisfied_required_criteria=sum(item.satisfied for item in required),
            privacy_mode=state.privacy_mode.value,
            compute_policy=state.compute_policy.value,
            updated_at=state.updated_at,
            budget={
                "max_iterations": contract.max_iterations if contract else None,
                "max_runtime_seconds": (
                    contract.max_runtime_seconds if contract else None
                ),
                "max_api_cost_usd": contract.max_api_cost_usd if contract else None,
            },
        )


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """Observable result of an idempotent lifecycle request."""

    project_id: str
    action: str
    from_status: str
    to_status: str
    changed: bool
    state_version: int
    reason: str
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "action": self.action,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "changed": self.changed,
            "state_version": self.state_version,
            "reason": self.reason,
            "event_id": self.event_id,
        }


def create_project(store: SQLiteStore, definition: ProjectDefinition) -> ProjectState:
    """Persist a version-zero project without overwriting an existing one."""

    if not isinstance(definition, ProjectDefinition):
        raise TypeError("definition must be a ProjectDefinition")
    if store.load_state(definition.project_id) is not None:
        raise ProjectAlreadyExistsError(
            f"project {definition.project_id!r} already exists"
        )
    state = ProjectState(
        project_id=definition.project_id,
        goal=definition.goal_contract.goal,
        goal_contract=GoalContract.from_dict(definition.goal_contract.to_dict()),
        current_focus=definition.goal_contract.goal,
        active_subproblem=definition.goal_contract.goal,
        user_constraints=list(definition.constraints),
        privacy_mode=definition.privacy_mode,
        compute_policy=definition.compute_policy,
        status="created",
    )
    store.save_state(state)
    return state


def list_projects(
    store: SQLiteStore, *, status: str | None = None
) -> list[ProjectSummary]:
    """Return safe summaries in durable most-recently-updated order."""

    status_filter = status.casefold() if status is not None else None
    summaries: list[ProjectSummary] = []
    for project_id in store.list_projects():
        state = store.load_state(project_id)
        if state is None:  # Defensive against an externally modified database.
            continue
        if status_filter is not None and state.status.casefold() != status_filter:
            continue
        summaries.append(ProjectSummary.from_state(state))
    return summaries


def _transition(
    store: SQLiteStore,
    project_id: str,
    *,
    action: str,
    target_status: str,
    allowed_from: frozenset[str],
    reason: str,
) -> LifecycleTransition:
    project_id = validate_project_id(project_id)
    reason = _bounded_text("reason", reason, maximum=2_000)
    state = store.load_state(project_id)
    if state is None:
        raise ProjectNotFoundError(f"project {project_id!r} was not found")
    prior = state.status.casefold()
    if prior == target_status:
        return LifecycleTransition(
            project_id=project_id,
            action=action,
            from_status=state.status,
            to_status=state.status,
            changed=False,
            state_version=state.version,
            reason=reason,
        )
    if prior in _TERMINAL_STATUSES or prior not in allowed_from:
        raise ProjectLifecycleError(
            f"project {project_id!r} cannot {action} from status {state.status!r}"
        )
    event = CognitiveEvent(
        event_type=EventType.STATE_UPDATE,
        source="user",
        state_version=state.version,
        payload={
            "set": {"status": target_status},
            "lifecycle": {
                "action": action,
                "from_status": state.status,
                "to_status": target_status,
                "reason": reason,
            },
        },
    )
    updated = StateManager(state, store=store, strict_versions=True).apply(event)
    return LifecycleTransition(
        project_id=project_id,
        action=action,
        from_status=state.status,
        to_status=updated.status,
        changed=True,
        state_version=updated.version,
        reason=reason,
        event_id=event.id,
    )


def start_project(
    store: SQLiteStore,
    project_id: str,
    *,
    reason: str = "Project execution started by the user.",
) -> LifecycleTransition:
    """Transition a freshly created project to the kernel's active status."""

    return _transition(
        store,
        project_id,
        action="start",
        target_status="active",
        allowed_from=_STARTABLE_STATUSES,
        reason=reason,
    )


def stop_project(
    store: SQLiteStore,
    project_id: str,
    *,
    reason: str = "Stopped by the user.",
) -> LifecycleTransition:
    """Durably record a user stop; repeating the request is idempotent."""

    return _transition(
        store,
        project_id,
        action="stop",
        target_status="stopped",
        allowed_from=_STOPPABLE_STATUSES,
        reason=reason,
    )


def wait_for_approval_project(
    store: SQLiteStore,
    project_id: str,
    *,
    reason: str = "Project is waiting for an exact-scope user approval.",
) -> LifecycleTransition:
    """Durably pause provider execution at an approval boundary."""

    return _transition(
        store,
        project_id,
        action="wait_for_approval",
        target_status="waiting_for_approval",
        allowed_from=frozenset({"created", "active", "degraded"}),
        reason=reason,
    )


def resume_project(
    store: SQLiteStore,
    project_id: str,
    *,
    dry_run: bool = False,
) -> RecoveryReport:
    """Replay only already-durable pending events for one validated project."""

    project_id = validate_project_id(project_id)
    try:
        return recover_project(store, project_id, dry_run=dry_run)
    except RecoveryProjectNotFoundError as exc:
        raise ProjectNotFoundError(f"project {project_id!r} was not found") from exc


__all__ = [
    "LifecycleTransition",
    "ProjectAlreadyExistsError",
    "ProjectDefinition",
    "ProjectLifecycleError",
    "ProjectNotFoundError",
    "ProjectSummary",
    "create_project",
    "criterion_from_mapping",
    "list_projects",
    "resume_project",
    "start_project",
    "stop_project",
    "validate_project_id",
    "wait_for_approval_project",
]
