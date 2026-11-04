"""M0 versioned contract envelopes for the v0.5.0 intelligence boundaries.

Additive module introduced by the M0 Contract Foundation milestone. It defines
implementation-level data records for the planner, agent, model-provider, and
evidence boundaries described in docs/AMCA-v0.5.0-Intelligence-Contracts.md.

These records are inert data only: they confer no execution, permission,
budget, or state-mutation authority, and no pre-existing application code
imports this package. Envelope rules follow spec section 1.1 — every record
carries ``contract_name``/``contract_version``, stable record and correlation
identifiers, a UTC creation time, a producing-component identity, a sensitivity
classification, and a content digest over execution-significant fields.
Unknown enum values, non-finite numbers, unsupported major versions, and
credential material fail closed.
"""

import dataclasses
import enum
from typing import Any, ClassVar, Mapping, Optional, Tuple, Union, get_args, get_origin, get_type_hints

from .canonical import (
    content_digest,
    parse_utc_timestamp,
    require_supported_version,
)
from .errors import ContractError, ContractErrorCode, reject_secret_material

# ---------------------------------------------------------------------------
# Shared envelope metadata
# ---------------------------------------------------------------------------


class Sensitivity(enum.Enum):
    """Privacy classification carried by every contract record."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


@dataclasses.dataclass(frozen=True)
class ContractMetadata:
    """Envelope metadata shared by every contract record (spec 1.1)."""

    record_id: str
    correlation_id: str
    producer: str
    created_at: str
    sensitivity: Sensitivity

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "correlation_id": self.correlation_id,
            "producer": self.producer,
            "created_at": self.created_at,
            "sensitivity": self.sensitivity.value,
        }


def _enum_value(enum_cls, value, field):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"unknown {enum_cls.__name__} value for {field}: {value!r}",
        ) from None


def _coerce_value(hint, value, field):
    """Coerce a plain JSON value into the annotated field type."""

    if hint is Any or hint is None:
        return value
    origin = get_origin(hint)
    if origin is None:
        if isinstance(hint, type) and issubclass(hint, enum.Enum):
            return _enum_value(hint, value, field)
        if isinstance(hint, type) and issubclass(hint, Contract):
            return hint.from_dict(value)
        if hint is ContractMetadata:
            if not isinstance(value, Mapping):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field} must be an object"
                )
            return ContractMetadata(
                record_id=_require_str(value.get("record_id"), f"{field}.record_id"),
                correlation_id=_require_str(
                    value.get("correlation_id"), f"{field}.correlation_id"
                ),
                producer=_require_str(value.get("producer"), f"{field}.producer"),
                created_at=_require_str(value.get("created_at"), f"{field}.created_at"),
                sensitivity=_enum_value(
                    Sensitivity, value.get("sensitivity"), f"{field}.sensitivity"
                ),
            )
        if hint is str:
            return _require_str(value, field)
        if hint is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field} must be a number"
                )
            numeric = float(value)
            if numeric != numeric or numeric in (float("inf"), float("-inf")):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field} must be finite"
                )
            return numeric
        if hint is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field} must be an integer"
                )
            return value
        return value
    args = get_args(hint)
    if origin is Union:
        non_none = [arg for arg in args if arg is not type(None)]
        if value is None:
            return None
        if not non_none:
            return value
        return _coerce_value(non_none[0], value, field)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise ContractError(
                ContractErrorCode.INVALID, f"{field} must be an array"
            )
        item_hint = args[0] if args and args[0] is not Ellipsis else Any
        return tuple(_coerce_value(item_hint, item, field) for item in value)
    if origin in (list, frozenset):
        if not isinstance(value, (list, tuple)):
            raise ContractError(
                ContractErrorCode.INVALID, f"{field} must be an array"
            )
        item_hint = args[0] if args else Any
        coerced = [_coerce_value(item_hint, item, field) for item in value]
        if origin is frozenset:
            return frozenset(coerced)
        return coerced
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise ContractError(
                ContractErrorCode.INVALID, f"{field} must be an object"
            )
        return dict(value)
    return value


def _require_str(value, field):
    if not isinstance(value, str) or not value:
        raise ContractError(
            ContractErrorCode.INVALID, f"{field} must be a non-empty string"
        )
    return value


def _to_jsonable(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Contract):
        return value.to_dict()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, frozenset)):
        return [_to_jsonable(item) for item in value]
    return value


class Contract:
    """Mixin supplying canonical serialization, digests, and round-trips."""

    CONTRACT_NAME: ClassVar[str]
    CONTRACT_VERSION: ClassVar[int] = 1

    def to_dict(self) -> dict:
        data = {
            "contract_name": type(self).CONTRACT_NAME,
            "contract_version": type(self).CONTRACT_VERSION,
        }
        for field in dataclasses.fields(self):  # type: ignore[arg-type]
            data[field.name] = _to_jsonable(getattr(self, field.name))
        return data

    def content_digest(self) -> str:
        """Digest over execution-significant fields (everything recorded)."""

        return content_digest(self.to_dict())

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping):
            raise ContractError(
                ContractErrorCode.INVALID, f"{cls.CONTRACT_NAME} record must be an object"
            )
        if data.get("contract_name") != cls.CONTRACT_NAME:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"contract_name mismatch: expected {cls.CONTRACT_NAME!r}, "
                f"got {data.get('contract_name')!r}",
            )
        require_supported_version(data)
        hints = get_type_hints(cls)
        kwargs = {}
        for field in dataclasses.fields(cls):
            kwargs[field.name] = _coerce_value(
                hints[field.name], data.get(field.name), field.name
            )
        instance = cls(**kwargs)
        return instance


def _resolve_optional(hint):
    """Return (inner_hint, is_optional) for Optional[...] annotations."""

    origin = get_origin(hint)
    if origin is Union:
        args = [arg for arg in get_args(hint) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return hint, False


def _check_field_annotations(instance) -> None:
    """Fail closed on enum-typed or empty str-typed plain fields."""

    hints = get_type_hints(type(instance))
    for field in dataclasses.fields(instance):
        value = getattr(instance, field.name)
        hint, optional = _resolve_optional(hints[field.name])
        if value is None:
            if optional:
                continue
            # None for a non-optional field fails the type checks below.
        origin = get_origin(hint)
        if origin is None and isinstance(hint, type):
            if issubclass(hint, enum.Enum):
                if not isinstance(value, hint):
                    raise ContractError(
                        ContractErrorCode.INVALID,
                        f"{field.name} must be a {hint.__name__} value, "
                        f"got {value!r}",
                    )
                continue
            if hint is str:
                _require_str(value, field.name)
                continue
            if hint is float:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ContractError(
                        ContractErrorCode.INVALID, f"{field.name} must be a number"
                    )
                if value != value or value in (float("inf"), float("-inf")):
                    raise ContractError(
                        ContractErrorCode.INVALID, f"{field.name} must be finite"
                    )
                continue
            if hint is int:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ContractError(
                        ContractErrorCode.INVALID,
                        f"{field.name} must be an integer",
                    )
                continue
            if hint is bool and not isinstance(value, bool):
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field.name} must be a boolean"
                )


def contract_post_init(instance: Contract) -> None:
    """Shared fail-closed validation for every contract record."""

    meta = getattr(instance, "meta", None)
    if not isinstance(meta, ContractMetadata):
        raise ContractError(
            ContractErrorCode.INTEGRITY_VIOLATION,
            f"{type(instance).CONTRACT_NAME} is missing envelope metadata",
        )
    _require_str(meta.record_id, "meta.record_id")
    _require_str(meta.correlation_id, "meta.correlation_id")
    _require_str(meta.producer, "meta.producer")
    parse_utc_timestamp(meta.created_at)
    if not isinstance(meta.sensitivity, Sensitivity):
        raise ContractError(
            ContractErrorCode.INVALID, "meta.sensitivity must be a Sensitivity value"
        )
    if type(instance).CONTRACT_VERSION < 1:
        raise ContractError(
            ContractErrorCode.INVALID, "contract_version must be positive"
        )
    reject_secret_material(instance.to_dict())
    _check_field_annotations(instance)
    custom = getattr(instance, "_validate", None)
    if callable(custom):
        custom()


# ---------------------------------------------------------------------------
# Planner boundary (spec section 2)
# ---------------------------------------------------------------------------


class PlannerResultStatus(enum.Enum):
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PlannerFailureCategory(enum.Enum):
    """Stable planner failure categories (spec 2.7)."""

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_VERSION = "unsupported_version"
    NO_FEASIBLE_STRATEGY = "no_feasible_strategy"
    CONSTRAINT_CONFLICT = "constraint_conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POLICY_UNAVAILABLE = "policy_unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


@dataclasses.dataclass(frozen=True)
class TaskRequest(Contract):
    """User- or system-originated intent presented to planning (spec 2.1)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.task_request"

    meta: ContractMetadata
    request_id: str
    objective: str
    requested_outputs: Tuple[str, ...]
    constraints: Mapping[str, Any]
    actor_ref: str
    goal_contract_ref: str

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class PlannerInput(Contract):
    """Normalized, immutable planning facts (spec 2.1); never a scope widen."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_input"

    meta: ContractMetadata
    request_id: str
    project_ref: str
    goal_ref: str
    state_version: int
    eligible_collaboration_modes: Tuple[str, ...]
    budget_remaining: float
    budget_hard_limit: bool
    feature_gates: Mapping[str, bool]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.state_version < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "state_version must be positive"
            )
        if self.budget_remaining < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "budget_remaining must be non-negative"
            )


@dataclasses.dataclass(frozen=True)
class WorkUnit(Contract):
    """One bounded, dependency-ordered unit of proposed work (spec 2.2)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.work_unit"

    meta: ContractMetadata
    unit_id: str
    depends_on: Tuple[str, ...]
    focus: str
    assigned_agent_ref: str
    verifier_ref: str
    max_retries: int

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.max_retries < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "max_retries must be non-negative"
            )


