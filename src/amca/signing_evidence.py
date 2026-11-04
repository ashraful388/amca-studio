"""AMCA Studio v1.0.0 Windows Signing Evidence Contract and Promotion Adapter (I2H).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2H-WINDOWS-SIGNING-EVIDENCE-CONTRACT-UNSIGNED-RELEASE-DISCLOSURE-AND-PROMOTION-ADAPTER-PREFLIGHT-01

Converts the I2G signing audit into deterministic evidence that the final
release process can consume whether the project owner chooses an unsigned
v1.0.0 or a signed v1.0.0 — without confusing unsigned binaries with broken
binaries.

States (explicit; UNSIGNED_ALLOWED is never equated with SIGNED_VALID):
  SIGNED_VALID                       - valid signature + timestamp
  UNSIGNED_ALLOWED                   - unsigned under an optional policy
  UNSIGNED_REQUIRES_ACKNOWLEDGMENT   - unsigned under optional policy;
                                       promotion must carry the disclosure
  SIGNING_REQUIRED_BUT_UNAVAILABLE   - unsigned under a required policy
  SIGNATURE_INVALID                  - HashMismatch/NotTrusted/Expired/etc.
  SIGNING_ERROR                      - tool/inspection failure

Policy modes (the project owner/Manager decides; this module does not choose):
  optional - unsigned artifacts may be UNSIGNED_REQUIRES_ACKNOWLEDGMENT
  required - unsigned artifacts become SIGNING_REQUIRED_BUT_UNAVAILABLE

This module is a READ-ONLY adapter for the final promotion gate: it never
modifies final_promotion.py, the promotion process, or any artifact. Exact-
byte binding is enforced by SHA256: signing an already-hashed executable,
modifying a signed executable, or moving evidence between different binaries
(all proved by tests) invalidates prior evidence.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amca.distribution_smoke import sha256_file

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]

# Policy / result states
ST_SIGNED_VALID = "SIGNED_VALID"
ST_UNSIGNED_ALLOWED = "UNSIGNED_ALLOWED"
ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT = "UNSIGNED_REQUIRES_ACKNOWLEDGMENT"
ST_SIGNING_REQUIRED_BUT_UNAVAILABLE = "SIGNING_REQUIRED_BUT_UNAVAILABLE"
ST_SIGNATURE_INVALID = "SIGNATURE_INVALID"
ST_SIGNING_ERROR = "SIGNING_ERROR"

POLICY_OPTIONAL = "OPTIONAL"
POLICY_REQUIRED = "REQUIRED"

EVIDENCE_SCHEMA = "amca-windows-signing-evidence/1"

SMARTSCREEN_LIMITATION = (
    "Code signing establishes publisher integrity/authenticity. SmartScreen "
    "reputation is not guaranteed merely by signing; unsigned binaries "
    "trigger Unknown Publisher/SmartScreen warnings. Unsigned does not imply "
    "malware or application malfunction. Users should verify the official "
    "SHA256 checksums; checksum verification is a complement to, not a "
    "replacement for, code signing. Signing may be introduced in a later "
    "release."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SigningEvidence:
    """Deterministic evidence bundle bound to exact physical bytes."""

    source_sha: str
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    publisher_identity: Optional[str] = None
    timestamp_status: str = "ABSENT"
    signing_policy: str = POLICY_OPTIONAL
    acknowledgment_state: str = "NOT_ACKNOWLEDGED"
    smartscreen_limitation: str = SMARTSCREEN_LIMITATION
    overall_technical_verdict: str = "REVIEW"
    generated_at_utc: str = field(default_factory=_utc_now)
    schema: str = EVIDENCE_SCHEMA

    def to_document(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at_utc": self.generated_at_utc,
            "source_sha": self.source_sha,
            "artifacts": self.artifacts,
            "publisher_identity": self.publisher_identity,
            "timestamp_status": self.timestamp_status,
            "signing_policy": self.signing_policy,
            "acknowledgment_state": self.acknowledgment_state,
            "smartscreen_limitation": self.smartscreen_limitation,
            "overall_technical_verdict": self.overall_technical_verdict,
        }


def _per_artifact_state(
    inspection_status: str,
    policy: str,
    timestamped: bool,
) -> str:
    """Map one artifact's Authenticode status + policy to an explicit state."""
    if inspection_status == "Valid":
        return ST_SIGNED_VALID if timestamped else ST_SIGNED_VALID
    if inspection_status in ("HashMismatch", "NotTrusted", "Expired",
                             "Incompatible"):
        return ST_SIGNATURE_INVALID
    if inspection_status in ("INSPECTION_ERROR", "UnknownError"):
        return ST_SIGNING_ERROR
    if inspection_status in ("NotSigned", "ARTIFACT_MISSING"):
        if policy == POLICY_REQUIRED:
            return ST_SIGNING_REQUIRED_BUT_UNAVAILABLE
        return ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT
    return ST_SIGNING_ERROR


