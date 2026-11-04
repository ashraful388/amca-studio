"""M3 governed Nano Agent foundation (bridge over the existing Agent Fabric).

Additive module introduced by the M3 Nano Agent Runtime Foundation milestone
(zcode/amca-v0.5.0-m3-nano-agent-runtime-foundation-01).

Critical architecture rule honored: this module EVOLVES AND BRIDGES the
existing v0.4.x Agent Fabric runtime instead of building a competing system.
Reused verbatim (imported read-only, files untouched):

- ``amca.agent_fabric.AgentGovernor``  — the hard deterministic authorization
  arithmetic (logical agents, concurrency, manager depth, API budget,
  recursive-spawning policy);
- ``amca.agent_fabric.ResourceEnvelope`` / ``AgentFabricSettings`` — the
  existing resource-envelope and limits model (max_nano_tasks, concurrency,
  depth, budget ceilings);
- ``amca.agent_runtime.AgentTaskStatus`` + ``ALLOWED_TRANSITIONS`` — the
  existing audited lifecycle names and single allowed transition map;
- ``amca.agent_fabric.AgentClass.NANO`` / ``amca.agent_runtime.ExecutorKind``
  — existing identity/class terminology.

Adapted here under the approved M0 intelligence contracts: a versioned
``NanoSpawnProposal`` (a request, never an authorization) is validated by a
governor front that combines the existing ``AgentGovernor`` arithmetic with
M3 bounds (children per parent, total per run, capability intersection,
permission intersection, cancelled parent/run refusal, duplicate identity)
before any ``NanoAssignment`` is sealed; a per-run in-memory registry keeps
hierarchy (no orphan Nano Agents), typed communication, terminal cancellation
with descendant propagation, scope-checked results, and escalation records
that grant no authority.

Import-isolated as in M0-M2: nothing in the pre-existing application imports
this package, the nano_agents gate remains FALSE, the production Research
Gateway is untouched, and tests may instantiate the foundation directly.
"""

import dataclasses
import enum
from typing import Any, ClassVar, Mapping, Optional, Tuple

from ..agent_fabric import (
    AgentClass,
    AgentFabricSettings,
    AgentGovernor,
    ResourceEnvelope,
)
from ..agent_runtime import AgentTaskStatus
from .contracts import (
    Contract,
    ContractError,
    ContractErrorCode,
    ContractMetadata,
    AgentCompletionDisposition,
    contract_post_init,
)
from .gates import FeatureGateRegistry


# ---------------------------------------------------------------------------
# Existing-runtime bridges (REUSED — names keep the approved terminology)
# ---------------------------------------------------------------------------

NANO_AGENT_CLASS = AgentClass.NANO
# Direct reference: the nano foundation REUSES the existing audited lifecycle
# map instead of deriving its own.
NANO_LIFECYCLE_TRANSITIONS = AgentTaskStatus.ALLOWED_TRANSITIONS


def require_nano_gate(gates: FeatureGateRegistry) -> None:
    """Refuse the spawn path while the nano_agents gate is disabled.

    The shipped production default is OFF, so the normal selection path can
    never spawn Nano Agents; direct instantiation for tests remains possible.
    """

    if not gates.is_enabled("nano_agents"):
        raise ContractError(
            ContractErrorCode.INVALID,
            "nano_agents requested while the nano_agents gate is disabled; "
            "refusing instead of activating Nano Agents",
        )