@dataclasses.dataclass(frozen=True)
class ExecutionStrategy(Contract):
    """A bounded execution proposal; never authority to execute (spec 2.2)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.execution_strategy"

    meta: ContractMetadata
    planner_kind: str
    planner_version: str
    collaboration_mode: str
    synchronization_mode: str
    work_units: Tuple[WorkUnit, ...]
    max_rounds: int
    max_total_cost: float
    stop_condition: str
    fallback_strategy_ref: Optional[str]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.max_rounds < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "max_rounds must be at least 1"
            )
        if self.max_total_cost < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "max_total_cost must be non-negative"
            )
        unit_ids = [unit.unit_id for unit in self.work_units]
        if len(set(unit_ids)) != len(unit_ids):
            raise ContractError(
                ContractErrorCode.INVALID, "work unit ids must be unique"
            )
        known = set(unit_ids)
        for unit in self.work_units:
            unknown = [dep for dep in unit.depends_on if dep not in known]
            if unknown:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"work unit {unit.unit_id!r} depends on unknown units: {unknown}",
                )


@dataclasses.dataclass(frozen=True)
class PlannerFailure(Contract):
    """Bounded planner failure record (spec 2.7)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_failure"

    meta: ContractMetadata
    category: PlannerFailureCategory
    message: str
    retryable: bool
    deterministic_fallback: bool

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class PlannerResult(Contract):
    """Planner output: exactly one of proposed/blocked/cancelled/failed."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.planner_result"

    meta: ContractMetadata
    request_ref: str
    status: PlannerResultStatus
    strategy: Optional[ExecutionStrategy]
    alternatives: Tuple[ExecutionStrategy, ...]
    failure: Optional[PlannerFailure]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.status is PlannerResultStatus.PROPOSED:
            if self.strategy is None:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "a proposed planner result must carry a strategy",
                )
        elif self.status in (PlannerResultStatus.BLOCKED, PlannerResultStatus.FAILED):
            if self.failure is None:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"a {self.status.value} planner result must carry a failure record",
                )
        if self.alternatives and self.status is not PlannerResultStatus.PROPOSED:
            raise ContractError(
                ContractErrorCode.INVALID,
                "ranked alternatives are only valid on proposed results",
            )


# ---------------------------------------------------------------------------
# Agent boundary (spec section 3)
# ---------------------------------------------------------------------------


class AgentOrigin(enum.Enum):
    BUILT_IN = "built_in"
    USER_DEFINED = "user_defined"


class AgentLifecycleState(enum.Enum):
    """Audited agent lifecycle (spec 3.4); terminal states are durable."""

    PROPOSED = "proposed"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    MERGED = "merged"
    RETIRED = "retired"
    CANCELLED = "cancelled"
    FAILED = "failed"


#: Allowed lifecycle transitions; anything not listed fails closed. The
#: failed -> queued retry is only legal through separately admitted bounded
#: retry (spec 3.4).
AGENT_LIFECYCLE_TRANSITIONS = {
    AgentLifecycleState.PROPOSED: frozenset(
        {AgentLifecycleState.AUTHORIZED, AgentLifecycleState.CANCELLED}
    ),
    AgentLifecycleState.AUTHORIZED: frozenset(
        {AgentLifecycleState.QUEUED, AgentLifecycleState.CANCELLED}
    ),
    AgentLifecycleState.QUEUED: frozenset(
        {AgentLifecycleState.STARTING, AgentLifecycleState.CANCELLED}
    ),
    AgentLifecycleState.STARTING: frozenset(
        {
            AgentLifecycleState.RUNNING,
            AgentLifecycleState.FAILED,
            AgentLifecycleState.CANCELLED,
        }
    ),
    AgentLifecycleState.RUNNING: frozenset(
        {
            AgentLifecycleState.WAITING,
            AgentLifecycleState.VERIFYING,
            AgentLifecycleState.FAILED,
            AgentLifecycleState.CANCELLED,
        }
    ),
    AgentLifecycleState.WAITING: frozenset(
        {AgentLifecycleState.RUNNING, AgentLifecycleState.CANCELLED}
    ),
    AgentLifecycleState.VERIFYING: frozenset(
        # PS1-13D (PS1-13B-FUNC-01): VERIFYING cancellable via canonical path.
        {AgentLifecycleState.COMPLETED, AgentLifecycleState.FAILED, AgentLifecycleState.CANCELLED}
    ),
    AgentLifecycleState.COMPLETED: frozenset({AgentLifecycleState.MERGED}),
    AgentLifecycleState.MERGED: frozenset({AgentLifecycleState.RETIRED}),
    # failed -> queued only through a separately admitted bounded retry.
    AgentLifecycleState.FAILED: frozenset({AgentLifecycleState.QUEUED}),
    AgentLifecycleState.CANCELLED: frozenset(),
    AgentLifecycleState.RETIRED: frozenset(),
}


def validate_agent_transition(previous, next_state) -> None:
    """Fail closed on any lifecycle transition outside the audited map."""

    previous = _enum_value(AgentLifecycleState, previous, "previous")
    next_state = _enum_value(AgentLifecycleState, next_state, "next_state")
    allowed = AGENT_LIFECYCLE_TRANSITIONS[previous]
    if next_state not in allowed:
        raise ContractError(
            ContractErrorCode.INVALID,
            f"invalid agent lifecycle transition {previous.value} -> "
            f"{next_state.value}",
        )


class AgentCompletionDisposition(enum.Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class AgentIdentity(Contract):
    """Durable template or task-scoped instance identity (spec 3.1)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.agent_identity"

    meta: ContractMetadata
    agent_id: str
    template_id: str
    template_version: str
    display_name: str
    agent_class: str
    origin: AgentOrigin
    project_ref: str
    parent_agent_ref: Optional[str]
    authorization_ref: str

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class AgentCapabilityDeclaration(Contract):
    """What an agent can attempt; claims subject to validation (spec 3.2)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.agent_capability_declaration"

    meta: ContractMetadata
    agent_ref: str
    capabilities: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]
    supported_collaboration_modes: Tuple[str, ...]
    local_execution: bool
    max_context_items: int
    capability_source: str
    last_validation_status: str

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class AgentAssignment(Contract):
    """Execution-authorized work assignment; immutable after authorization."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.agent_assignment"

    meta: ContractMetadata
    assignment_id: str
    agent_ref: str
    work_unit_ref: str
    focus: str
    acceptance_criteria: Tuple[str, ...]
    input_refs: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]
    dependency_unit_ids: Tuple[str, ...]
    allowed_tools: Tuple[str, ...]
    max_retries: int
    deadline: str
    policy_version: str
    feature_gate_version: int

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.max_retries < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "max_retries must be non-negative"
            )
        parse_utc_timestamp(self.deadline, field="deadline")


