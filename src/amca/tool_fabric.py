"""T1.1 Unified Permissioned Tool Fabric foundation.

One canonical permission/capability layer over AMCA's separately governed
bounded capabilities:

    ToolRequest -> Capability Registry -> Policy Evaluation
        -> ALLOW / DENY / REQUIRE_APPROVAL
        -> existing bounded implementation
        -> ToolExecutionRecord

Truth rules:
- approval is capability-specific, never one global boolean;
- AUTHORIZED is never mistaken for SUCCEEDED;
- an interrupted side-effecting execution is preserved as outcome-uncertain
  and is NEVER automatically replayed;
- secrets are stripped from persisted arguments;
- tool telemetry is separate from model inference counts;
- canonical amca.db only - no parallel permissions/tools database.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:16]}"


class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    COMPUTE_ONLY = "compute_only"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_OR_EXTERNAL = "irreversible_or_external"
    SENSITIVE = "sensitive"


class SideEffectClass(str, Enum):
    NONE = "none"
    LOCAL_READ = "local_read"
    LOCAL_WRITE = "local_write"
    PROCESS_EXECUTION = "process_execution"
    NETWORK = "network"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Reason(str, Enum):
    POLICY_ALLOW = "policy_allow"
    OUTSIDE_PROJECT_SCOPE = "outside_project_scope"
    SENSITIVE_RESOURCE = "sensitive_resource"
    SIDE_EFFECT_REQUIRES_APPROVAL = "side_effect_requires_approval"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_UNKNOWN = "tool_unknown"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    BUDGET_OR_LIMIT = "budget_or_limit"


class ExecutionStatus(str, Enum):
    REQUESTED = "requested"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED_AFTER_START = "interrupted_after_start"


SENSITIVE_PATH_PATTERNS = (
    ".env", ".env.", "credential", "secret", "token",
    ".pem", ".key", "id_rsa", "id_ed25519", ".p12", ".pfx",
)


def is_sensitive_resource(resource: str | None) -> bool:
    if not resource:
        return False
    lowered = PurePosixPath(resource.replace("\\", "/")).name.lower()
    return any(pattern in lowered for pattern in SENSITIVE_PATH_PATTERNS)


def normalize_scope(project_root: str, requested: str | None) -> tuple[str | None, bool]:
    """Normalize a resource path into the project scope.

    Returns (normalized_relative_path_or_None, inside_scope). Absolute paths,
    traversal escapes, and machine-wide scopes are OUTSIDE the project scope.
    """

    if not requested:
        return None, True
    candidate = requested.replace("\\", "/")
    pure = PurePosixPath(candidate)
    if pure.is_absolute() or (len(candidate) > 1 and candidate[1] == ":"):
        return None, False
    parts: list[str] = []
    for part in pure.parts:
        if part in (".", ""):
            continue
        if part == "..":
            if not parts:
                return None, False
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        return None, True
    normalized = "/".join(parts)
    root_name = PurePosixPath(project_root.replace("\\", "/")).name.lower()
    if parts[0].lower() == root_name and root_name:
        normalized = "/".join(parts[1:]) or "."
    return normalized, True


@dataclass(slots=True)
class ToolCapability:
    tool_id: str
    version: str
    category: str
    description: str
    risk_class: RiskClass
    side_effect_class: SideEffectClass
    supported_operations: tuple[str, ...] = ()
    resource_scopes: tuple[str, ...] = ("project",)
    approval_policy: Decision = Decision.ALLOW
    execution_backend: str | None = None


@dataclass(slots=True)
class ToolRequest:
    request_id: str
    organization_id: str
    project_id: str
    tool_id: str
    operation: str
    normalized_arguments: dict[str, Any]
    requested_resource_scope: str | None = None
    actor: str | None = None
    created_at: str = field(default_factory=_now)


@dataclass(slots=True)
class ToolDecisionResult:
    decision: Decision
    reason: Reason


@dataclass(slots=True)
class ToolExecutionRecord:
    execution_id: str
    request_id: str
    tool_id: str
    operation: str
    decision: Decision
    status: ExecutionStatus
    started_at: str | None = None
    completed_at: str | None = None
    approval_reference: str | None = None
    resource_scope: str | None = None
    result_summary: str | None = None
    side_effect_summary: str | None = None
    outcome_uncertain: int = 0
    # Work Order 09 section 2.3: recovery must decide from execution EVIDENCE
    # rather than reassembling it from separate places.  Effect class and
    # replay safety come from the Tool Fabric's authority table, so this record
    # is self-sufficient: authorization, approval, effect, replay safety,
    # status, result and the artifacts produced all travel together.
    effect_class: str = ""
    replay_safe: bool = False
    artifact_versions: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "request_id": self.request_id,
            "tool_id": self.tool_id,
            "operation": self.operation,
            "decision": self.decision.value if isinstance(self.decision, Decision) else self.decision,
            "status": self.status.value if isinstance(self.status, ExecutionStatus) else self.status,
            "approval_reference": self.approval_reference,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "resource_scope": self.resource_scope,
            "result_summary": self.result_summary,
            "side_effect_summary": self.side_effect_summary,
            "outcome_uncertain": self.outcome_uncertain,
            "effect_class": self.effect_class,
            "replay_safe": self.replay_safe,
            "artifact_versions": [list(item) for item in self.artifact_versions],
        }


class DuplicateToolRegistration(ValueError):
    pass


class ToolRegistry:
    """Stable capability registry with ID/version conflict handling."""

    def __init__(self) -> None:
        self._capabilities: dict[str, list[ToolCapability]] = {}

    def register(self, capability: ToolCapability) -> ToolCapability:
        versions = {c.version for c in self._capabilities.get(capability.tool_id, ())}
        if capability.version in versions:
            raise DuplicateToolRegistration(
                f"tool {capability.tool_id!r} version "
                f"{capability.version!r} already registered"
            )
        self._capabilities.setdefault(capability.tool_id, []).append(capability)
        return capability

    def get(self, tool_id: str, version: str | None = None) -> ToolCapability | None:
        caps = self._capabilities.get(tool_id)
        if not caps:
            return None
        if version is None:
            return caps[-1]
        for cap in caps:
            if cap.version == version:
                return cap
        return None

    def all_capabilities(self) -> list[ToolCapability]:
        return [c for caps in self._capabilities.values() for c in caps]

    def __len__(self) -> int:
        return sum(len(caps) for caps in self._capabilities.values())


class ProjectPolicyDenied(KeyError):
    pass


DEFAULT_POLICY: dict[str, Decision] = {
    # read-only contained inspection / bounded pure computation
    "filesystem.read": Decision.ALLOW,
    "math.evaluate": Decision.ALLOW,
    "python.bounded": Decision.REQUIRE_APPROVAL,
    "artifact.export": Decision.REQUIRE_APPROVAL,
    # local mutation / process execution require explicit approval
    "engineering.patch.apply": Decision.REQUIRE_APPROVAL,
    "engineering.test.run": Decision.REQUIRE_APPROVAL,
    "git.stage": Decision.REQUIRE_APPROVAL,
    "git.commit": Decision.REQUIRE_APPROVAL,
}


class ToolPolicyEngine:
    """Evaluates requests against safe defaults plus project overrides."""

    def __init__(self, *, project_root: str = "project") -> None:
        self.project_root = project_root

    def evaluate(
        self,
        request: ToolRequest,
        capability: ToolCapability | None,
        *,
        project_overrides: dict[str, Decision] | None = None,
        invocation_count: int | None = None,
        invocation_limit: int | None = None,
    ) -> ToolDecisionResult:
        if capability is None:
            return ToolDecisionResult(Decision.DENY, Reason.TOOL_UNKNOWN)

        if invocation_limit is not None and (
            invocation_count is None or invocation_count >= invocation_limit
        ):
            return ToolDecisionResult(Decision.DENY, Reason.BUDGET_OR_LIMIT)

        if capability.supported_operations and (
            request.operation not in capability.supported_operations
        ):
            return ToolDecisionResult(Decision.DENY, Reason.OPERATION_NOT_ALLOWED)

        _, inside = normalize_scope(self.project_root,
                                    request.requested_resource_scope)
        if not inside:
            if is_sensitive_resource(request.requested_resource_scope):
                return ToolDecisionResult(Decision.DENY, Reason.SENSITIVE_RESOURCE)
            return ToolDecisionResult(Decision.DENY, Reason.OUTSIDE_PROJECT_SCOPE)

        if is_sensitive_resource(request.requested_resource_scope):
            # Sensitive resources are DENY by default. An explicit project
            # ALLOW override still only escalates to approval - never to
            # silent access.
            if (project_overrides or {}).get(request.tool_id) is Decision.ALLOW:
                return ToolDecisionResult(Decision.REQUIRE_APPROVAL,
                                          Reason.SENSITIVE_RESOURCE)
            return ToolDecisionResult(Decision.DENY, Reason.SENSITIVE_RESOURCE)

        decision = (project_overrides or {}).get(request.tool_id)
        if decision is None:
            decision = DEFAULT_POLICY.get(request.tool_id, capability.approval_policy)
        # Network capabilities are special: even an explicit project enable
        # only escalates to approval - silent network access never happens.
        if capability.side_effect_class == SideEffectClass.NETWORK:
            if decision is Decision.ALLOW:
                return ToolDecisionResult(Decision.REQUIRE_APPROVAL,
                                          Reason.SIDE_EFFECT_REQUIRES_APPROVAL)
            return ToolDecisionResult(Decision.DENY, Reason.TOOL_NOT_ALLOWED)
        if decision is Decision.DENY:
            return ToolDecisionResult(Decision.DENY, Reason.TOOL_NOT_ALLOWED)
        if decision is Decision.REQUIRE_APPROVAL:
            return ToolDecisionResult(Decision.REQUIRE_APPROVAL,
                                      Reason.SIDE_EFFECT_REQUIRES_APPROVAL)
        return ToolDecisionResult(Decision.ALLOW, Reason.POLICY_ALLOW)


class ToolFabricStore:
    """Durable requests/executions/policies in the CANONICAL amca.db."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tool_fabric_requests (
                    request_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    normalized_arguments_json TEXT NOT NULL,
                    requested_resource_scope TEXT,
                    actor TEXT,
                    created_at TEXT NOT NULL
                )""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tool_fabric_executions (
                    execution_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_reference TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    resource_scope TEXT,
                    result_summary TEXT,
                    side_effect_summary TEXT,
                    outcome_uncertain INTEGER NOT NULL DEFAULT 0
                )""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tool_fabric_project_policies (
                    project_id TEXT NOT NULL,
                    tool_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    PRIMARY KEY (project_id, tool_id)
                )""")

    def save_request(self, request: ToolRequest,
                     arguments: dict[str, Any]) -> None:
        import json as _json

        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO tool_fabric_requests
                   (request_id, organization_id, project_id, tool_id, operation,
                    normalized_arguments_json, requested_resource_scope, actor,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request.request_id, request.organization_id,
                 request.project_id, request.tool_id, request.operation,
                 _json.dumps(arguments, sort_keys=True),
                 request.requested_resource_scope, request.actor,
                 request.created_at),
            )

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        import json as _json

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_fabric_requests WHERE request_id = ?",
                (request_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["normalized_arguments"] = _json.loads(
            record.pop("normalized_arguments_json"))
        return record

    def save_execution(self, record: ToolExecutionRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO tool_fabric_executions
                   (execution_id, request_id, tool_id, operation, decision,
                    status, approval_reference, started_at, completed_at,
                    resource_scope, result_summary, side_effect_summary,
                    outcome_uncertain)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.execution_id, record.request_id, record.tool_id,
                 record.operation,
                 record.decision.value if isinstance(record.decision, Decision) else record.decision,
                 record.status.value if isinstance(record.status, ExecutionStatus) else record.status,
                 record.approval_reference, record.started_at,
                 record.completed_at, record.resource_scope,
                 record.result_summary, record.side_effect_summary,
                 record.outcome_uncertain),
            )

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_fabric_executions WHERE execution_id = ?",
                (execution_id,)).fetchone()
        return dict(row) if row else None

    def running_executions(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_fabric_executions WHERE status = 'running'"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_project_policy(self, project_id: str,
                           decisions: dict[str, Decision]) -> None:
        with self._connect() as connection:
            for tool_id, decision in decisions.items():
                connection.execute(
                    """INSERT OR REPLACE INTO tool_fabric_project_policies
                       (project_id, tool_id, decision)
                       VALUES (?, ?, ?)""",
                    (project_id, tool_id,
                     decision.value if isinstance(decision, Decision) else decision),
                )

    def get_project_policy(self, project_id: str) -> dict[str, Decision]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_id, decision FROM tool_fabric_project_policies "
                "WHERE project_id = ?", (project_id,)).fetchall()
        return {
            row["tool_id"]: Decision(row["decision"]) for row in rows
        }

    def apply_restart_recovery(self) -> dict[str, int]:
        """Startup pass distinguishing AUTHORIZED-not-started from
        INTERRUPTED_AFTER_START. Neither is auto-replayed; only executions
        that actually started carry outcome uncertainty."""
        with self._connect() as connection:
            interrupted = connection.execute(
                """UPDATE tool_fabric_executions
                   SET status = 'interrupted_after_start', outcome_uncertain = 1
                   WHERE status = 'running'"""
            ).rowcount
            authorized_not_started = connection.execute(
                """SELECT COUNT(*) c FROM tool_fabric_executions
                   WHERE status = 'authorized'"""
            ).fetchone()["c"]
        return {"interrupted_after_start": interrupted,
                "authorized_not_started": authorized_not_started}


def strip_secrets(arguments: dict[str, Any]) -> dict[str, Any]:
    """Remove secret-bearing values before persisting request arguments."""

    cleaned: dict[str, Any] = {}
    for key, value in sorted(arguments.items()):
        lowered = str(key).lower()
        if any(token in lowered for token in
               ("secret", "token", "key", "password", "auth", "credential")):
            cleaned[str(key)] = "[stripped]"
        else:
            cleaned[str(key)] = value
    return cleaned


class ToolFabric:
    """Facade: registry + policy + durable requests/executions."""

    def __init__(self, store: ToolFabricStore, *,
                 project_root: str = "project",
                 registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()
        self.store = store
        self.policy = ToolPolicyEngine(project_root=project_root)
        self._backends: dict[str, Callable[..., dict[str, Any]]] = {}

    def register_capability(self, capability: ToolCapability,
                            backend: Callable[..., dict[str, Any]] | None = None) -> None:
        self.registry.register(capability)
        if backend is not None:
            self._backends[capability.tool_id] = backend

    def set_backend(self, tool_id: str, backend) -> None:
        """Bind (or replace) the execution backend for one tool id."""

        self._backends[tool_id] = backend

    def invocation_count(self, tool_id: str) -> int:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) c FROM tool_fabric_executions WHERE tool_id = ?",
                (tool_id,)).fetchone()
        return int(row["c"])

    def submit(self, request: ToolRequest,
               arguments: dict[str, Any] | None = None) -> tuple[ToolDecisionResult, ToolExecutionRecord]:
        capability = self.registry.get(request.tool_id)
        decision_result = self.policy.evaluate(
            request, capability,
            project_overrides=self.store.get_project_policy(request.project_id),
            invocation_count=self.invocation_count(request.tool_id),
        )
        self.store.save_request(request, strip_secrets(arguments or {}))
        status = {
            Decision.ALLOW: ExecutionStatus.AUTHORIZED,
            Decision.DENY: ExecutionStatus.DENIED,
            Decision.REQUIRE_APPROVAL: ExecutionStatus.REQUESTED,
        }[decision_result.decision]
        # Carry the Tool Fabric's authoritative effect metadata onto the record,
        # so recovery reads replay safety from execution evidence rather than
        # asking a separate table later.
        from .tool_effects import effect_for

        effect = effect_for(request.tool_id)
        record = ToolExecutionRecord(
            execution_id=_uuid("exec"),
            request_id=request.request_id,
            tool_id=request.tool_id,
            operation=request.operation,
            decision=decision_result.decision,
            status=status,
            resource_scope=request.requested_resource_scope,
            effect_class=str(
                getattr(effect.get("effect_class"), "value", effect.get("effect_class", ""))
            ),
            replay_safe=bool(effect.get("replay_safe", False)),
        )
        self.store.save_execution(record)
        return decision_result, record

    def approve(self, execution_id: str, approval_reference: str) -> dict[str, Any] | None:
        record_row = self.store.get_execution(execution_id)
        if record_row is None:
            return None
        record = ToolExecutionRecord(**{**record_row,
            "decision": Decision(record_row["decision"]),
            "status": ExecutionStatus(record_row["status"])})
        if record.status is not ExecutionStatus.REQUESTED:
            raise ValueError(f"execution {execution_id} is not awaiting approval")
        record.status = ExecutionStatus.AUTHORIZED
        record.approval_reference = approval_reference
        self.store.save_execution(record)
        return record.to_dict()

    def execute(self, execution_id: str) -> dict[str, Any] | None:
        row = self.store.get_execution(execution_id)
        if row is None:
            return None
        decision = Decision(row["decision"])
        status = ExecutionStatus(row["status"])
        if decision == Decision.DENY or status in (
                ExecutionStatus.FAILED, ExecutionStatus.SUCCEEDED):
            raise ValueError("execution is not runnable")
        if status is ExecutionStatus.RUNNING:
            raise ValueError("execution is already running")
        if status is not ExecutionStatus.AUTHORIZED:
            # REQUIRE_APPROVAL lands in REQUESTED.  It becomes runnable only
            # through approve(), never by calling execute() directly.
            raise ValueError("execution requires an approval before it can run")
        capability = self.registry.get(row["tool_id"])
        backend = self._backends.get(row["tool_id"])
        record = ToolExecutionRecord(**{**row,
            "decision": decision, "status": ExecutionStatus.RUNNING})
        record.started_at = _now()
        self.store.save_execution(record)
        try:
            if backend is None:
                raise ValueError("no execution backend registered")
            result = backend()
            summary = str(result.get("summary", ""))[:400]
            record.status = ExecutionStatus.SUCCEEDED
            record.result_summary = summary
            record.side_effect_summary = str(result.get("side_effect_summary", ""))
        except Exception as exc:
            record.status = ExecutionStatus.FAILED
            record.result_summary = f"{type(exc).__name__}: {exc}"[:200]
        record.completed_at = _now()
        self.store.save_execution(record)
        return record.to_dict()


def register_standard_capabilities(registry: ToolRegistry) -> None:
    """Representative descriptors over EXISTING bounded capabilities.

    These wrap/represent permissions only - execution backends stay the
    already-approved implementations (E1 boundaries are not bypassed).
    """

    descriptors = [
        ToolCapability("math.evaluate", "1.0.0", "compute",
                       "Restricted symbolic math evaluation",
                       RiskClass.COMPUTE_ONLY, SideEffectClass.NONE,
                       ("evaluate",), ("project",), Decision.ALLOW),
        ToolCapability("python.bounded", "1.0.0", "compute",
                       "Bounded Python execution sandbox (not proven side-effect free)",
                       RiskClass.IRREVERSIBLE_OR_EXTERNAL,
                       SideEffectClass.PROCESS_EXECUTION,
                       ("execute",), ("project",), Decision.REQUIRE_APPROVAL),
        ToolCapability("filesystem.read", "1.0.0", "inspection",
                       "Contained project file inspection",
                       RiskClass.READ_ONLY, SideEffectClass.LOCAL_READ,
                       ("read",), ("project",), Decision.ALLOW),
        ToolCapability("artifact.export", "1.0.0", "export",
                       "Export artifacts to sanctioned roots (creates local files)",
                       RiskClass.REVERSIBLE_WRITE, SideEffectClass.LOCAL_WRITE,
                       ("export",), ("artifact",), Decision.REQUIRE_APPROVAL),
        ToolCapability("engineering.patch.apply", "1.0.0", "engineering",
                       "Apply reviewed patch to workspace files",
                       RiskClass.REVERSIBLE_WRITE, SideEffectClass.LOCAL_WRITE,
                       ("apply",), ("workspace",), Decision.REQUIRE_APPROVAL),
        ToolCapability("engineering.test.run", "1.0.0", "engineering",
                       "Run project test/build command set",
                       RiskClass.IRREVERSIBLE_OR_EXTERNAL,
                       SideEffectClass.PROCESS_EXECUTION,
                       ("run",), ("workspace",), Decision.REQUIRE_APPROVAL),
        ToolCapability("git.stage", "1.0.0", "git",
                       "Stage selected files for commit",
                       RiskClass.REVERSIBLE_WRITE, SideEffectClass.LOCAL_WRITE,
                       ("stage",), ("workspace",), Decision.REQUIRE_APPROVAL),
        ToolCapability("git.commit", "1.0.0", "git",
                       "Create a reviewed local commit",
                       RiskClass.IRREVERSIBLE_OR_EXTERNAL,
                       SideEffectClass.LOCAL_WRITE,
                       ("commit",), ("workspace",), Decision.REQUIRE_APPROVAL),
    ]
    for capability in descriptors:
        registry.register(capability)
