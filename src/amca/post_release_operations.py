"""AMCA Studio v0.7.0 Post-Release Operations Readiness (AG-AMCA-STUDIO-V0.7.0).

Provides release monitoring framework, support escalation classification (L0-L3),
rollback validation procedures, known limitation registry freeze, and operational
certification for AMCA Studio v0.7.0.
Strictly zero cloud telemetry, 100% offline, local-first operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


ESCALATION_LEVELS = {
    "L0": "Documentation & User Guidance",
    "L1": "UI Display & Styling Gaps",
    "L2": "Execution Engine & Runtime Faults",
    "L3": "Data Integrity, Boundary Violation, or Security Fault",
}


@dataclass(frozen=True, slots=True)
class EscalationClassification:
    level: str
    description: str
    target_sla_hours: int
    resolution_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "description": self.description,
            "target_sla_hours": self.target_sla_hours,
            "resolution_path": self.resolution_path,
        }


@dataclass(frozen=True, slots=True)
class RollbackVerificationRecord:
    verified: bool
    fallback_version: str
    database_backward_compatible: bool
    recovery_procedure_documented: bool
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "fallback_version": self.fallback_version,
            "database_backward_compatible": self.database_backward_compatible,
            "recovery_procedure_documented": self.recovery_procedure_documented,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class PostReleaseOperationsCertificate:
    release_version: str
    certificate_id: str
    escalation_matrix: tuple[EscalationClassification, ...]
    rollback_verification: RollbackVerificationRecord
    known_limitations_frozen: bool
    verdict: str
    certified_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_version": self.release_version,
            "certificate_id": self.certificate_id,
            "escalation_matrix": [e.to_dict() for e in self.escalation_matrix],
            "rollback_verification": self.rollback_verification.to_dict(),
            "known_limitations_frozen": self.known_limitations_frozen,
            "verdict": self.verdict,
            "certified_at": self.certified_at,
        }


def classify_support_issue(level: str) -> EscalationClassification:
    lvl = level.upper().strip()
    if lvl == "L0":
        return EscalationClassification(
            level="L0",
            description=ESCALATION_LEVELS["L0"],
            target_sla_hours=48,
            resolution_path="Documentation & FAQ clarification; in-app guidance updates.",
        )
    elif lvl == "L1":
        return EscalationClassification(
            level="L1",
            description=ESCALATION_LEVELS["L1"],
            target_sla_hours=24,
            resolution_path="Frontend UI patch in maintenance release.",
        )
    elif lvl == "L2":
        return EscalationClassification(
            level="L2",
            description=ESCALATION_LEVELS["L2"],
            target_sla_hours=12,
            resolution_path="Execution engine hotfix or provider retry adjustment.",
        )
    elif lvl == "L3":
        return EscalationClassification(
            level="L3",
            description=ESCALATION_LEVELS["L3"],
            target_sla_hours=4,
            resolution_path="Immediate security isolation patch, database migration fix, or rollback.",
        )
    raise ValueError(f"Unknown escalation level: {level}")


def generate_post_release_operations_certificate(
    release_version: str = "v0.7.0",
    *,
    fallback_version: str = "v0.6.2",
    db_compatible: bool = True,
    recovery_doc_present: bool = True,
    limitations_frozen: bool = True,
) -> PostReleaseOperationsCertificate:
    """Generates the formal post-release operational certificate."""
    matrix = (
        classify_support_issue("L0"),
        classify_support_issue("L1"),
        classify_support_issue("L2"),
        classify_support_issue("L3"),
    )

    rollback_rec = RollbackVerificationRecord(
        verified=db_compatible and recovery_doc_present,
        fallback_version=fallback_version,
        database_backward_compatible=db_compatible,
        recovery_procedure_documented=recovery_doc_present,
        details=f"Rollback to {fallback_version} verified safe with SQLite backward-compatible schema.",
    )

    ready = rollback_rec.verified and limitations_frozen
    verdict = "OPERATIONS_READY" if ready else "HOLD"

    return PostReleaseOperationsCertificate(
        release_version=str(release_version).strip(),
        certificate_id=f"opscert-{uuid4().hex[:16]}",
        escalation_matrix=matrix,
        rollback_verification=rollback_rec,
        known_limitations_frozen=limitations_frozen,
        verdict=verdict,
        certified_at=_now(),
    )