# ---------------------------------------------------------------------------
# Versioned spawn-proposal and assignment envelopes (M0 contract style)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NanoSpawnProposal(Contract):
    """A parent's bounded REQUEST for Nano children — never an authorization.

    Fields carry everything the governor needs to validate: purpose/type,
    requested child count, capabilities, resource limits, depth, expected
    cost/work, and the parent/correlation identity that keeps every Nano
    Agent inside a task/run hierarchy (no orphans).
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_spawn_proposal"

    meta: ContractMetadata
    proposal_id: str
    run_ref: str
    parent_task_id: str
    parent_depth: int
    task_purpose: str
    task_type: str
    requested_child_count: int
    capabilities: Tuple[str, ...]
    permissions: Tuple[str, ...]
    expected_cost: float
    correlation_id: str

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if isinstance(self.parent_depth, bool) or not isinstance(
            self.parent_depth, int
        ) or self.parent_depth < 0:
            raise ContractError(
                ContractErrorCode.INVALID, "parent_depth must be >= 0"
            )
        if isinstance(self.requested_child_count, bool) or not isinstance(
            self.requested_child_count, int
        ) or self.requested_child_count < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "requested_child_count must be a positive integer",
            )
        if isinstance(self.expected_cost, bool) or not isinstance(
            self.expected_cost, (int, float)
        ):
            raise ContractError(
                ContractErrorCode.INVALID, "expected_cost must be a number"
            )
        value = float(self.expected_cost)
        if value != value or value in (float("inf"), float("-inf")) or value < 0:
            raise ContractError(
                ContractErrorCode.INVALID,
                "expected_cost must be finite and non-negative",
            )


@dataclasses.dataclass(frozen=True)
class NanoAssignment(Contract):
    """The immutable, execution-authorized form of an approved spawn.

    Built only by :meth:`NanoRunRegistry.authorize_spawn` after governor
    approval; capabilities and permissions are the intersection of the
    request with the parent's authority, so authority can only stay equal
    or shrink.
    """

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_assignment"

    meta: ContractMetadata
    nano_id: str
    task_id: str
    run_ref: str
    parent_task_id: str
    depth: int
    purpose: str
    task_type: str
    capabilities: Tuple[str, ...]
    permissions: Tuple[str, ...]
    expected_cost: float
    correlation_id: str
    proposal_ref: str
    assignment_version: int

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.assignment_version < 1:
            raise ContractError(
                ContractErrorCode.INVALID,
                "assignment_version must be positive",
            )
        if not self.parent_task_id or not self.run_ref:
            raise ContractError(
                ContractErrorCode.INVALID,
                "a Nano Agent must always belong to a task/run hierarchy",
            )


@dataclasses.dataclass(frozen=True)
class NanoGovernorDecision(Contract):
    """Reason-coded governor verdict for one spawn proposal."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_governor_decision"

    meta: ContractMetadata
    proposal_id: str
    authorized: bool
    reason_code: str
    checks: Mapping[str, Any]
    envelope_snapshot: Mapping[str, Any]
    governor_layer: str = "existing_agent_governor_bridge"

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.authorized and self.reason_code != "authorized":
            raise ContractError(
                ContractErrorCode.INVALID,
                "an authorized decision must use the authorized reason code",
            )
        if not self.authorized and self.reason_code == "authorized":
            raise ContractError(
                ContractErrorCode.INVALID,
                "a rejected decision must carry a rejection reason code",
            )


# ---------------------------------------------------------------------------
# Typed communication and escalation (no authority transfer)
# ---------------------------------------------------------------------------


class NanoMessageKind(enum.Enum):
    CLAIM = "claim"
    EVIDENCE = "evidence"
    OBJECTION = "objection"
    QUESTION = "question"
    STATUS = "status"
    RESULT_PROPOSAL = "result_proposal"
    CANCELLATION_ACK = "cancellation_ack"
    ESCALATION_REQUEST = "escalation_request"


@dataclasses.dataclass(frozen=True)
class NanoMessage(Contract):
    """Typed, attributable Nano communication (no free-form authority)."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_message"

    meta: ContractMetadata
    sender_id: str
    recipient_id: str
    kind: NanoMessageKind
    correlation_id: str
    summary: str

    def __post_init__(self):
        contract_post_init(self)


@dataclasses.dataclass(frozen=True)
class NanoEscalationRequest(Contract):
    """A routed escalation record. Grants no authority by construction."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_escalation_request"

    meta: ContractMetadata
    nano_id: str
    reason: str
    requested_authority: str
    granted: bool = False

    def __post_init__(self):
        contract_post_init(self)

    def _validate(self):
        if self.granted:
            raise ContractError(
                ContractErrorCode.INVALID,
                "escalation records never grant authority",
            )


@dataclasses.dataclass(frozen=True)
class NanoResultRecord(Contract):
    """A completion result with a truthful disposition and scoped claims."""

    CONTRACT_NAME: ClassVar[str] = "amca.intelligence.nano_result_record"

    meta: ContractMetadata
    nano_id: str
    disposition: AgentCompletionDisposition
    summary: str
    output_digest: str
    capabilities_used: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]

    def __post_init__(self):
        contract_post_init(self)


# ---------------------------------------------------------------------------
# Per-run registry: governor front, hierarchy, cancellation, results
# ---------------------------------------------------------------------------


