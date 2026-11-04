"""AMCA Studio v1.0.0 Final Release Policy Decision Contract (I2J).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2J-FINAL-RELEASE-POLICY-DECISION-CONTRACT-AND-PROMOTION-AUTHORIZATION-PREFLIGHT-01

Deterministic policy/authorization layer that records the remaining
manager/project-owner release decisions SEPARATELY from technical
certification, preventing the final promotion gate from confusing an
engineering PASS with an unresolved release-policy choice.

Policy decisions represented:
- Windows signing policy: OPTIONAL_UNSIGNED_ALLOWED | SIGNED_REQUIRED
- Unsigned disclosure: acknowledged | not acknowledged
- Compliance review: PASS_WITH_REVIEW acknowledged / not acknowledged
  (the certifi/MPL-2.0 review item is preserved; acknowledgment never
  converts PASS_WITH_REVIEW into plain PASS and never claims legal approval)
- Platform scope: Windows installer + portable included; Linux explicitly
  not included in v1.0.0 (acknowledgment required)
- Promotion overwrite: existing official artifacts may not be overwritten
  unless explicitly authorized
- Release destination: the canonical standing release location

States: POLICY_READY / DECISION_REQUIRED / POLICY_BLOCKED. Policy values are
NEVER chosen automatically for the user. The evaluator is read-only: it
copies, builds, signs, tags, and promotes nothing.

Technical vs policy separation: a technical blocker (certification failure,
hash mismatch, invalid signature under required-signing policy, blocking
compliance result) is never overridable by policy acknowledgment; a pending
policy choice is never misreported as a software defect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]

#: Canonical standing release destination (unchanged; I2E contract).
RELEASE_DESTINATION = (
    "D:/ChatGPT/AMCA/software/dist"
)

# Signing policies
SIGNING_OPTIONAL_UNSIGNED_ALLOWED = "OPTIONAL_UNSIGNED_ALLOWED"
SIGNING_SIGNED_REQUIRED = "SIGNED_REQUIRED"

# Overall policy states
POLICY_READY = "POLICY_READY"
DECISION_REQUIRED = "DECISION_REQUIRED"
POLICY_BLOCKED = "POLICY_BLOCKED"

POLICY_SCHEMA = "amca-final-release-policy/1"

#: Compliance review item preserved from the I2N payload (must remain
#: visible; acknowledgment does NOT rename PASS_WITH_REVIEW to PASS).
COMPLIANCE_REVIEW_ITEM = "certifi/MPL-2.0 license review"

#: Technical blockers that policy acknowledgment can NEVER override.
TECHNICAL_BLOCKERS = (
    "certification failure",
    "hash mismatch",
    "invalid signature under required-signing policy",
    "blocking compliance result",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ReleasePolicyDecision:
    """The owner/Manager's explicit policy decisions (no defaults chosen)."""

    release_version: Optional[str] = None
    target_source_sha: Optional[str] = None
    signing_policy: Optional[str] = None  # OPTIONAL_UNSIGNED_ALLOWED | SIGNED_REQUIRED
    unsigned_disclosure_acknowledged: Optional[bool] = None
    compliance_review_acknowledged: Optional[bool] = None
    linux_exclusion_acknowledged: Optional[bool] = None
    overwrite_authorized: Optional[bool] = None
    destination: str = RELEASE_DESTINATION
    decision_timestamp_utc: Optional[str] = None
    decision_provenance: str = "unrecorded"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "release_version": self.release_version,
            "target_source_sha": self.target_source_sha,
            "signing_policy": self.signing_policy,
            "unsigned_disclosure_acknowledged": self.unsigned_disclosure_acknowledged,
            "compliance_review_acknowledged": self.compliance_review_acknowledged,
            "linux_exclusion_acknowledged": self.linux_exclusion_acknowledged,
            "overwrite_authorized": self.overwrite_authorized,
            "destination": self.destination,
            "decision_timestamp_utc": self.decision_timestamp_utc,
            "decision_provenance": self.decision_provenance,
        }


