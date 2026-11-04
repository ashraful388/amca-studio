"""M5.7 authoritative controlled gate-management layer.

Additive module introduced by the M5.7 Secure Intelligence Enablement
milestone (zcode/amca-v0.5.0-m5-7-secure-intelligence-enablement-01).

This evolves the passive M0 registry into the ONE controlled runtime
source for intelligence feature-gate state:

    ServiceContext -> IntelligenceRuntimeBridge -> ControlledGateController

Design rules (work order ZC-AMCA-V0.5.0-M5.7):
- initial state always OFF (the production bridge always starts on
  DEFAULT_GATES; nothing auto-enables);
- every mutation is validated in full BEFORE an atomic single-assignment
  commit of a new immutable FeatureGateRegistry;
- snapshots are versioned and immutable;
- enabling requires truthful runtime readiness ("ready") — "unknown"
  readiness can never be enabled;
- dependencies fail closed (Hybrid requires Adaptive enabled; nothing is
  auto-enabled);
- disabling is always safe and never requires readiness;
- gate changes are refused while an affected intelligence execution is
  active (ACTIVE_EXECUTION), except the bounded emergency disable path;
- every mutation/rejection appends a bounded audit record that never
  carries session tokens, bootstrap secrets, or credentials.

Existing M0 semantics (FeatureGateRegistry immutability, fail-closed
unknown-gate handling) remain authoritative.
"""

import dataclasses
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Tuple

from .contracts import ContractError, ContractErrorCode
from .gates import DEFAULT_GATES, FeatureGateRegistry

#: The only contract version this controller accepts.
GATE_CONTRACT_VERSION = 1

#: Hard dependency edges between capabilities (approved architecture):
#: enabling Hybrid requires Adaptive to ALREADY be enabled. Dependencies
#: are never auto-enabled.
CAPABILITY_DEPENDENCIES: Mapping[str, Tuple[str, ...]] = {
    "hybrid_execution": ("adaptive_planner",),
}

_AUDIT_BOUND = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclasses.dataclass(frozen=True)
class GateAuditRecord:
    """Bounded, secret-free audit evidence for one mutation/rejection."""

    feature: str
    old_state: bool
    new_state: bool
    at: str
    bootstrap_mode: str
    correlation_id: str
    decision: str  # "applied" | "rejected"
    category: str  # applied / runtime_not_ready / dependency_not_enabled / ...

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "feature": self.feature,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "at": self.at,
            "bootstrap_mode": self.bootstrap_mode,
            "correlation_id": self.correlation_id,
            "decision": self.decision,
            "category": self.category,
        }


def _reject(category: str, message: str) -> ContractError:
    return ContractError(
        ContractErrorCode.INVALID,
        message,
        details={"category": category},
    )