class NanoRunRegistry:
    """In-memory per-run Nano ledger binding the existing runtime structures.

    Keeps the whole-run hierarchy (no orphan agents), enforces the governor
    bounds, tracks cancelled agents with descendant propagation, accepts
    typed messages/escalations, and validates results against assignment
    scope. Purely in-memory and side-effect free: creating a registry never
    touches production execution.
    """

    def __init__(
        self,
        *,
        meta_factory,
        run_ref: str,
        envelope: Optional[ResourceEnvelope] = None,
        settings: Optional[AgentFabricSettings] = None,
        allowed_capabilities: Tuple[str, ...] = (),
        max_children_per_parent: Optional[int] = None,
    ) -> None:
        self.envelope = envelope if envelope is not None else ResourceEnvelope()
        self.settings = (
            settings if settings is not None else AgentFabricSettings(envelope=self.envelope)
        )
        self.allowed_capabilities = tuple(allowed_capabilities)
        self.max_children_per_parent = (
            self.envelope.max_nano_tasks
            if max_children_per_parent is None
            else max_children_per_parent
        )
        self.run_cancelled = False
        self._meta_factory = meta_factory
        self._assignments: dict[str, NanoAssignment] = {}
        self._statuses: dict[str, str] = {}
        self._results: dict[str, NanoResultRecord] = {}
        self._children_by_parent: dict[str, int] = {}
        self._decisions: dict[str, NanoGovernorDecision] = {}
        self._escalations: list[NanoEscalationRequest] = []
        self._messages: list[NanoMessage] = []

    # -- spawn authorization ------------------------------------------------

    def propose_spawn(
        self,
        proposal: NanoSpawnProposal,
        *,
        parent_permissions: Tuple[str, ...],
        gates: FeatureGateRegistry,
    ) -> NanoGovernorDecision:
        """Validate one spawn proposal; a request is not an authorization."""

        checks, authorized, reason_code = self._evaluate_proposal(
            proposal, parent_permissions=parent_permissions, gates=gates
        )
        decision = NanoGovernorDecision(
            meta=self._meta_factory(f"gov-{proposal.proposal_id}"),
            proposal_id=proposal.proposal_id,
            authorized=authorized,
            reason_code=reason_code,
            checks=checks,
            envelope_snapshot=self.envelope.to_dict()
            if hasattr(self.envelope, "to_dict")
            else {},
        )
        self._decisions[proposal.proposal_id] = decision
        return decision

    def authorize_spawn(
        self,
        proposal: NanoSpawnProposal,
        *,
        parent_permissions: Tuple[str, ...],
        gates: FeatureGateRegistry,
    ) -> NanoAssignment:
        """Seal an immutable assignment from an authorized spawn request."""

        checks, authorized, reason_code = self._evaluate_proposal(
            proposal, parent_permissions=parent_permissions, gates=gates
        )
        if not authorized:
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"spawn proposal {proposal.proposal_id!r} was not authorized: "
                f"{reason_code}",
            )
        # Authority can only stay equal or shrink: the assignment carries the
        # intersection of requested and parent permissions.
        granted_permissions = tuple(
            permission
            for permission in proposal.permissions
            if permission in parent_permissions
        )
        assignment = NanoAssignment(
            meta=self._meta_factory(f"assign-{proposal.proposal_id}"),
            nano_id=f"nano:{proposal.proposal_id}",
            task_id=f"task:{proposal.proposal_id}",
            run_ref=proposal.run_ref,
            parent_task_id=proposal.parent_task_id,
            depth=proposal.parent_depth + 1,
            purpose=proposal.task_purpose,
            task_type=proposal.task_type,
            capabilities=tuple(proposal.capabilities),
            permissions=granted_permissions,
            expected_cost=float(proposal.expected_cost),
            correlation_id=proposal.correlation_id,
            proposal_ref=proposal.proposal_id,
            assignment_version=1,
        )
        self._assignments[assignment.nano_id] = assignment
        self._statuses[assignment.nano_id] = AgentTaskStatus.PROPOSED
        self._children_by_parent[proposal.parent_task_id] = (
            self._children_by_parent.get(proposal.parent_task_id, 0)
            + proposal.requested_child_count
        )
        return assignment

    def _evaluate_proposal(
        self,
        proposal: NanoSpawnProposal,
        *,
        parent_permissions: Tuple[str, ...],
        gates: FeatureGateRegistry,
    ) -> Tuple[dict, bool, str]:
        """Run every governor check once and return (checks, authorized, reason)."""

        require_nano_gate(gates)
        checks: dict[str, bool] = {}
        if self.run_cancelled:
            checks["run_active"] = False
        else:
            checks["run_active"] = True
        parent_cancelled = self._is_cancelled_lineage(proposal.parent_task_id)
        checks["parent_active"] = not parent_cancelled
        checks["unique_identity"] = proposal.proposal_id not in self._decisions and not any(
            assignment.proposal_ref == proposal.proposal_id
            for assignment in self._assignments.values()
        )
        checks["children_per_parent"] = self._children_by_parent.get(
            proposal.parent_task_id, 0
        ) + proposal.requested_child_count <= self.max_children_per_parent
        checks["run_total"] = len(self._assignments) + proposal.requested_child_count <= (
            self.envelope.max_nano_tasks
        )
        checks["depth"] = proposal.parent_depth + 1 <= self.envelope.max_manager_depth
        checks["concurrency"] = (
            proposal.requested_child_count <= self.envelope.max_concurrent_agents
        )
        checks["budget"] = proposal.expected_cost <= self.envelope.max_api_cost_usd
        if self.allowed_capabilities:
            checks["capabilities_allowed"] = all(
                capability in self.allowed_capabilities
                for capability in proposal.capabilities
            )
        else:
            checks["capabilities_allowed"] = True
        checks["permission_intersection"] = all(
            permission in parent_permissions
            for permission in proposal.permissions
        )
        existing = AgentGovernor.authorize(
            proposed_agents=proposal.requested_child_count,
            proposed_concurrency=proposal.requested_child_count,
            proposed_depth=proposal.parent_depth + 1,
            estimated_api_cost_usd=proposal.expected_cost,
            settings=self.settings,
        )
        checks["existing_governor"] = bool(existing.get("authorized"))

        authorized = all(checks.values())
        if authorized:
            reason_code = "authorized"
        elif not checks["run_active"]:
            reason_code = "run_cancelled"
        elif not checks["parent_active"]:
            reason_code = "parent_cancelled"
        elif not checks["unique_identity"]:
            reason_code = "duplicate_identity"
        elif not checks["children_per_parent"]:
            reason_code = "children_per_parent_exceeded"
        elif not checks["run_total"]:
            reason_code = "run_agent_limit_exceeded"
        elif not checks["depth"]:
            reason_code = "spawn_depth_exceeded"
        elif not checks["concurrency"]:
            reason_code = "concurrency_bound"
        elif not checks["budget"]:
            reason_code = "budget_ceiling"
        elif not checks["capabilities_allowed"]:
            reason_code = "unauthorized_capability"
        elif not checks["permission_intersection"]:
            reason_code = "permission_escalation"
        else:
            reason_code = "existing_governor_rejected"
        return checks, authorized, reason_code

    # -- lifecycle ------------------------------------------------------------

    def transition(self, nano_id: str, new_status: str) -> str:
        """Apply one lifecycle transition via the existing audited map."""

        current = self._statuses.get(nano_id)
        if current is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"unknown nano agent {nano_id!r}",
            )
        allowed = AgentTaskStatus.ALLOWED_TRANSITIONS.get(current, frozenset())
        if new_status not in allowed:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"invalid lifecycle transition {current!r} -> {new_status!r}",
            )
        self._statuses[nano_id] = new_status
        return new_status

    def status(self, nano_id: str) -> str:
        return self._statuses[nano_id]

    # -- cancellation ---------------------------------------------------------

    def cancel_run(self) -> Tuple[str, ...]:
        """Cancel the whole run; returns every cancelled Nano id."""

        self.run_cancelled = True
        return self.cancel_descendants(None)

    def cancel(self, nano_id: str) -> Tuple[str, ...]:
        """Cancel one Nano Agent and propagate to all descendants."""

        assignment = self._assignments.get(nano_id)
        if assignment is None:
            raise ContractError(
                ContractErrorCode.INVALID, f"unknown nano agent {nano_id!r}"
            )
        return self.cancel_descendants(assignment.task_id)

    def cancel_descendants(self, root: Optional[str]) -> Tuple[str, ...]:
        """Cancel ``root`` (if given) and every descendant; terminal."""

        affected = self._lineage_members(root)
        for nano_id in affected:
            current = self._statuses.get(nano_id)
            if current in (AgentTaskStatus.CANCELLED, AgentTaskStatus.RETIRED):
                continue
            # Cancellation is terminal: the existing map allows cancellation
            # from every non-terminal active state; RETIRED records stay.
            self._statuses[nano_id] = AgentTaskStatus.CANCELLED
        return tuple(affected)

    def _lineage_members(self, root: Optional[str]) -> Tuple[str, ...]:
        """All nano ids in root's lineage (root included when given)."""

        def parent_of(nano_id: str) -> str:
            return self._assignments[nano_id].parent_task_id

        affected: list[str] = []
        for nano_id, assignment in self._assignments.items():
            if root is None:
                lineage_ok = True
            elif nano_id == root or assignment.task_id == root:
                lineage_ok = True
            else:
                lineage_ok = False
                walker: Optional[str] = assignment.parent_task_id
                while walker is not None:
                    if walker == root:
                        lineage_ok = True
                        break
                    walker_next = None
                    for other_id, other in self._assignments.items():
                        if other.task_id == walker:
                            walker_next = other.parent_task_id
                            break
                    walker = walker_next
            if lineage_ok:
                affected.append(nano_id)
        return tuple(affected)

    def lineage_members(self, root: Optional[str]) -> Tuple[str, ...]:
        """M5.6.1 public accessor: the nano ids in root's lineage."""

        return self._lineage_members(root)

    def cancel_member(self, nano_id: str) -> str:
        """M5.6.1: cancel ONE nano member; terminal from every active state.

        Unlike :meth:`cancel_descendants` this never touches other members,
        so a caller can preserve already-terminal members (history is not
        rewritten) while cancelling exactly the active lineage.
        """

        if nano_id not in self._assignments:
            raise ContractError(
                ContractErrorCode.INVALID, f"unknown nano agent {nano_id!r}"
            )
        current = self._statuses.get(nano_id)
        if current not in (AgentTaskStatus.CANCELLED, AgentTaskStatus.RETIRED):
            self._statuses[nano_id] = AgentTaskStatus.CANCELLED
        return self._statuses[nano_id]

    def _is_cancelled_lineage(self, task_id: str) -> bool:
        if self.run_cancelled:
            return True
        for nano_id, assignment in self._assignments.items():
            if assignment.task_id == task_id:
                return self._statuses.get(nano_id) == AgentTaskStatus.CANCELLED
        return False

    def is_cancelled_lineage(self, task_id: str) -> bool:
        """M5.6 public accessor: is this task's lineage cancelled?"""

        return self._is_cancelled_lineage(task_id)

    # -- results ----------------------------------------------------------------

    def record_result(self, result: NanoResultRecord) -> NanoResultRecord:
        """Accept a scope-checked result; out-of-scope results are invalid.

        M5.6: a result for a cancelled Nano Agent is a late result and is
        rejected — it can never become authoritative after cancellation.
        """

        assignment = self._assignments.get(result.nano_id)
        if assignment is None:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"result references unknown nano agent {result.nano_id!r}",
            )
        if self._statuses.get(result.nano_id) == AgentTaskStatus.CANCELLED:
            raise ContractError(
                ContractErrorCode.BLOCKED,
                f"late result for cancelled nano agent {result.nano_id!r} "
                "rejected; it can never become authoritative",
            )
        out_of_scope = [
            capability
            for capability in result.capabilities_used
            if capability not in assignment.capabilities
        ]
        if out_of_scope:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"result claims capabilities outside the assignment scope: "
                f"{out_of_scope}",
            )
        self._results[result.nano_id] = result
        return result

    def result(self, nano_id: str) -> Optional[NanoResultRecord]:
        return self._results.get(nano_id)

    # -- typed communication and escalation ---------------------------------------

    def send(self, message: NanoMessage) -> NanoMessage:
        """Accept one typed message between known Nano Agents."""

        for actor_id in (message.sender_id, message.recipient_id):
            if actor_id not in self._assignments:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"message references unknown nano agent {actor_id!r}",
                )
        self._messages.append(message)
        return message

    def escalate(self, request: NanoEscalationRequest) -> NanoEscalationRequest:
        """Route an escalation; it never grants authority."""

        if request.nano_id not in self._assignments:
            raise ContractError(
                ContractErrorCode.INVALID,
                f"escalation references unknown nano agent {request.nano_id!r}",
            )
        self._escalations.append(request)
        return request

    # -- inspection -------------------------------------------------------------

    def assignment(self, nano_id: str) -> Optional[NanoAssignment]:
        return self._assignments.get(nano_id)

    def assignments(self) -> Tuple[NanoAssignment, ...]:
        return tuple(self._assignments.values())

    def messages(self) -> Tuple[NanoMessage, ...]:
        return tuple(self._messages)

    def escalations(self) -> Tuple[NanoEscalationRequest, ...]:
        return tuple(self._escalations)
