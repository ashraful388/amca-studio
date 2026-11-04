"""AMCA Studio v0.8.0 Provider Health & Credential Governance Design (AG-AMCA-STUDIO-V0.8.0).

Defines architectural specifications, contracts, and lifecycle state machines for:
1. Credential Lifecycle Management (ACTIVE, ROTATING, ROTATED, REVOKED, EXPIRED)
2. In-place Atomic Credential Rotation Workflow (Validate -> Swap -> Verify -> Commit / Rollback)
3. Explicit Provider Health Verification (HEALTHY, DEGRADED, UNREACHABLE, UNCHECKED)
4. Security & Audit Specifications (Zero plaintext secrets, no background telemetry)

Strictly architecture specification, schema design, and invariant validation;
no premature runtime credential migrations or background polling daemons introduced.
100% offline, zero cloud dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


CredentialState = Literal["ACTIVE", "ROTATING", "ROTATED", "REVOKED", "EXPIRED"]
ProviderHealthStatus = Literal["HEALTHY", "DEGRADED", "UNREACHABLE", "UNCHECKED"]


@dataclass(frozen=True, slots=True)
class CredentialLifecycleMetadata:
    credential_id: str
    workspace_id: str
    provider_name: str
    key_fingerprint: str
    state: CredentialState
    created_at: str
    last_rotated_at: str | None
    expires_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "workspace_id": self.workspace_id,
            "provider_name": self.provider_name,
            "key_fingerprint": self.key_fingerprint,
            "state": self.state,
            "created_at": self.created_at,
            "last_rotated_at": self.last_rotated_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class ProviderHealthRecord:
    provider_name: str
    endpoint_url: str
    last_checked: str | None
    status: ProviderHealthStatus
    failure_reason: str | None
    capabilities_checked: tuple[str, ...]
    latency_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "endpoint_url": self.endpoint_url,
            "last_checked": self.last_checked,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "capabilities_checked": list(self.capabilities_checked),
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True, slots=True)
class CredentialRotationStep:
    step_name: str
    description: str
    rollback_action: str


@dataclass(frozen=True, slots=True)
class CredentialRotationWorkflowSpecification:
    workflow_name: str
    supported_states: tuple[CredentialState, ...]
    steps: tuple[CredentialRotationStep, ...]
    supports_in_place_swap: bool
    requires_zero_plaintext_storage: bool


@dataclass(frozen=True, slots=True)
class CredentialRotationAuditEvent:
    audit_id: str
    workspace_id: str
    operator_role: str
    provider_name: str
    old_fingerprint: str
    new_fingerprint: str
    verification_result: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "workspace_id": self.workspace_id,
            "operator_role": self.operator_role,
            "provider_name": self.provider_name,
            "old_fingerprint": self.old_fingerprint,
            "new_fingerprint": self.new_fingerprint,
            "verification_result": self.verification_result,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class ProviderGovernanceDesignPackage:
    design_id: str
    target_version: str
    rotation_workflow: CredentialRotationWorkflowSpecification
    provider_health_contract_version: str
    supported_health_statuses: tuple[ProviderHealthStatus, ...]
    background_monitoring_permitted: bool
    overall_status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "target_version": self.target_version,
            "rotation_workflow": {
                "workflow_name": self.rotation_workflow.workflow_name,
                "supported_states": list(self.rotation_workflow.supported_states),
                "steps": [s.__dict__ for s in self.rotation_workflow.steps],
                "supports_in_place_swap": self.rotation_workflow.supports_in_place_swap,
                "requires_zero_plaintext_storage": self.rotation_workflow.requires_zero_plaintext_storage,
            },
            "provider_health_contract_version": self.provider_health_contract_version,
            "supported_health_statuses": list(self.supported_health_statuses),
            "background_monitoring_permitted": self.background_monitoring_permitted,
            "overall_status": self.overall_status,
            "created_at": self.created_at,
        }


def assemble_provider_governance_design(
    target_version: str = "v0.8.0",
) -> ProviderGovernanceDesignPackage:
    """Assembles the formal v0.8.0 Provider Health & Credential Governance Design specification."""
    steps = (
        CredentialRotationStep(
            step_name="PRE_ROTATION_VALIDATE",
            description="Verify new credential against provider health check before modifying stored credential.",
            rollback_action="Abort rotation, leave existing ACTIVE credential untouched.",
        ),
        CredentialRotationStep(
            step_name="ATOMIC_SWAP",
            description="Transition current credential to ROTATING, stage new encrypted credential in atomic transaction.",
            rollback_action="Revert transaction, restore original ACTIVE credential.",
        ),
        CredentialRotationStep(
            step_name="POST_ROTATION_VERIFY",
            description="Execute explicit capability handshake with newly swapped credential.",
            rollback_action="Revert atomic swap to old credential, mark old credential ACTIVE.",
        ),
        CredentialRotationStep(
            step_name="COMMIT_AND_AUDIT",
            description="Mark new credential ACTIVE, old credential ROTATED/REVOKED, append immutable audit record with fingerprints only.",
            rollback_action="No rollback once committed.",
        ),
    )

    rotation_spec = CredentialRotationWorkflowSpecification(
        workflow_name="In-Place Atomic Credential Rotation",
        supported_states=("ACTIVE", "ROTATING", "ROTATED", "REVOKED", "EXPIRED"),
        steps=steps,
        supports_in_place_swap=True,
        requires_zero_plaintext_storage=True,
    )

    return ProviderGovernanceDesignPackage(
        design_id=f"gov-dsn-{uuid4().hex[:16]}",
        target_version=str(target_version).strip(),
        rotation_workflow=rotation_spec,
        provider_health_contract_version="v1-explicit",
        supported_health_statuses=("HEALTHY", "DEGRADED", "UNREACHABLE", "UNCHECKED"),
        background_monitoring_permitted=False,
        overall_status="PROVIDER_GOVERNANCE_DESIGN_APPROVED",
        created_at=_now(),
    )