class ControlledGateController:
    """The single mutation authority for intelligence feature gates."""

    def __init__(
        self,
        *,
        gates: FeatureGateRegistry,
        bootstrap_mode: str,
        meta_factory: Callable[[str], Any],
        readiness_provider: Callable[[], Mapping[str, str]],
        on_commit: Callable[[FeatureGateRegistry], None],
        audit_bound: int = _AUDIT_BOUND,
    ) -> None:
        self._gates = gates
        self._bootstrap_mode = bootstrap_mode
        self._meta_factory = meta_factory
        self._readiness_provider = readiness_provider
        self._on_commit = on_commit
        self._snapshot_version = 0
        self._audit_log: deque = deque(maxlen=audit_bound)
        # capability name -> set of active execution ids (M5.7 §7)
        self._active_executions: dict[str, set] = {}

    # -- inspection -----------------------------------------------------------

    @property
    def gates(self) -> FeatureGateRegistry:
        return self._gates

    @property
    def snapshot_version(self) -> int:
        return self._snapshot_version

    def snapshot(self) -> Mapping[str, Any]:
        """Immutable, versioned gate snapshot for consumers."""

        return {
            "snapshot_version": self._snapshot_version,
            "gates": self._gates.to_dict(),
            "bootstrap_mode": self._bootstrap_mode,
        }

    def readiness(self) -> Mapping[str, str]:
        return dict(self._readiness_provider())

    def audit_records(self) -> Tuple[Mapping[str, Any], ...]:
        return tuple(record.to_dict() for record in self._audit_log)

    # -- active-run protection --------------------------------------------------

    def begin_execution(self, capability: str, run_id: str) -> None:
        self._gates.is_enabled(capability)  # unknown names fail closed
        self._active_executions.setdefault(capability, set()).add(str(run_id))

    def end_execution(self, capability: str, run_id: str) -> None:
        runs = self._active_executions.get(capability)
        if runs is not None:
            runs.discard(str(run_id))
            if not runs:
                self._active_executions.pop(capability, None)

    def _affected_active(self, changes: Mapping[str, bool]) -> Tuple[str, ...]:
        return tuple(
            sorted(
                capability
                for capability in changes
                if self._active_executions.get(capability)
            )
        )

    def _audit(
        self,
        *,
        feature: str,
        old_state: bool,
        new_state: bool,
        correlation_id: str,
        decision: str,
        category: str,
    ) -> GateAuditRecord:
        record = GateAuditRecord(
            feature=feature,
            old_state=bool(old_state),
            new_state=bool(new_state),
            at=_utc_now(),
            bootstrap_mode=self._bootstrap_mode,
            correlation_id=correlation_id,
            decision=decision,
            category=category,
        )
        self._audit_log.append(record)
        return record

    # -- mutation ---------------------------------------------------------------

    def apply_changes(
        self,
        changes: Mapping[str, Any],
        *,
        contract_version: int,
        correlation_id: str,
        allow_active_disable: bool = False,
    ) -> Mapping[str, Any]:
        """Validate a full change set, then commit it atomically.

        All-or-nothing: any rejection leaves the current gate state and
        snapshot version untouched. Rejections raise ContractError with
        ``details["category"]`` set to a stable machine category.
        """

        if contract_version != GATE_CONTRACT_VERSION:
            raise _reject(
                "unsupported_contract_version",
                f"unsupported contract version {contract_version!r}; "
                f"expected {GATE_CONTRACT_VERSION}",
            )
        if not isinstance(changes, Mapping) or isinstance(changes, (str, bytes)):
            raise _reject(
                "malformed_request", "changes must be an object of booleans"
            )
        for feature, target in changes.items():
            if feature not in DEFAULT_GATES.to_dict():
                raise _reject(
                    "unknown_capability",
                    f"unknown intelligence capability {feature!r}",
                )
            if not isinstance(target, bool):
                raise _reject(
                    "malformed_request",
                    f"gate state for {feature!r} must be a boolean",
                )
        if not changes:
            raise _reject("malformed_request", "changes must not be empty")

        current = self._gates.to_dict()
        readiness = self._readiness_provider()
        for feature, target in sorted(changes.items()):
            if target is current[feature]:
                continue  # no-op entries are permitted and audited as such
            if target:
                # Readiness enforcement: "unknown" can never enable.
                if readiness.get(feature) != "ready":
                    self._audit(
                        feature=feature, old_state=current[feature],
                        new_state=target, correlation_id=correlation_id,
                        decision="rejected", category="runtime_not_ready",
                    )
                    raise _reject(
                        "runtime_not_ready",
                        f"{feature} cannot be enabled: runtime readiness is "
                        f"{readiness.get(feature)!r}; enabling requires "
                        "'ready'",
                    )
                for dependency in CAPABILITY_DEPENDENCIES.get(feature, ()):
                    if not current[dependency]:
                        self._audit(
                            feature=feature, old_state=current[feature],
                            new_state=target, correlation_id=correlation_id,
                            decision="rejected",
                            category="dependency_not_enabled",
                        )
                        raise _reject(
                            "dependency_not_enabled",
                            f"{feature} requires {dependency} to be enabled "
                            "first; dependencies are never auto-enabled",
                        )
            elif not allow_active_disable and self._affected_active({feature: target}):
                self._audit(
                    feature=feature, old_state=current[feature],
                    new_state=target, correlation_id=correlation_id,
                    decision="rejected", category="active_execution",
                )
                raise ContractError(
                    ContractErrorCode.INVALID,
                    f"{feature} has an active intelligence execution; gate "
                    "changes are refused until it finishes (or use the "
                    "emergency disable path)",
                    details={"category": "active_execution"},
                )

        active = self._affected_active(changes)
        if active and not allow_active_disable and any(
            changes[feature] != current[feature] for feature in active
        ):
            raise ContractError(
                ContractErrorCode.INVALID,
                "affected intelligence executions are active for: "
                f"{', '.join(active)}",
                details={"category": "active_execution"},
            )

        new_gates = self._gates.with_overrides(**dict(changes))
        applied: dict[str, bool] = {}
        for feature, target in sorted(changes.items()):
            applied[feature] = target
            self._audit(
                feature=feature, old_state=current[feature], new_state=target,
                correlation_id=correlation_id,
                decision="applied",
                category="no_op" if target is current[feature] else "applied",
            )
        # Atomic commit: one immutable registry swap.
        self._gates = new_gates
        self._snapshot_version += 1
        self._on_commit(new_gates)
        return {
            "snapshot_version": self._snapshot_version,
            "gates": self._gates.to_dict(),
            "changes": applied,
        }

    def disable_all_experimental(
        self, *, correlation_id: str, emergency: bool = False
    ) -> Mapping[str, Any]:
        """Fail-safe rollback: restore every experimental gate to OFF.

        Atomic; the emergency variant is the bounded internal operation for
        callers that first perform authoritative cancellation of affected
        executions.
        """

        current = self._gates.to_dict()
        if not emergency:
            active = self._affected_active(
                {name: False for name in current if current[name]}
            )
            if active:
                raise ContractError(
                    ContractErrorCode.INVALID,
                    "affected intelligence executions are active for: "
                    f"{', '.join(active)}",
                    details={"category": "active_execution"},
                )
        self._gates = DEFAULT_GATES
        self._snapshot_version += 1
        self._on_commit(self._gates)
        for feature, old_state in sorted(current.items()):
            self._audit(
                feature=feature, old_state=old_state, new_state=False,
                correlation_id=correlation_id,
                decision="applied",
                category="disable_all" if old_state else "no_op",
            )
        return {
            "snapshot_version": self._snapshot_version,
            "gates": self._gates.to_dict(),
        }