@dataclasses.dataclass(frozen=True)
class AgentTaskResult(Contract):
    """A task result with a truthful disposition; never a bare success flag."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.agent_task_result"

    meta: ContractMetadata
    assignment_ref: str
    attempt: int
    disposition: AgentCompletionDisposition
    summary: str
    output_digest: str
    evidence_refs: Tuple[str, ...]
    verifier_supported: Optional[bool]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.attempt < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "attempt must be at least 1"
            )


# ---------------------------------------------------------------------------
# Model provider boundary (spec section 4)
# ---------------------------------------------------------------------------


class CapabilitySource(enum.Enum):
    DECLARED = "declared"
    DISCOVERED = "discovered"
    MEASURED = "measured"


class ModelFailureCategory(enum.Enum):
    """Normalized provider failure categories (spec 4.6)."""

    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_OR_AUTHORIZATION_ERROR = "authentication_or_authorization_error"
    CAPABILITY_MISMATCH = "capability_mismatch"
    PROTOCOL_ERROR = "protocol_error"
    UNAVAILABLE_OR_RATE_LIMITED = "unavailable_or_rate_limited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BUDGET_REJECTED = "budget_rejected"
    CONTENT_OR_SAFETY_REJECTED = "content_or_safety_rejected"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"


@dataclasses.dataclass(frozen=True)
class ProviderDescriptor(Contract):
    """Provider/adapter identity and health; never credentials (spec 4.1)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.provider_descriptor"

    meta: ContractMetadata
    provider_id: str
    adapter_name: str
    model_id: str
    endpoint_class: str
    local_execution: bool
    configuration_provenance: str
    healthy: Optional[bool]

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class ModelCapabilityMetadata(Contract):
    """Provider-neutral capability metadata (spec 4.1); stale != supported."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_capability_metadata"

    meta: ContractMetadata
    provider_ref: str
    streaming: bool
    tool_calling: bool
    vision: bool
    json_mode: bool
    system_messages: bool
    local_execution: bool
    context_window: int
    max_output_tokens: int
    capability_source: CapabilitySource
    observed_at: str

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        parse_utc_timestamp(self.observed_at, field="observed_at")
        if self.context_window < 1 or self.max_output_tokens < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "context_window and max_output_tokens must be positive",
            )


@dataclasses.dataclass(frozen=True)
class ModelCallRequest(Contract):
    """Normalized model request envelope (spec 4.2); no credentials."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_call_request"

    meta: ContractMetadata
    request_id: str
    provider_ref: str
    messages: Tuple[str, ...]
    system_instruction: Optional[str]
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    idempotency_key: str
    privacy_mode: str
    approval_ref: Optional[str]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.max_output_tokens < 1:
            raise ContractError(
                ContractErrorCode.INVALID, "max_output_tokens must be positive"
            )
        if self.timeout_seconds <= 0:
            raise ContractError(
                ContractErrorCode.INVALID, "timeout_seconds must be positive"
            )


