"""Core domain objects for the AMCA collaborative cognition kernel.

The Phase 0 model intentionally uses only Python's standard library.  Domain
objects are plain dataclasses with explicit JSON representations so snapshots
can be inspected by people, persisted in SQLite, and migrated later without
depending on pickle or a provider SDK.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import math
from typing import Any, Mapping, TypeVar
from uuid import uuid4

from .security import assert_no_sensitive_keys


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp suitable for durable JSON."""

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _utc_datetime(value: str, *, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


class CollaborationMode(str, Enum):
    SOLO = "solo"
    JOINT = "joint"
    JOINT_DELIBERATION = "joint"
    PARALLEL = "parallel"
    PARALLEL_DECOMPOSITION = "parallel"
    INDEPENDENT_DISCUSS = "independent_discuss"
    INDEPENDENT_THEN_DISCUSS = "independent_discuss"
    ADAPTIVE_HYBRID = "adaptive_hybrid"
    HYBRID = "adaptive_hybrid"


class SyncMode(str, Enum):
    ASYNC = "async"
    ASYNCHRONOUS = "async"
    SOFT = "soft"
    QUORUM = "quorum"
    HARD = "hard"
    BOUNDED_STALENESS = "bounded_staleness"


class ComplexityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    FRONTIER = "critical"


class PrivacyMode(str, Enum):
    OFFLINE = "offline"
    PRIVATE_HYBRID = "private_hybrid"
    FULL_FRONTIER = "full_frontier"


class ComputePolicy(str, Enum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    RESEARCH = "research"
    MAXIMUM_INTELLIGENCE = "maximum_intelligence"
    MAXIMUM = "maximum_intelligence"
    CUSTOM = "custom"


class EventType(str, Enum):
    HYPOTHESIS = "hypothesis"
    OBJECTION = "objection"
    QUESTION = "question"
    ANSWER = "answer"
    ALTERNATIVE = "alternative"
    CORRECTION = "correction"
    EQUATION_UPDATE = "equation_update"
    EVIDENCE = "evidence"
    NOVELTY_ALERT = "novelty_alert"
    EXPERIMENT_REQUEST = "experiment_request"
    EXPERIMENT_RESULT = "experiment_result"
    TOOL_REQUEST = "tool_request"
    TOOL_RESULT = "tool_result"
    CONSENSUS = "consensus"
    BLOCKER = "blocker"
    ARCHITECTURE_PROPOSAL = "architecture_proposal"
    ARCHITECTURE_CHANGE_PROPOSAL = "architecture_proposal"
    CRITICAL_CONFLICT = "critical_conflict"
    STATE_UPDATE = "state_update"
    GOAL_PROGRESS = "goal_progress"
    MODEL_ERROR = "model_error"
    FOCUS_CHANGE = "focus_change"
    ARTIFACT_UPDATE = "artifact_update"
    COST_UPDATE = "cost_update"
    USER_INSTRUCTION = "user_instruction"
    MODE_SWITCH = "mode_switch"
    SYNC_CHANGE = "sync_change"
    SYNC_MODE_CHANGE = "sync_change"
    SYNC_CHECKPOINT_OPENED = "sync_checkpoint_opened"
    PARTICIPANT_DISPOSITION = "participant_disposition"
    SYNC_CHECKPOINT_CLOSED = "sync_checkpoint_closed"
    AMCA_API_PROVISIONED = "amca_api_provisioned"
    AMCA_API_REVOKED = "amca_api_revoked"
    AMCA_CREDIT_LOW = "amca_credit_low"
    AMCA_CREDIT_EXHAUSTED = "amca_credit_exhausted"
    BYOK_PROVIDER_CONNECTED = "byok_provider_connected"
    BYOK_PROVIDER_FAILED = "byok_provider_failed"
    ENDPOINT_VERIFIED = "endpoint_verified"
    ENDPOINT_CHANGED = "endpoint_changed"
    ENDPOINT_UNVERIFIED = "endpoint_unverified"
    MODEL_CATALOG_UPDATED = "model_catalog_updated"
    MODEL_DEPRECATED = "model_deprecated"
    PRICING_UPDATED = "pricing_updated"
    PRICING_STALE = "pricing_stale"
    PRICING_UNKNOWN = "pricing_unknown"
    PRICING_CONFLICT = "pricing_conflict"
    LOCAL_MODEL_COMPATIBILITY_UPDATED = "local_model_compatibility_updated"
    LOCAL_MODEL_INSTALLED = "local_model_installed"
    LOCAL_MODEL_INSTALL_FAILED = "local_model_install_failed"
    MODEL_TEAM_CREATED = "model_team_created"
    MODEL_TEAM_UPDATED = "model_team_updated"
    MODEL_TEAM_DELETED = "model_team_deleted"
    MODEL_SELECTION_RESOLVED = "model_selection_resolved"
    TASK_COST_FINALIZED = "task_cost_finalized"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    WARNING = "warning"
    HIGH = "high"
    ERROR = "error"
    CRITICAL = "critical"


class EvaluatorKind(str, Enum):
    CUSTOM = "custom"
    EXACT_VALUE = "exact_value"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    JSON_SCHEMA = "json_schema"
    FILE_EXISTS = "file_exists"
    CONTENT_PREDICATE = "content_predicate"
    COMMAND_EXIT = "command_exit"
    TEST_SUITE = "test_suite"
    HUMAN_REVIEW = "human_review"


class CriterionStatus(str, Enum):
    NOT_RUN = "not_run"
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    WAIVED = "waived"


class DecisionClass(str, Enum):
    PROVISIONAL = "provisional"
    REVERSIBLE = "reversible"
    SIGNIFICANT = "significant"
    CRITICAL = "critical"
    IRREVERSIBLE = "irreversible"


class EvidenceKind(str, Enum):
    SOURCE = "source"
    CALCULATION = "calculation"
    TOOL_RESULT = "tool_result"
    TEST_RESULT = "test_result"
    OBSERVATION = "observation"
    USER_PREMISE = "user_premise"


class EvidenceStrength(str, Enum):
    UNKNOWN = "unknown"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class ParticipantDispositionStatus(str, Enum):
    SUPPORT = "support"
    OBJECT = "object"
    ABSTAIN = "abstain"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RELEASED = "released"


class CheckpointStatus(str, Enum):
    OPEN = "open"
    PASSED = "passed"
    BLOCKED = "blocked"
    EXPIRED = "expired"


E = TypeVar("E", bound=Enum)


def _enum(enum_type: type[E], value: E | str) -> E:
    if isinstance(value, enum_type):
        return value
    return enum_type(value)


def _json_ready(value: Any) -> Any:
    """Convert supported values to a deterministic JSON-compatible tree."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if is_dataclass(value):
        to_dict = getattr(value, "to_dict", None)
        return _json_ready(to_dict() if callable(to_dict) else asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite floating point values are not valid AMCA JSON")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _to_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=None if indent is not None else (",", ":"),
        indent=indent,
    )


def _load_object(raw: str | bytes | bytearray) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _require_nonnegative(name: str, value: int | float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(slots=True)
class GoalCriterion:
    """One observable success condition in a goal contract."""

    description: str
    id: str = field(default_factory=lambda: uuid4().hex)
    required: bool = True
    satisfied: bool = False
    evidence: list[str] = field(default_factory=list)
    satisfied_at: str | None = None
    evaluator_kind: EvaluatorKind = EvaluatorKind.CUSTOM
    expected_value: Any = None
    tolerance: float | None = None
    evidence_requirement: int = 1
    status: CriterionStatus = CriterionStatus.NOT_RUN
    evidence_ids: list[str] = field(default_factory=list)
    decision_id: str | None = None
    decision_class: DecisionClass = DecisionClass.SIGNIFICANT
    waiver_authority: str | None = None

    def __post_init__(self) -> None:
        self.evaluator_kind = _enum(EvaluatorKind, self.evaluator_kind)
        self.status = _enum(CriterionStatus, self.status)
        self.decision_class = _enum(DecisionClass, self.decision_class)
        if not isinstance(self.evidence_requirement, int) or isinstance(
            self.evidence_requirement, bool
        ):
            raise ValueError("evidence_requirement must be an integer")
        _require_nonnegative("evidence_requirement", self.evidence_requirement)
        if self.tolerance is not None:
            _require_nonnegative("tolerance", self.tolerance)
        self.evidence = [str(item) for item in self.evidence]
        self.evidence_ids = [str(item) for item in self.evidence_ids]
        if self.satisfied and self.status is CriterionStatus.NOT_RUN:
            self.status = CriterionStatus.PASS
        if self.status in {CriterionStatus.PASS, CriterionStatus.WAIVED}:
            self.satisfied = True
        elif self.status in {
            CriterionStatus.NOT_RUN,
            CriterionStatus.FAIL,
            CriterionStatus.BLOCKED,
        }:
            self.satisfied = False

    def mark_satisfied(
        self,
        evidence: str | None = None,
        *,
        at: str | None = None,
        evidence_ids: list[str] | None = None,
        decision_id: str | None = None,
    ) -> None:
        self.satisfied = True
        self.status = CriterionStatus.PASS
        self.satisfied_at = self.satisfied_at or at or utc_now()
        if evidence and evidence not in self.evidence:
            self.evidence.append(evidence)
        for item in evidence_ids or []:
            if item not in self.evidence_ids:
                self.evidence_ids.append(item)
        if decision_id is not None:
            self.decision_id = decision_id

    def reopen(self, *, status: CriterionStatus = CriterionStatus.NOT_RUN) -> None:
        self.satisfied = False
        self.status = _enum(CriterionStatus, status)
        self.satisfied_at = None
        self.evidence = []
        self.evidence_ids = []
        self.decision_id = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "required": self.required,
            "satisfied": self.satisfied,
            "evidence": list(self.evidence),
            "satisfied_at": self.satisfied_at,
            "evaluator_kind": self.evaluator_kind.value,
            "expected_value": _json_ready(self.expected_value),
            "tolerance": self.tolerance,
            "evidence_requirement": self.evidence_requirement,
            "status": self.status.value,
            "evidence_ids": list(self.evidence_ids),
            "decision_id": self.decision_id,
            "decision_class": self.decision_class.value,
            "waiver_authority": self.waiver_authority,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GoalCriterion:
        return cls(
            id=str(value.get("id") or uuid4().hex),
            description=str(value.get("description", "")),
            required=bool(value.get("required", True)),
            satisfied=bool(value.get("satisfied", False)),
            evidence=[str(item) for item in value.get("evidence", [])],
            satisfied_at=(
                str(value["satisfied_at"])
                if value.get("satisfied_at") is not None
                else None
            ),
            evaluator_kind=EvaluatorKind(
                value.get("evaluator_kind", EvaluatorKind.CUSTOM.value)
            ),
            expected_value=value.get("expected_value"),
            tolerance=(
                float(value["tolerance"])
                if value.get("tolerance") is not None
                else None
            ),
            evidence_requirement=int(value.get("evidence_requirement", 1)),
            status=CriterionStatus(
                value.get("status", CriterionStatus.NOT_RUN.value)
            ),
            evidence_ids=[str(item) for item in value.get("evidence_ids", [])],
            decision_id=(
                str(value["decision_id"])
                if value.get("decision_id") is not None
                else None
            ),
            decision_class=DecisionClass(
                value.get("decision_class", DecisionClass.SIGNIFICANT.value)
            ),
            waiver_authority=(
                str(value["waiver_authority"])
                if value.get("waiver_authority") is not None
                else None
            ),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> GoalCriterion:
        return cls.from_dict(_load_object(raw))


@dataclass(slots=True)
class GoalContract:
    """Explicit completion rules and bounded-execution limits for a goal."""

    goal: str
    criteria: list[GoalCriterion] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)
    max_iterations: int | None = None
    max_runtime_seconds: float | None = None
    max_api_cost_usd: float | None = None
    # 28B: budget values are informational telemetry by default in the
    # desktop product.  When true, kernel budget-exceeded checks record
    # costs but never block execution; provider/network authorization
    # remains the security control.
    budget_is_telemetry: bool = False
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.criteria = [
            item if isinstance(item, GoalCriterion) else GoalCriterion.from_dict(item)
            for item in self.criteria
        ]
        for name in ("max_iterations", "max_runtime_seconds", "max_api_cost_usd"):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative(name, value)

    def is_satisfied(self) -> bool:
        required = [criterion for criterion in self.criteria if criterion.required]
        return bool(required) and all(criterion.satisfied for criterion in required)

    @property
    def satisfied(self) -> bool:
        return self.is_satisfied()

    @property
    def progress(self) -> float:
        required = [criterion for criterion in self.criteria if criterion.required]
        if not required:
            return 0.0
        return sum(criterion.satisfied for criterion in required) / len(required)

    @property
    def unmet_criteria(self) -> list[GoalCriterion]:
        return [
            criterion
            for criterion in self.criteria
            if criterion.required and not criterion.satisfied
        ]

    def criterion(self, criterion_id: str) -> GoalCriterion | None:
        return next(
            (criterion for criterion in self.criteria if criterion.id == criterion_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
            "max_iterations": self.max_iterations,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_api_cost_usd": self.max_api_cost_usd,
            "budget_is_telemetry": self.budget_is_telemetry,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GoalContract:
        return cls(
            id=str(value.get("id") or uuid4().hex),
            goal=str(value.get("goal", "")),
            criteria=[
                GoalCriterion.from_dict(item) for item in value.get("criteria", [])
            ],
            max_iterations=value.get("max_iterations"),
            max_runtime_seconds=value.get("max_runtime_seconds"),
            max_api_cost_usd=value.get("max_api_cost_usd"),
            budget_is_telemetry=bool(value.get("budget_is_telemetry", False)),
            created_at=str(value.get("created_at") or utc_now()),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> GoalContract:
        return cls.from_dict(_load_object(raw))


@dataclass(slots=True)
class CognitiveEvent:
    """A compact semantic contribution produced against a state version."""

    event_type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    state_version: int = 0
    target: str | None = None
    severity: Severity = Severity.INFO
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        self.event_type = _enum(EventType, self.event_type)
        self.severity = _enum(Severity, self.severity)
        self.payload = dict(self.payload)
        _require_nonnegative("state_version", self.state_version)

    @property
    def event_id(self) -> str:
        return self.id

    @property
    def source_model(self) -> str:
        return self.source

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "source": self.source,
            "payload": _json_ready(self.payload),
            "state_version": self.state_version,
            "target": self.target,
            "severity": self.severity.value,
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CognitiveEvent:
        payload = value.get("payload", {})
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be a JSON object")
        return cls(
            id=str(value.get("id") or uuid4().hex),
            event_type=EventType(value["event_type"]),
            source=str(value.get("source", "unknown")),
            payload=dict(payload),
            state_version=int(value.get("state_version", 0)),
            target=str(value["target"]) if value.get("target") is not None else None,
            severity=Severity(value.get("severity", Severity.INFO.value)),
            created_at=str(value.get("created_at") or utc_now()),
            correlation_id=(
                str(value["correlation_id"])
                if value.get("correlation_id") is not None
                else None
            ),
            causation_id=(
                str(value["causation_id"])
                if value.get("causation_id") is not None
                else None
            ),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        assert_no_sensitive_keys(_json_ready(self.payload), path="event.payload")
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> CognitiveEvent:
        return cls.from_dict(_load_object(raw))


@dataclass(slots=True)
class EvidenceRecord:
    """Attributable evidence whose verification state is separate from belief."""

    summary: str
    kind: EvidenceKind = EvidenceKind.OBSERVATION
    provenance: dict[str, Any] = field(default_factory=dict)
    method: str = ""
    supports_claim_ids: list[str] = field(default_factory=list)
    contradicts_claim_ids: list[str] = field(default_factory=list)
    supports_criterion_ids: list[str] = field(default_factory=list)
    addresses_disagreement_ids: list[str] = field(default_factory=list)
    strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    sensitivity: str = "internal"
    value: Any = None
    producer: str = "unknown"
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("evidence summary must not be empty")
        self.kind = _enum(EvidenceKind, self.kind)
        self.strength = _enum(EvidenceStrength, self.strength)
        self.verification_status = _enum(
            VerificationStatus, self.verification_status
        )
        self.provenance = dict(self.provenance)
        self.supports_claim_ids = [str(item) for item in self.supports_claim_ids]
        self.contradicts_claim_ids = [
            str(item) for item in self.contradicts_claim_ids
        ]
        self.supports_criterion_ids = [
            str(item) for item in self.supports_criterion_ids
        ]
        self.addresses_disagreement_ids = [
            str(item) for item in self.addresses_disagreement_ids
        ]

    @property
    def verified(self) -> bool:
        return self.verification_status is VerificationStatus.VERIFIED

    @property
    def has_provenance(self) -> bool:
        return bool(
            self.provenance.get("source")
            and self.provenance.get("locator")
            and self.provenance.get("content_digest")
            and self.provenance.get("timestamp")
        )

    def usable_for_critical_disagreement(self, disagreement_id: str) -> bool:
        return bool(
            self.verified
            and self.has_provenance
            and self.method.strip()
            and self.strength in {EvidenceStrength.MODERATE, EvidenceStrength.STRONG}
            and disagreement_id in self.addresses_disagreement_ids
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "kind": self.kind.value,
            "provenance": _json_ready(self.provenance),
            "method": self.method,
            "supports_claim_ids": list(self.supports_claim_ids),
            "contradicts_claim_ids": list(self.contradicts_claim_ids),
            "supports_criterion_ids": list(self.supports_criterion_ids),
            "addresses_disagreement_ids": list(
                self.addresses_disagreement_ids
            ),
            "strength": self.strength.value,
            "verification_status": self.verification_status.value,
            "sensitivity": self.sensitivity,
            "value": _json_ready(self.value),
            "producer": self.producer,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceRecord:
        provenance = value.get("provenance", {})
        if not isinstance(provenance, Mapping):
            raise ValueError("evidence provenance must be an object")
        return cls(
            id=str(value.get("id") or uuid4().hex),
            summary=str(value.get("summary", "")),
            kind=EvidenceKind(value.get("kind", EvidenceKind.OBSERVATION.value)),
            provenance=dict(provenance),
            method=str(value.get("method", "")),
            supports_claim_ids=[
                str(item) for item in value.get("supports_claim_ids", [])
            ],
            contradicts_claim_ids=[
                str(item) for item in value.get("contradicts_claim_ids", [])
            ],
            supports_criterion_ids=[
                str(item) for item in value.get("supports_criterion_ids", [])
            ],
            addresses_disagreement_ids=[
                str(item) for item in value.get("addresses_disagreement_ids", [])
            ],
            strength=EvidenceStrength(
                value.get("strength", EvidenceStrength.UNKNOWN.value)
            ),
            verification_status=VerificationStatus(
                value.get(
                    "verification_status", VerificationStatus.UNVERIFIED.value
                )
            ),
            sensitivity=str(value.get("sensitivity", "internal")),
            value=value.get("value"),
            producer=str(value.get("producer", "unknown")),
            created_at=str(value.get("created_at") or utc_now()),
        )


@dataclass(slots=True)
class ParticipantDisposition:
    participant_id: str
    status: ParticipantDispositionStatus
    summary: str = ""
    start_state_version: int = 0
    integrated_state_version: int | None = None
    cognitive_lag: int = 0
    rebased: bool = False
    authorized_by: str | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.participant_id:
            raise ValueError("participant_id must not be empty")
        self.status = _enum(ParticipantDispositionStatus, self.status)
        _require_nonnegative("start_state_version", self.start_state_version)
        _require_nonnegative("cognitive_lag", self.cognitive_lag)
        if self.integrated_state_version is not None:
            _require_nonnegative(
                "integrated_state_version", self.integrated_state_version
            )
        if (
            self.status is ParticipantDispositionStatus.RELEASED
            and not self.authorized_by
        ):
            raise ValueError("released participants require authorization")

    @property
    def attended(self) -> bool:
        return self.status in {
            ParticipantDispositionStatus.SUPPORT,
            ParticipantDispositionStatus.OBJECT,
            ParticipantDispositionStatus.ABSTAIN,
            ParticipantDispositionStatus.RELEASED,
        }

    @property
    def supports(self) -> bool:
        return self.status is ParticipantDispositionStatus.SUPPORT

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "status": self.status.value,
            "summary": self.summary,
            "start_state_version": self.start_state_version,
            "integrated_state_version": self.integrated_state_version,
            "cognitive_lag": self.cognitive_lag,
            "rebased": self.rebased,
            "authorized_by": self.authorized_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ParticipantDisposition:
        return cls(
            participant_id=str(value.get("participant_id", "")),
            status=ParticipantDispositionStatus(value["status"]),
            summary=str(value.get("summary", "")),
            start_state_version=int(value.get("start_state_version", 0)),
            integrated_state_version=(
                int(value["integrated_state_version"])
                if value.get("integrated_state_version") is not None
                else None
            ),
            cognitive_lag=int(value.get("cognitive_lag", 0)),
            rebased=bool(value.get("rebased", False)),
            authorized_by=(
                str(value["authorized_by"])
                if value.get("authorized_by") is not None
                else None
            ),
            created_at=str(value.get("created_at") or utc_now()),
        )


_SYNC_RANK = {
    SyncMode.ASYNC: 0,
    SyncMode.SOFT: 1,
    SyncMode.QUORUM: 2,
    SyncMode.HARD: 4,
}
_DECISION_RANK = {
    DecisionClass.PROVISIONAL: 0,
    DecisionClass.REVERSIBLE: 1,
    DecisionClass.SIGNIFICANT: 2,
    DecisionClass.CRITICAL: 3,
    DecisionClass.IRREVERSIBLE: 4,
}


@dataclass(slots=True)
class SynchronizationCheckpoint:
    sync_mode: SyncMode
    participant_ids: list[str]
    required_participant_ids: list[str]
    base_mode: SyncMode | None = None
    dispositions: dict[str, ParticipantDisposition] = field(default_factory=dict)
    status: CheckpointStatus = CheckpointStatus.OPEN
    passed: bool = False
    reason: str = ""
    max_version_lag: int = 0
    opened_state_version: int = 0
    closed_state_version: int | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    opened_at: str = field(default_factory=utc_now)
    deadline_at: str | None = None
    closed_at: str | None = None
    expired_at: str | None = None

    def __post_init__(self) -> None:
        self.sync_mode = _enum(SyncMode, self.sync_mode)
        if self.base_mode is not None:
            self.base_mode = _enum(SyncMode, self.base_mode)
        if self.sync_mode is SyncMode.BOUNDED_STALENESS:
            if self.base_mode is None:
                self.base_mode = SyncMode.HARD
            if self.base_mode is SyncMode.BOUNDED_STALENESS:
                raise ValueError("bounded staleness requires a non-bounded base mode")
        else:
            self.base_mode = self.sync_mode
        self.status = _enum(CheckpointStatus, self.status)
        self.participant_ids = sorted(str(item) for item in self.participant_ids)
        self.required_participant_ids = sorted(
            str(item) for item in self.required_participant_ids
        )
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("checkpoint participant IDs must be unique")
        if len(set(self.required_participant_ids)) != len(
            self.required_participant_ids
        ):
            raise ValueError("required checkpoint participant IDs must be unique")
        if not set(self.required_participant_ids).issubset(self.participant_ids):
            raise ValueError("required participants must be eligible participants")
        self.dispositions = {
            str(key): (
                item
                if isinstance(item, ParticipantDisposition)
                else ParticipantDisposition.from_dict(item)
            )
            for key, item in self.dispositions.items()
        }
        _require_nonnegative("max_version_lag", self.max_version_lag)
        _require_nonnegative("opened_state_version", self.opened_state_version)
        if self.closed_state_version is not None:
            _require_nonnegative("closed_state_version", self.closed_state_version)
        opened_time = _utc_datetime(self.opened_at, name="opened_at")
        deadline_time = (
            _utc_datetime(self.deadline_at, name="deadline_at")
            if self.deadline_at is not None
            else None
        )
        if deadline_time is not None and deadline_time < opened_time:
            raise ValueError("checkpoint deadline cannot precede its opening")
        closed_time = (
            _utc_datetime(self.closed_at, name="closed_at")
            if self.closed_at is not None
            else None
        )
        expired_time = (
            _utc_datetime(self.expired_at, name="expired_at")
            if self.expired_at is not None
            else None
        )
        if closed_time is not None and closed_time < opened_time:
            raise ValueError("checkpoint closure cannot precede its opening")
        if self.status is CheckpointStatus.OPEN and self.passed:
            raise ValueError("an open checkpoint cannot already be passed")
        if self.status is CheckpointStatus.PASSED and not self.passed:
            raise ValueError("passed checkpoint status requires passed=true")
        if (
            self.status in {CheckpointStatus.BLOCKED, CheckpointStatus.EXPIRED}
            and self.passed
        ):
            raise ValueError("blocked or expired checkpoint status requires passed=false")
        if self.status is CheckpointStatus.OPEN and (
            self.closed_at is not None
            or self.closed_state_version is not None
            or self.expired_at is not None
        ):
            raise ValueError("an open checkpoint cannot have closure metadata")
        if self.status is not CheckpointStatus.OPEN and (
            self.closed_at is None or self.closed_state_version is None
        ):
            raise ValueError("a closed checkpoint requires closure metadata")
        if self.status is CheckpointStatus.EXPIRED and self.expired_at is None:
            raise ValueError("an expired checkpoint requires expired_at")
        if self.status is not CheckpointStatus.EXPIRED and self.expired_at is not None:
            raise ValueError("only an expired checkpoint may have expired_at")
        if (
            self.status is CheckpointStatus.EXPIRED
            and deadline_time is not None
            and expired_time is not None
            and expired_time < deadline_time
        ):
            raise ValueError("checkpoint cannot expire before its deadline")

    @property
    def effective_mode(self) -> SyncMode:
        assert self.base_mode is not None
        return self.base_mode

    def is_expired(self, at: str | None = None) -> bool:
        if self.status is CheckpointStatus.EXPIRED:
            return True
        if self.status is not CheckpointStatus.OPEN:
            return False
        if self.deadline_at is None:
            return False
        check_time = _utc_datetime(at or utc_now(), name="checkpoint comparison time")
        return check_time >= _utc_datetime(self.deadline_at, name="deadline_at")

    def record(
        self, disposition: ParticipantDisposition, *, at: str | None = None
    ) -> None:
        if self.status is not CheckpointStatus.OPEN:
            raise ValueError("cannot add a disposition to a closed checkpoint")
        if self.is_expired(at or disposition.created_at):
            raise ValueError("cannot add a disposition after the checkpoint deadline")
        if disposition.participant_id not in self.participant_ids:
            raise ValueError("disposition participant is not eligible")
        if disposition.participant_id in self.dispositions:
            raise ValueError("participant already has a checkpoint disposition")
        self.dispositions[disposition.participant_id] = disposition

    def can_pass(
        self, *, has_critical_objection: bool, at: str | None = None
    ) -> bool:
        if has_critical_objection or self.is_expired(at):
            return False
        values = list(self.dispositions.values())
        if self.effective_mode is SyncMode.ASYNC:
            base_passes = True
        elif self.effective_mode is SyncMode.SOFT:
            base_passes = True
        elif self.effective_mode is SyncMode.QUORUM:
            if not self.participant_ids:
                return False
            threshold = min(
                len(self.participant_ids),
                max(2, math.ceil(2 * len(self.participant_ids) / 3)),
            )
            base_passes = sum(item.attended for item in values) >= threshold
        else:
            base_passes = bool(self.required_participant_ids) and all(
                participant_id in self.dispositions
                and self.dispositions[participant_id].attended
                for participant_id in self.required_participant_ids
            )
        if not base_passes:
            return False
        if self.sync_mode is SyncMode.BOUNDED_STALENESS:
            return all(
                participant_id in self.dispositions
                and (
                    self.dispositions[participant_id].cognitive_lag
                    <= self.max_version_lag
                    or self.dispositions[participant_id].rebased
                    or self.dispositions[participant_id].status
                    is ParticipantDispositionStatus.RELEASED
                )
                for participant_id in self.required_participant_ids
            )
        return True

    def permits(self, decision_class: DecisionClass) -> bool:
        if not self.passed:
            return False
        decision_class = _enum(DecisionClass, decision_class)
        # A user may explicitly release an unavailable required participant so
        # bounded work can continue, but absence is not equivalent to review
        # for critical/irreversible decisions.
        if decision_class in {
            DecisionClass.CRITICAL,
            DecisionClass.IRREVERSIBLE,
        } and any(
            participant_id in self.dispositions
            and self.dispositions[participant_id].status
            is ParticipantDispositionStatus.RELEASED
            for participant_id in self.required_participant_ids
        ):
            return False
        required_rank = _DECISION_RANK[decision_class]
        gate_rank = _SYNC_RANK[self.effective_mode]
        return required_rank <= gate_rank

    def close(
        self,
        *,
        passed: bool,
        reason: str,
        closed_state_version: int,
        closed_at: str,
        expired: bool = False,
    ) -> None:
        if self.status is not CheckpointStatus.OPEN:
            raise ValueError("checkpoint is already closed")
        closed_time = _utc_datetime(closed_at, name="closed_at")
        if closed_time < _utc_datetime(self.opened_at, name="opened_at"):
            raise ValueError("checkpoint closure cannot precede its opening")
        if expired and passed:
            raise ValueError("an expired checkpoint cannot pass")
        if expired:
            if self.deadline_at is None:
                raise ValueError("a checkpoint without a deadline cannot expire")
            if closed_time < _utc_datetime(self.deadline_at, name="deadline_at"):
                raise ValueError("checkpoint cannot expire before its deadline")
        self.passed = passed
        self.status = (
            CheckpointStatus.EXPIRED
            if expired
            else (CheckpointStatus.PASSED if passed else CheckpointStatus.BLOCKED)
        )
        self.reason = reason
        self.closed_state_version = closed_state_version
        self.closed_at = closed_at
        self.expired_at = closed_at if expired else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sync_mode": self.sync_mode.value,
            "base_mode": self.effective_mode.value,
            "participant_ids": sorted(self.participant_ids),
            "required_participant_ids": sorted(self.required_participant_ids),
            "dispositions": {
                key: self.dispositions[key].to_dict()
                for key in sorted(self.dispositions)
            },
            "status": self.status.value,
            "passed": self.passed,
            "reason": self.reason,
            "max_version_lag": self.max_version_lag,
            "opened_state_version": self.opened_state_version,
            "closed_state_version": self.closed_state_version,
            "opened_at": self.opened_at,
            "deadline_at": self.deadline_at,
            "closed_at": self.closed_at,
            "expired_at": self.expired_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SynchronizationCheckpoint:
        dispositions = value.get("dispositions", {})
        if not isinstance(dispositions, Mapping):
            raise ValueError("checkpoint dispositions must be an object")
        return cls(
            id=str(value.get("id") or uuid4().hex),
            sync_mode=SyncMode(value["sync_mode"]),
            base_mode=(
                SyncMode(value["base_mode"])
                if value.get("base_mode") is not None
                else None
            ),
            participant_ids=[str(item) for item in value.get("participant_ids", [])],
            required_participant_ids=[
                str(item) for item in value.get("required_participant_ids", [])
            ],
            dispositions={
                str(key): ParticipantDisposition.from_dict(item)
                for key, item in dispositions.items()
            },
            status=CheckpointStatus(
                value.get("status", CheckpointStatus.OPEN.value)
            ),
            passed=bool(value.get("passed", False)),
            reason=str(value.get("reason", "")),
            max_version_lag=int(value.get("max_version_lag", 0)),
            opened_state_version=int(value.get("opened_state_version", 0)),
            closed_state_version=(
                int(value["closed_state_version"])
                if value.get("closed_state_version") is not None
                else None
            ),
            opened_at=str(value.get("opened_at") or utc_now()),
            deadline_at=(
                str(value["deadline_at"])
                if value.get("deadline_at") is not None
                else None
            ),
            closed_at=(
                str(value["closed_at"])
                if value.get("closed_at") is not None
                else None
            ),
            expired_at=(
                str(value["expired_at"])
                if value.get("expired_at") is not None
                else None
            ),
        )


@dataclass(slots=True)
class DecisionRecord:
    criterion_id: str
    decision_class: DecisionClass
    checkpoint_id: str
    evidence_ids: list[str]
    dispositions: dict[str, str]
    rationale: str
    evaluator: str
    passed: bool = True
    critical_disagreement_dispositions: dict[str, str] = field(
        default_factory=dict
    )
    authorized_by: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.criterion_id or not self.checkpoint_id:
            raise ValueError("decision requires criterion and checkpoint IDs")
        self.decision_class = _enum(DecisionClass, self.decision_class)
        self.evidence_ids = [str(item) for item in self.evidence_ids]
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("decision evidence IDs must be unique")
        self.dispositions = {
            str(key): str(value) for key, value in self.dispositions.items()
        }
        self.critical_disagreement_dispositions = {
            str(key): str(value)
            for key, value in self.critical_disagreement_dispositions.items()
        }
        if not self.rationale.strip() or not self.evaluator.strip():
            raise ValueError("decision requires rationale and evaluator")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "criterion_id": self.criterion_id,
            "decision_class": self.decision_class.value,
            "checkpoint_id": self.checkpoint_id,
            "evidence_ids": list(self.evidence_ids),
            "dispositions": dict(self.dispositions),
            "critical_disagreement_dispositions": dict(
                self.critical_disagreement_dispositions
            ),
            "rationale": self.rationale,
            "evaluator": self.evaluator,
            "passed": self.passed,
            "authorized_by": self.authorized_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DecisionRecord:
        return cls(
            id=str(value.get("id") or uuid4().hex),
            criterion_id=str(value.get("criterion_id", "")),
            decision_class=DecisionClass(
                value.get("decision_class", DecisionClass.SIGNIFICANT.value)
            ),
            checkpoint_id=str(value.get("checkpoint_id", "")),
            evidence_ids=[str(item) for item in value.get("evidence_ids", [])],
            dispositions={
                str(key): str(item)
                for key, item in value.get("dispositions", {}).items()
            },
            critical_disagreement_dispositions={
                str(key): str(item)
                for key, item in value.get(
                    "critical_disagreement_dispositions", {}
                ).items()
            },
            rationale=str(value.get("rationale", "")),
            evaluator=str(value.get("evaluator", "")),
            passed=bool(value.get("passed", True)),
            authorized_by=(
                str(value["authorized_by"])
                if value.get("authorized_by") is not None
                else None
            ),
            created_at=str(value.get("created_at") or utc_now()),
        )


@dataclass(slots=True)
class Disagreement:
    """A disputed claim that remains open until evidence-backed resolution."""

    topic: str
    description: str = ""
    raised_by: str = "unknown"
    positions: dict[str, str] = field(default_factory=dict)
    severity: Severity = Severity.MEDIUM
    evidence: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    discriminating_action: str = ""
    resolved: bool = False
    resolution: str | None = None
    resolution_decision_id: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    opened_at: str = field(default_factory=utc_now)
    resolved_at: str | None = None

    def __post_init__(self) -> None:
        self.severity = _enum(Severity, self.severity)
        self.positions = {str(key): str(value) for key, value in self.positions.items()}
        self.evidence = [str(item) for item in self.evidence]
        self.evidence_ids = [str(item) for item in self.evidence_ids]

    def resolve(
        self,
        resolution: str,
        evidence: str | None = None,
        *,
        at: str | None = None,
        evidence_ids: list[str] | None = None,
        decision_id: str | None = None,
    ) -> None:
        self.resolved = True
        self.resolution = resolution
        self.resolved_at = at or utc_now()
        if evidence and evidence not in self.evidence:
            self.evidence.append(evidence)
        for item in evidence_ids or []:
            if item not in self.evidence_ids:
                self.evidence_ids.append(item)
        if decision_id is not None:
            self.resolution_decision_id = decision_id

    def reopen(self) -> None:
        self.resolved = False
        self.resolution = None
        self.resolved_at = None
        self.resolution_decision_id = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "description": self.description,
            "raised_by": self.raised_by,
            "positions": dict(self.positions),
            "severity": self.severity.value,
            "evidence": list(self.evidence),
            "evidence_ids": list(self.evidence_ids),
            "discriminating_action": self.discriminating_action,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "resolution_decision_id": self.resolution_decision_id,
            "opened_at": self.opened_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Disagreement:
        return cls(
            id=str(value.get("id") or uuid4().hex),
            topic=str(value.get("topic", "")),
            description=str(value.get("description", "")),
            raised_by=str(value.get("raised_by", "unknown")),
            positions=dict(value.get("positions", {})),
            severity=Severity(value.get("severity", Severity.MEDIUM.value)),
            evidence=[str(item) for item in value.get("evidence", [])],
            evidence_ids=[str(item) for item in value.get("evidence_ids", [])],
            discriminating_action=str(
                value.get("discriminating_action", value.get("proposed_check", ""))
            ),
            resolved=bool(value.get("resolved", False)),
            resolution=(
                str(value["resolution"])
                if value.get("resolution") is not None
                else None
            ),
            resolution_decision_id=(
                str(value["resolution_decision_id"])
                if value.get("resolution_decision_id") is not None
                else None
            ),
            opened_at=str(value.get("opened_at") or utc_now()),
            resolved_at=(
                str(value["resolved_at"])
                if value.get("resolved_at") is not None
                else None
            ),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> Disagreement:
        return cls.from_dict(_load_object(raw))


@dataclass(slots=True)
class CostState:
    """Cumulative API and local-compute accounting for a project."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    api_cost_usd: float = 0.0
    tool_cost_usd: float = 0.0
    local_compute_cost_usd: float = 0.0
    local_compute_seconds: float = 0.0
    latency_seconds: float = 0.0
    model_calls: int = 0
    api_model_calls: int = 0
    local_model_calls: int = 0
    per_model_cost_usd: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "api_cost_usd",
            "tool_cost_usd",
            "local_compute_cost_usd",
            "local_compute_seconds",
            "latency_seconds",
            "model_calls",
            "api_model_calls",
            "local_model_calls",
        ):
            _require_nonnegative(name, getattr(self, name))
        self.per_model_cost_usd = {
            str(model): float(cost) for model, cost in self.per_model_cost_usd.items()
        }
        for cost in self.per_model_cost_usd.values():
            _require_nonnegative("per-model cost", cost)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        return self.api_cost_usd + self.tool_cost_usd + self.local_compute_cost_usd

    @property
    def total_cost(self) -> float:
        return self.total_cost_usd

    def record_usage(
        self,
        *,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        api_cost_usd: float = 0.0,
        tool_cost_usd: float = 0.0,
        local_compute_cost_usd: float = 0.0,
        local_compute_seconds: float = 0.0,
        latency_seconds: float = 0.0,
        calls: int = 1,
        api_calls: int = 0,
        local_calls: int = 0,
    ) -> None:
        values = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "api_cost_usd": api_cost_usd,
            "tool_cost_usd": tool_cost_usd,
            "local_compute_cost_usd": local_compute_cost_usd,
            "local_compute_seconds": local_compute_seconds,
            "latency_seconds": latency_seconds,
            "calls": calls,
            "api_calls": api_calls,
            "local_calls": local_calls,
        }
        for name, value in values.items():
            _require_nonnegative(name, value)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.api_cost_usd += api_cost_usd
        self.tool_cost_usd += tool_cost_usd
        self.local_compute_cost_usd += local_compute_cost_usd
        self.local_compute_seconds += local_compute_seconds
        self.latency_seconds += latency_seconds
        self.model_calls += calls
        self.api_model_calls += api_calls
        self.local_model_calls += local_calls
        if model:
            self.per_model_cost_usd[model] = (
                self.per_model_cost_usd.get(model, 0.0) + api_cost_usd
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "api_cost_usd": self.api_cost_usd,
            "tool_cost_usd": self.tool_cost_usd,
            "local_compute_cost_usd": self.local_compute_cost_usd,
            "local_compute_seconds": self.local_compute_seconds,
            "latency_seconds": self.latency_seconds,
            "model_calls": self.model_calls,
            "api_model_calls": self.api_model_calls,
            "local_model_calls": self.local_model_calls,
            "per_model_cost_usd": dict(self.per_model_cost_usd),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CostState:
        return cls(
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            cached_tokens=int(value.get("cached_tokens", 0)),
            api_cost_usd=float(value.get("api_cost_usd", 0.0)),
            tool_cost_usd=float(value.get("tool_cost_usd", 0.0)),
            local_compute_cost_usd=float(value.get("local_compute_cost_usd", 0.0)),
            local_compute_seconds=float(value.get("local_compute_seconds", 0.0)),
            latency_seconds=float(value.get("latency_seconds", 0.0)),
            model_calls=int(value.get("model_calls", 0)),
            api_model_calls=int(value.get("api_model_calls", 0)),
            local_model_calls=int(value.get("local_model_calls", 0)),
            per_model_cost_usd={
                str(model): float(cost)
                for model, cost in value.get("per_model_cost_usd", {}).items()
            },
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> CostState:
        return cls.from_dict(_load_object(raw))


@dataclass(slots=True)
class ProjectState:
    """Canonical, versioned state of one local AMCA project."""

    project_id: str
    goal: str = ""
    goal_contract: GoalContract | None = None
    current_focus: str = ""
    active_subproblem: str = ""
    accepted_facts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    evidence_records: dict[str, EvidenceRecord] = field(default_factory=dict)
    experimental_results: list[Any] = field(default_factory=list)
    decision_records: dict[str, DecisionRecord] = field(default_factory=dict)
    synchronization_checkpoints: dict[str, SynchronizationCheckpoint] = field(
        default_factory=dict
    )
    active_checkpoint_id: str | None = None
    active_disagreements: list[Disagreement] = field(default_factory=list)
    rejected_ideas: list[str] = field(default_factory=list)
    pending_ideas: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    recent_events: list[CognitiveEvent] = field(default_factory=list)
    user_constraints: list[str] = field(default_factory=list)
    model_contributions: list[dict[str, Any]] = field(default_factory=list)
    progress: float = 0.0
    current_collaboration_mode: CollaborationMode = CollaborationMode.SOLO
    current_sync_mode: SyncMode = SyncMode.ASYNC
    complexity: ComplexityLevel = ComplexityLevel.LOW
    privacy_mode: PrivacyMode = PrivacyMode.PRIVATE_HYBRID
    compute_policy: ComputePolicy = ComputePolicy.BALANCED
    participating_models: list[str] = field(default_factory=list)
    cost_state: CostState = field(default_factory=CostState)
    applied_event_ids: list[str] = field(default_factory=list)
    applied_event_digests: dict[str, str] = field(default_factory=dict)
    status: str = "active"
    version: int = 0
    cognitive_revision: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id must not be empty")
        if self.goal_contract is not None and not isinstance(
            self.goal_contract, GoalContract
        ):
            self.goal_contract = GoalContract.from_dict(self.goal_contract)
        if not self.goal and self.goal_contract is not None:
            self.goal = self.goal_contract.goal
        self.active_disagreements = [
            item if isinstance(item, Disagreement) else Disagreement.from_dict(item)
            for item in self.active_disagreements
        ]
        self.evidence_records = {
            str(key): (
                item
                if isinstance(item, EvidenceRecord)
                else EvidenceRecord.from_dict(item)
            )
            for key, item in self.evidence_records.items()
        }
        self.decision_records = {
            str(key): (
                item
                if isinstance(item, DecisionRecord)
                else DecisionRecord.from_dict(item)
            )
            for key, item in self.decision_records.items()
        }
        self.synchronization_checkpoints = {
            str(key): (
                item
                if isinstance(item, SynchronizationCheckpoint)
                else SynchronizationCheckpoint.from_dict(item)
            )
            for key, item in self.synchronization_checkpoints.items()
        }
        if any(key != item.id for key, item in self.evidence_records.items()):
            raise ValueError("evidence record map keys must match record IDs")
        if any(key != item.id for key, item in self.decision_records.items()):
            raise ValueError("decision record map keys must match record IDs")
        if any(
            key != item.id
            for key, item in self.synchronization_checkpoints.items()
        ):
            raise ValueError("checkpoint map keys must match checkpoint IDs")
        if self.active_checkpoint_id is not None:
            self.active_checkpoint_id = str(self.active_checkpoint_id)
            active = self.synchronization_checkpoints.get(
                self.active_checkpoint_id
            )
            if active is None or active.status is not CheckpointStatus.OPEN:
                raise ValueError("active checkpoint ID must identify an open checkpoint")
        self.recent_events = [
            item if isinstance(item, CognitiveEvent) else CognitiveEvent.from_dict(item)
            for item in self.recent_events
        ]
        if not isinstance(self.cost_state, CostState):
            self.cost_state = CostState.from_dict(self.cost_state)
        self.applied_event_digests = {
            str(event_id): str(digest)
            for event_id, digest in self.applied_event_digests.items()
        }
        self.current_collaboration_mode = _enum(
            CollaborationMode, self.current_collaboration_mode
        )
        self.current_sync_mode = _enum(SyncMode, self.current_sync_mode)
        self.complexity = _enum(ComplexityLevel, self.complexity)
        self.privacy_mode = _enum(PrivacyMode, self.privacy_mode)
        self.compute_policy = _enum(ComputePolicy, self.compute_policy)
        _require_nonnegative("version", self.version)
        _require_nonnegative("cognitive_revision", self.cognitive_revision)
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("progress must be between 0.0 and 1.0")

    @property
    def unresolved_disagreements(self) -> list[Disagreement]:
        return [item for item in self.active_disagreements if not item.resolved]

    @property
    def has_critical_objection(self) -> bool:
        return any(
            not item.resolved and item.severity is Severity.CRITICAL
            for item in self.active_disagreements
        )

    @property
    def goal_satisfied(self) -> bool:
        if (
            self.goal_contract is None
            or not self.goal_contract.is_satisfied()
            or self.has_critical_objection
        ):
            return False
        for disagreement in self.active_disagreements:
            if disagreement.severity is not Severity.CRITICAL:
                continue
            if not disagreement.resolved or not any(
                evidence_id in self.evidence_records
                and self.evidence_records[evidence_id].usable_for_critical_disagreement(
                    disagreement.id
                )
                for evidence_id in disagreement.evidence_ids
            ):
                return False
        for criterion in self.goal_contract.criteria:
            if not criterion.required:
                continue
            if criterion.status is CriterionStatus.WAIVED:
                if not criterion.waiver_authority:
                    return False
                continue
            if criterion.status is not CriterionStatus.PASS or not criterion.decision_id:
                return False
            decision = self.decision_records.get(criterion.decision_id)
            if decision is None or not decision.passed:
                return False
            expected_critical = {
                disagreement.id: "resolved"
                for disagreement in self.active_disagreements
                if disagreement.severity is Severity.CRITICAL
            }
            if decision.critical_disagreement_dispositions != expected_critical:
                return False
            checkpoint = self.synchronization_checkpoints.get(decision.checkpoint_id)
            if checkpoint is None or not checkpoint.permits(decision.decision_class):
                return False
            if decision.criterion_id != criterion.id:
                return False
            if set(criterion.evidence_ids) != set(decision.evidence_ids):
                return False
            if len(decision.evidence_ids) < criterion.evidence_requirement:
                return False
            if any(
                evidence_id not in self.evidence_records
                or not self.evidence_records[evidence_id].verified
                or not self.evidence_records[evidence_id].has_provenance
                or self.evidence_records[evidence_id].strength
                not in {EvidenceStrength.MODERATE, EvidenceStrength.STRONG}
                for evidence_id in decision.evidence_ids
            ):
                return False
        return True

    def evidence_record(self, evidence_id: str) -> EvidenceRecord | None:
        return self.evidence_records.get(evidence_id)

    def synchronization_checkpoint(
        self, checkpoint_id: str
    ) -> SynchronizationCheckpoint | None:
        return self.synchronization_checkpoints.get(checkpoint_id)

    def copy(self) -> ProjectState:
        return ProjectState.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "goal": self.goal,
            "goal_contract": (
                self.goal_contract.to_dict() if self.goal_contract is not None else None
            ),
            "current_focus": self.current_focus,
            "active_subproblem": self.active_subproblem,
            "accepted_facts": list(self.accepted_facts),
            "assumptions": list(self.assumptions),
            "hypotheses": list(self.hypotheses),
            "equations": list(self.equations),
            "artifacts": _json_ready(self.artifacts),
            "evidence": _json_ready(self.evidence),
            "evidence_records": {
                key: value.to_dict() for key, value in self.evidence_records.items()
            },
            "experimental_results": _json_ready(self.experimental_results),
            "decision_records": {
                key: value.to_dict() for key, value in self.decision_records.items()
            },
            "synchronization_checkpoints": {
                key: value.to_dict()
                for key, value in self.synchronization_checkpoints.items()
            },
            "active_checkpoint_id": self.active_checkpoint_id,
            "active_disagreements": [
                item.to_dict() for item in self.active_disagreements
            ],
            "rejected_ideas": list(self.rejected_ideas),
            "pending_ideas": list(self.pending_ideas),
            "open_questions": list(self.open_questions),
            "recent_events": [item.to_dict() for item in self.recent_events],
            "user_constraints": list(self.user_constraints),
            "model_contributions": _json_ready(self.model_contributions),
            "progress": self.progress,
            "current_collaboration_mode": self.current_collaboration_mode.value,
            "current_sync_mode": self.current_sync_mode.value,
            "complexity": self.complexity.value,
            "privacy_mode": self.privacy_mode.value,
            "compute_policy": self.compute_policy.value,
            "participating_models": list(self.participating_models),
            "cost_state": self.cost_state.to_dict(),
            "applied_event_ids": list(self.applied_event_ids),
            "applied_event_digests": dict(self.applied_event_digests),
            "status": self.status,
            "version": self.version,
            "cognitive_revision": self.cognitive_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProjectState:
        contract = value.get("goal_contract")
        return cls(
            project_id=str(value["project_id"]),
            goal=str(value.get("goal", "")),
            goal_contract=(
                GoalContract.from_dict(contract)
                if isinstance(contract, Mapping)
                else None
            ),
            current_focus=str(value.get("current_focus", "")),
            active_subproblem=str(value.get("active_subproblem", "")),
            accepted_facts=[str(item) for item in value.get("accepted_facts", [])],
            assumptions=[str(item) for item in value.get("assumptions", [])],
            hypotheses=[str(item) for item in value.get("hypotheses", [])],
            equations=[str(item) for item in value.get("equations", [])],
            artifacts=dict(value.get("artifacts", {})),
            evidence=list(value.get("evidence", [])),
            evidence_records={
                str(key): EvidenceRecord.from_dict(item)
                for key, item in value.get("evidence_records", {}).items()
            },
            experimental_results=list(value.get("experimental_results", [])),
            decision_records={
                str(key): DecisionRecord.from_dict(item)
                for key, item in value.get("decision_records", {}).items()
            },
            synchronization_checkpoints={
                str(key): SynchronizationCheckpoint.from_dict(item)
                for key, item in value.get(
                    "synchronization_checkpoints", {}
                ).items()
            },
            active_checkpoint_id=(
                str(value["active_checkpoint_id"])
                if value.get("active_checkpoint_id") is not None
                else None
            ),
            active_disagreements=[
                Disagreement.from_dict(item)
                for item in value.get("active_disagreements", [])
            ],
            rejected_ideas=[str(item) for item in value.get("rejected_ideas", [])],
            pending_ideas=[str(item) for item in value.get("pending_ideas", [])],
            open_questions=[str(item) for item in value.get("open_questions", [])],
            recent_events=[
                CognitiveEvent.from_dict(item)
                for item in value.get("recent_events", [])
            ],
            user_constraints=[
                str(item) for item in value.get("user_constraints", [])
            ],
            model_contributions=[
                dict(item) for item in value.get("model_contributions", [])
            ],
            progress=float(value.get("progress", 0.0)),
            current_collaboration_mode=CollaborationMode(
                value.get("current_collaboration_mode", CollaborationMode.SOLO.value)
            ),
            current_sync_mode=SyncMode(
                value.get("current_sync_mode", SyncMode.ASYNC.value)
            ),
            complexity=ComplexityLevel(
                value.get("complexity", ComplexityLevel.LOW.value)
            ),
            privacy_mode=PrivacyMode(
                value.get("privacy_mode", PrivacyMode.PRIVATE_HYBRID.value)
            ),
            compute_policy=ComputePolicy(
                value.get("compute_policy", ComputePolicy.BALANCED.value)
            ),
            participating_models=[
                str(item) for item in value.get("participating_models", [])
            ],
            cost_state=CostState.from_dict(value.get("cost_state", {})),
            applied_event_ids=[
                str(item) for item in value.get("applied_event_ids", [])
            ],
            applied_event_digests={
                str(event_id): str(digest)
                for event_id, digest in value.get("applied_event_digests", {}).items()
            },
            status=str(value.get("status", "active")),
            version=int(value.get("version", 0)),
            cognitive_revision=int(value.get("cognitive_revision", 0)),
            created_at=str(value.get("created_at") or utc_now()),
            updated_at=str(value.get("updated_at") or utc_now()),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, raw: str | bytes | bytearray) -> ProjectState:
        return cls.from_dict(_load_object(raw))


__all__ = [
    "CollaborationMode",
    "SyncMode",
    "ComplexityLevel",
    "PrivacyMode",
    "ComputePolicy",
    "EventType",
    "Severity",
    "EvaluatorKind",
    "CriterionStatus",
    "DecisionClass",
    "EvidenceKind",
    "EvidenceStrength",
    "VerificationStatus",
    "ParticipantDispositionStatus",
    "CheckpointStatus",
    "GoalCriterion",
    "GoalContract",
    "CognitiveEvent",
    "EvidenceRecord",
    "ParticipantDisposition",
    "SynchronizationCheckpoint",
    "DecisionRecord",
    "Disagreement",
    "CostState",
    "ProjectState",
]