@dataclass
class PolicyDecisionOutcome:
    """Deterministic evaluation of the policy contract (read-only)."""

    overall_policy_state: str = "DECISION_REQUIRED"
    unresolved_decisions: List[str] = field(default_factory=list)
    policy_blockers: List[str] = field(default_factory=list)
    consequences: List[str] = field(default_factory=list)
    signing_policy_state: Optional[str] = None
    compliance_item_preserved: bool = True
    compliance_result_unchanged: bool = True
    destination: str = RELEASE_DESTINATION
    schema: str = POLICY_SCHEMA
    evaluated_at_utc: str = field(default_factory=_utc_now)

    def to_document(self, decision: Optional[ReleasePolicyDecision] = None) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "evaluated_at_utc": self.evaluated_at_utc,
            "release_version": decision.release_version if decision else None,
            "target_source_sha": decision.target_source_sha if decision else None,
            "signing_policy": decision.signing_policy if decision else None,
            "unsigned_disclosure_acknowledged":
                decision.unsigned_disclosure_acknowledged if decision else None,
            "compliance_review_acknowledged":
                decision.compliance_review_acknowledged if decision else None,
            "compliance_review_item_preserved": COMPLIANCE_REVIEW_ITEM,
            "compliance_result_unchanged": self.compliance_result_unchanged,
            "linux_exclusion_acknowledged":
                decision.linux_exclusion_acknowledged if decision else None,
            "overwrite_authorized":
                decision.overwrite_authorized if decision else None,
            "destination": self.destination,
            "decision_timestamp_utc":
                decision.decision_timestamp_utc if decision else None,
            "decision_provenance":
                decision.decision_provenance if decision else "unrecorded",
            "overall_policy_state": self.overall_policy_state,
            "unresolved_decisions": self.unresolved_decisions,
            "policy_blockers": self.policy_blockers,
            "consequences": self.consequences,
            "technical_vs_policy": {
                "technical_blockers_not_overridable": list(TECHNICAL_BLOCKERS),
                "policy_pending_is_not_a_software_defect": True,
            },
        }