@dataclasses.dataclass(frozen=True)
class CostMetadata(Contract):
    """Separated preflight/estimated/measured cost facts (spec 4.5)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.cost_metadata"

    meta: ContractMetadata
    currency: str
    preflight_upper_bound: Optional[float]
    estimated_cost: Optional[float]
    measured_cost: Optional[float]
    pricing_source: Optional[str]
    pricing_known: bool
    settled: bool

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        for field_name in (
            "preflight_upper_bound",
            "estimated_cost",
            "measured_cost",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ContractError(
                    ContractErrorCode.INVALID, f"{field_name} must be non-negative"
                )
        if not self.pricing_known and self.preflight_upper_bound is None:
            # Unknown pricing with no conservative bound must stay blocked
            # upstream; the record itself refuses to claim admissibility.
            raise ContractError(
                ContractErrorCode.INVALID,
                "unknown pricing requires a conservative preflight upper bound",
            )


@dataclasses.dataclass(frozen=True)
class ModelCallResponse(Contract):
    """Provider response envelope with measured usage (spec 4.3)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.model_call_response"

    meta: ContractMetadata
    request_ref: str
    provider_ref: str
    resolved_model_id: str
    text_output: Optional[str]
    finish_reason: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost: Optional[CostMetadata]
    local_execution: bool
    failure_category: Optional[ModelFailureCategory]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "token counts must be non-negative"
            )
        if self.latency_ms < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "latency_ms must be non-negative"
            )