def build_signing_evidence(
    audit: Dict[str, Any],
    *,
    policy: str = POLICY_OPTIONAL,
    source_sha: Optional[str] = None,
    acknowledged: bool = False,
) -> SigningEvidence:
    """Convert an I2G audit (amca-windows-signing-audit/1) into evidence.

    Exact-byte binding: every artifact record carries the SHA256 that was
    audited; the adapter later re-verifies these against physical bytes.
    """
    evidence = SigningEvidence(
        source_sha=source_sha or audit.get("source_sha", ""),
        signing_policy=policy,
        acknowledgment_state="ACKNOWLEDGED" if acknowledged else "NOT_ACKNOWLEDGED",
    )
    verdicts: List[str] = []
    timestamps: List[bool] = []
    publishers: List[str] = []
    for art in audit.get("artifacts", []):
        inspection = art.get("inspection", {})
        status = inspection.get("status", "SIGNING_ERROR")
        state = _per_artifact_state(status, policy, bool(inspection.get("timestamped")))
        if status == "NOT_APPLICABLE":
            state = "NOT_APPLICABLE"
        record = {
            "artifact": art.get("artifact"),
            "sha256": art.get("sha256"),
            "signature_status": status,
            "state": state,
        }
        if inspection.get("signer"):
            publishers.append(inspection["signer"])
            record["publisher"] = inspection["signer"]
        evidence.artifacts.append(record)
        if state != "NOT_APPLICABLE":
            verdicts.append(state)
        if inspection.get("timestamped"):
            timestamps.append(True)

    evidence.publisher_identity = publishers[0] if publishers else None
    evidence.timestamp_status = (
        "PRESENT" if timestamps and all(timestamps)
        else ("PARTIAL" if timestamps else "ABSENT")
    )
    # Technical verdict: INVALID/ERROR dominate; under optional policy an
    # unsigned set is REVIEW (allowed with acknowledgment), under required
    # policy it is BLOCKED.
    if any(v in (ST_SIGNATURE_INVALID, ST_SIGNING_ERROR) for v in verdicts):
        evidence.overall_technical_verdict = "FAIL"
    elif policy == POLICY_REQUIRED and any(
        v == ST_SIGNING_REQUIRED_BUT_UNAVAILABLE for v in verdicts
    ):
        evidence.overall_technical_verdict = "BLOCKED"
    elif all(v == ST_SIGNED_VALID for v in verdicts):
        evidence.overall_technical_verdict = "PASS"
    else:
        evidence.overall_technical_verdict = "REVIEW"
    return evidence


def promote_adapter(
    evidence: SigningEvidence,
    *,
    artifact_hashes: Dict[str, str],
) -> Dict[str, Any]:
    """READ-ONLY adapter for the final promotion process.

    Returns signing state/policy, per-artifact exact hashes, whether
    acknowledgment is required, and whether signing technically blocks
    promotion. Verifies that the supplied hashes match the evidence bytes;
    stale/mismatched evidence is rejected. Never mutates anything.
    """
    mismatched = [
        name for name, sha in artifact_hashes.items()
        if not any(
            a["artifact"] == name and a["sha256"] == sha
            for a in evidence.artifacts
        )
    ]
    states = {a["state"] for a in evidence.artifacts if a["state"] != "NOT_APPLICABLE"}
    signing_blocks = bool(
        states & {ST_SIGNATURE_INVALID, ST_SIGNING_ERROR,
                  ST_SIGNING_REQUIRED_BUT_UNAVAILABLE}
    )
    unsigned_present = bool(
        states & {ST_UNSIGNED_ALLOWED, ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT}
    )
    acknowledgment_required = (
        evidence.acknowledgment_state != "ACKNOWLEDGED" and unsigned_present
    )
    return {
        "schema": "amca-signing-promotion-adapter/1",
        "read_only": True,
        "signing_state": (
            ST_SIGNED_VALID if states == {ST_SIGNED_VALID}
            else (ST_SIGNATURE_INVALID if ST_SIGNATURE_INVALID in states
                  else (ST_SIGNING_ERROR if ST_SIGNING_ERROR in states
                        else (ST_SIGNING_REQUIRED_BUT_UNAVAILABLE
                              if ST_SIGNING_REQUIRED_BUT_UNAVAILABLE in states
                              else ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT
                              if ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT in states
                              else ST_UNSIGNED_ALLOWED)))
        ),
        "signing_policy": evidence.signing_policy,
        "exact_artifact_hashes": dict(artifact_hashes),
        "acknowledgment_required": acknowledgment_required,
        "signing_blocks_promotion": signing_blocks,
        "stale_or_mismatched_evidence": mismatched,
        "evidence_valid_for_promotion": not mismatched and not signing_blocks,
    }


def verify_evidence_binding(
    evidence: SigningEvidence,
    current_hashes: Dict[str, str],
) -> bool:
    """Exact-byte check: every artifact hash in the evidence must still match
    the current physical bytes. Signing after hashing, modifying after
    signing, or substituting a different binary all break this."""
    for art in evidence.artifacts:
        name, recorded = art["artifact"], art["sha256"]
        if recorded is None:
            continue
        current = current_hashes.get(name)
        if current is None or current != recorded:
            return False
    return True