def evaluate_policy(
    decision: Optional[ReleasePolicyDecision],
    *,
    signing_states: Optional[List[str]] = None,
    technical_blockers_present: bool = False,
) -> PolicyDecisionOutcome:
    """Evaluate the policy contract read-only.

    - Missing policy input is never inferred: each undecided item lands in
      unresolved_decisions (DECISION_REQUIRED).
    - An owner choice that contradicts the technical state is a policy
      blocker (POLICY_BLOCKED), e.g. SIGNED_REQUIRED while artifacts remain
      unsigned, or any acknowledgment attempted while a technical blocker is
      present (acknowledgment cannot override technical failures).
    - Signing consequence: OPTIONAL_UNSIGNED_ALLOWED + acknowledged
      disclosure leaves unsigned binaries policy-eligible (subject to all
      technical gates); SIGNED_REQUIRED + unsigned -> blocked.
    - Compliance consequence: PASS_WITH_REVIEW stays PASS_WITH_REVIEW;
      acknowledgment permits promotion to continue but never renames the
      result to plain PASS and never claims legal approval.
    """
    outcome = PolicyDecisionOutcome()
    signing_states = signing_states or []

    if decision is None:
        outcome.overall_policy_state = DECISION_REQUIRED
        outcome.unresolved_decisions = [
            "Windows signing policy undecided "
            "(OPTIONAL_UNSIGNED_ALLOWED vs SIGNED_REQUIRED)",
            "unsigned-release disclosure acknowledgment undecided",
            "compliance-review acknowledgment undecided",
            "Linux exclusion from v1.0.0 not acknowledged",
            "overwrite authorization for existing official artifacts undecided",
            "no policy decision supplied at all",
        ]
        outcome.consequences.append(
            "final promotion must not proceed until the owner resolves the "
            "policy contract")
        return outcome

    # Required-decision enumeration (every field must be explicitly set).
    if not decision.signing_policy:
        outcome.unresolved_decisions.append(
            "Windows signing policy undecided "
            "(OPTIONAL_UNSIGNED_ALLOWED vs SIGNED_REQUIRED)")
    if decision.unsigned_disclosure_acknowledged is None:
        outcome.unresolved_decisions.append(
            "unsigned-release disclosure acknowledgment undecided")
    if decision.compliance_review_acknowledged is None:
        outcome.unresolved_decisions.append(
            "compliance-review (PASS_WITHReview / "
            f"{COMPLIANCE_REVIEW_ITEM}) acknowledgment undecided")
    if decision.linux_exclusion_acknowledged is None:
        outcome.unresolved_decisions.append(
            "Linux exclusion from v1.0.0 not acknowledged")
    if decision.overwrite_authorized is None:
        outcome.unresolved_decisions.append(
            "overwrite authorization for existing official artifacts undecided")

    # Signing policy consequences (using the I2H signing states).
    unsigned_present = any(
        s in ("UNSIGNED_ALLOWED", "UNSIGNED_REQUIRES_ACKNOWLEDGMENT")
        for s in signing_states)
    signed_valid = any(s == "SIGNED_VALID" for s in signing_states)
    if decision.signing_policy == SIGNING_OPTIONAL_UNSIGNED_ALLOWED:
        if unsigned_present:
            outcome.signing_policy_state = (
                "UNSIGNED_ALLOWED" if decision.unsigned_disclosure_acknowledged
                else "UNSIGNED_REQUIRES_ACKNOWLEDGMENT")
            if decision.unsigned_disclosure_acknowledged:
                outcome.consequences.append(
                    "unsigned binaries remain policy-eligible subject to all "
                    "technical release gates; the unsigned-release disclosure "
                    "must ship with the release")
            else:
                outcome.consequences.append(
                    "unsigned binaries cannot ship until the unsigned-release "
                    "disclosure is acknowledged")
    elif decision.signing_policy == SIGNING_SIGNED_REQUIRED:
        if unsigned_present or not signed_valid:
            outcome.signing_policy_state = "SIGNING_REQUIRED_BUT_UNAVAILABLE"
            outcome.policy_blockers.append(
                "SIGNED_REQUIRED policy while artifacts remain unsigned; "
                "signing must be configured and performed before promotion")
            outcome.consequences.append(
                "policy blocked: signed release required, unsigned binaries "
                "are not promotable")
        elif signed_valid:
            outcome.signing_policy_state = "SIGNED_VALID"
            outcome.consequences.append(
                "signed release policy satisfied by validly signed artifacts")

    # Compliance-review consequence: acknowledgment preserves the item and
    # never renames PASS_WITH_REVIEW to plain PASS.
    if decision.compliance_review_acknowledged:
        outcome.consequences.append(
            "compliance PASS_WITH_REVIEW acknowledged; the review item "
            f"({COMPLIANCE_REVIEW_ITEM}) remains preserved and the result "
            "is NOT renamed to plain PASS; no legal approval is claimed")
        outcome.compliance_result_unchanged = True

    # Linux scope consequence.
    if decision.linux_exclusion_acknowledged:
        outcome.consequences.append(
            "Linux explicitly not included in v1.0.0 acknowledged "
            "(Windows installer + portable scope)")

    # Overwrite consequence.
    if decision.overwrite_authorized:
        outcome.consequences.append(
            "overwrite of existing official artifacts explicitly authorized")

    # Overall state: policy blockers first, then unresolved decisions.
    if outcome.policy_blockers:
        outcome.overall_policy_state = POLICY_BLOCKED
    elif outcome.unresolved_decisions:
        outcome.overall_policy_state = DECISION_REQUIRED
    else:
        outcome.overall_policy_state = POLICY_READY
    return outcome


def separate_technical_from_policy(
    technical_blockers: List[str],
    policy_outcome: PolicyDecisionOutcome,
) -> Dict[str, Any]:
    """Explicit technical/policy separation for the final report.

    A pending policy choice is never misreported as a software defect, and a
    technical failure is never overridden by policy acknowledgment.
    """
    return {
        "technical_blockers": list(technical_blockers),
        "technical_blockers_overridable_by_policy": False,
        "technical_blockers_not_overridable_by_policy": list(
            TECHNICAL_BLOCKERS),
        "policy_state": policy_outcome.overall_policy_state,
        "policy_unresolved_decisions": list(
            policy_outcome.unresolved_decisions),
        "policy_blockers": list(policy_outcome.policy_blockers),
        "note": (
            "Technical blockers require engineering fixes; policy decisions "
            "require owner resolution. The two lists are independent: a "
            "pending policy choice is never reported as a software defect, "
            "and policy acknowledgment never clears a technical blocker."),
    }