# ---------------------------------------------------------------------------
# Evidence boundary (spec section 5)
# ---------------------------------------------------------------------------


class EvidenceKind(enum.Enum):
    SOURCE = "source"
    CALCULATION = "calculation"
    TOOL_RESULT = "tool_result"
    TEST_RESULT = "test_result"
    OBSERVATION = "observation"
    USER_PREMISE = "user_premise"


class ConfidenceBand(enum.Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class CriterionEvaluation(enum.Enum):
    NOT_RUN = "not_run"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    WAIVED = "waived"


class OverallDisposition(enum.Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclasses.dataclass(frozen=True)
class ProvenanceRecord(Contract):
    """Attributable source provenance (spec 5.3); never credentials."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.provenance_record"

    meta: ContractMetadata
    source_id: str
    source_class: str
    locator: str
    content_digest_value: str
    digest_algorithm: str
    observed_at: str
    producer_version: str
    redacted: bool

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        parse_utc_timestamp(self.observed_at, field="observed_at")
        if not self.locator or not self.content_digest_value:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a citation without a stable locator and digest is not "
                "complete provenance",
            )


@dataclasses.dataclass(frozen=True)
class ConfidenceAssessment(Contract):
    """Belief kept separate from verification (spec 5.2)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.confidence_assessment"

    meta: ContractMetadata
    band: ConfidenceBand
    score: Optional[float]
    method: str
    evaluator: str
    uncertainty_note: Optional[str]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise ContractError(
                ContractErrorCode.INVALID, "score must be within [0, 1]"
            )
        if self.score is not None and self.band is ConfidenceBand.UNKNOWN:
            raise ContractError(
                ContractErrorCode.INVALID,
                "uncalibrated scores stay unknown instead of false precision",
            )


@dataclasses.dataclass(frozen=True)
class ValidationStatus(Contract):
    """Explicit multi-dimension validation state (spec 5.4)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.validation_status"

    meta: ContractMetadata
    criterion_evaluation: CriterionEvaluation
    overall: OverallDisposition
    verifier_supported: Optional[bool]
    waiver_ref: Optional[str]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.overall is OverallDisposition.VERIFIED and (
            self.criterion_evaluation is not CriterionEvaluation.PASS
        ):
            raise ContractError(
                ContractErrorCode.INVALID,
                "overall verified requires the mandated criteria to pass",
            )
        if self.waiver_ref and self.criterion_evaluation is not CriterionEvaluation.WAIVED:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a waiver reference requires the waived criterion dimension",
            )


@dataclasses.dataclass(frozen=True)
class ExecutionEvidence(Contract):
    """Attributable observation produced by execution activity (spec 5.1)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.execution_evidence"

    meta: ContractMetadata
    evidence_id: str
    kind: EvidenceKind
    summary: str
    producer: str
    project_ref: str
    task_ref: Optional[str]
    method: str
    content_digest_value: str
    provenance_ref: Optional[str]
    validation: Optional[ValidationStatus]
    confidence: Optional[ConfidenceAssessment]

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if not self.method:
            raise ContractError(
                ContractErrorCode.INVALID,
                "evidence requires a non-empty observation method",
            )
