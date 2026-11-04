"""AMCA Studio v1.0.0 Final Policy-to-Promotion Decision Matrix Rehearsal (I2K).

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2K-FINAL-POLICY-TO-PROMOTION-DECISION-MATRIX-AND-NO-SIDE-EFFECT-REHEARSAL-01

Proves that the I2J policy contract combines deterministically with the
existing I2E promotion gate semantics, I2H signing adapter, and I2I
orchestration evidence WITHOUT modifying any of them. Read-only: no copying,
no mutation, no promotion. This is the final policy-layer rehearsal before
canonical convergence.

Key invariant (enforced everywhere):
- A policy acknowledgment must never convert a failed certification, bad
  artifact hash, invalid signature under required-signing policy, or
  blocking compliance result into an eligible release.
- An unresolved policy choice is never reported as an application defect.
- Which scenario becomes the real release policy is the owner's decision;
  this matrix does not choose it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from amca.release_policy import (
    POLICY_BLOCKED,
    POLICY_READY,
    DECISION_REQUIRED,
    RELEASE_DESTINATION,
    ReleasePolicyDecision,
    evaluate_policy,
    separate_technical_from_policy,
)

SOFTWARE_ROOT = Path(__file__).resolve().parents[2]

MATRIX_SCHEMA = "amca-final-policy-promotion-matrix/1"

ELIGIBLE = "ELIGIBLE_FOR_TECHNICAL_PROMOTION"
NOT_ELIGIBLE = "NOT_ELIGIBLE"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ScenarioInput:
    """One row of the decision matrix."""

    scenario: str
    signing_policy: Optional[str] = None
    unsigned_disclosure_acknowledged: Optional[bool] = None
    compliance_review_acknowledged: Optional[bool] = None
    linux_exclusion_acknowledged: Optional[bool] = None
    overwrite_authorized: Optional[bool] = None
    signing_states: List[str] = field(default_factory=list)
    technical_blockers: List[str] = field(default_factory=list)
    existing_official_artifacts: bool = False
    compliance_result: str = "PASS_WITH_REVIEW"

    def to_decision(self) -> ReleasePolicyDecision:
        return ReleasePolicyDecision(
            release_version="1.0.0",
            target_source_sha="a" * 40,
            signing_policy=self.signing_policy,
            unsigned_disclosure_acknowledged=self.unsigned_disclosure_acknowledged,
            compliance_review_acknowledged=self.compliance_review_acknowledged,
            linux_exclusion_acknowledged=self.linux_exclusion_acknowledged,
            overwrite_authorized=self.overwrite_authorized,
            destination=RELEASE_DESTINATION,
            decision_timestamp_utc=_utc_now(),
            decision_provenance=f"matrix scenario {self.scenario}",
        )


def evaluate_scenario(scenario: ScenarioInput) -> Dict[str, Any]:
    """Combine one scenario through policy + technical + overwrite layers.

    Read-only. Returns policy result, technical result, signing result,
    compliance result, overwrite result, combined promotion eligibility,
    unresolved decisions, and blockers.
    """
    decision = scenario.to_decision()
    policy = evaluate_policy(
        decision,
        signing_states=scenario.signing_states,
    )

    # ---- Technical layer (independent of policy acknowledgment) ----
    technical_blockers = list(scenario.technical_blockers)
    technical_result = "PASS" if not technical_blockers else "FAIL"

    # ---- Signing layer (I2H states) ----
    unsigned_present = any(
        s in ("UNSIGNED_ALLOWED", "UNSIGNED_REQUIRES_ACKNOWLEDGMENT")
        for s in scenario.signing_states)
    signed_valid = any(s == "SIGNED_VALID" for s in scenario.signing_states)
    if signed_valid:
        signing_result = "SIGNED_VALID"
    elif scenario.signing_policy == "SIGNED_REQUIRED":
        signing_result = "SIGNING_REQUIRED_BUT_UNAVAILABLE" if unsigned_present \
            else "SIGNING_ERROR"
    elif unsigned_present:
        signing_result = (
            "UNSIGNED_ALLOWED"
            if scenario.unsigned_disclosure_acknowledged
            else "UNSIGNED_REQUIRES_ACKNOWLEDGMENT")
    else:
        signing_result = "SIGNING_NOT_EVALUATED"

    # ---- Compliance layer (PASS_WITH_REVIEW preserved) ----
    compliance_result = (
        f"{scenario.compliance_result} "
        f"({'acknowledged' if scenario.compliance_review_acknowledged else 'not acknowledged'}; "
        f"review item preserved)"
    )

    # ---- Overwrite layer (deny by default) ----
    if scenario.existing_official_artifacts and decision.overwrite_authorized:
        overwrite_result = "AUTHORIZED"
    elif scenario.existing_official_artifacts:
        overwrite_result = "BLOCKED_NOT_AUTHORIZED"
    else:
        overwrite_result = "NO_CONFLICT"

    # ---- Combined eligibility (the key invariant, deterministic) ----
    blockers: List[str] = []
    unresolved = list(policy.unresolved_decisions)

    # Compliance review unacknowledged: promotion cannot proceed until the
    # owner acknowledges PASS_WITH_REVIEW (the item itself stays preserved).
    if not scenario.compliance_review_acknowledged:
        blockers.append(
            "compliance: PASS_WITH_REVIEW not acknowledged by the owner")
    # Owner explicitly refuses an unsigned release (acknowledged=False) while
    # the binaries are unsigned under the optional policy: the unsigned
    # release cannot ship (switch to SIGNED_REQUIRED + signing, or reconsider).
    if unsigned_present and scenario.unsigned_disclosure_acknowledged is False:
        blockers.append(
            "policy: owner has not accepted an unsigned release "
            "(disclosure acknowledgment = false)")

    # Technical failures can NEVER be cleared by policy acknowledgment.
    if technical_blockers:
        blockers.extend(f"technical: {b}" for b in technical_blockers)
    # Invalid signature / signing error under required policy: technical.
    if signing_result == "SIGNING_REQUIRED_BUT_UNAVAILABLE" and \
            scenario.signing_policy == "SIGNED_REQUIRED":
        blockers.append(
            "policy: SIGNED_REQUIRED while binaries remain unsigned")
    # Unresolved policy choices: decision required, not a defect.
    if policy.overall_policy_state == DECISION_REQUIRED:
        blockers.append("policy: unresolved owner decisions (decision required, "
                        "not a software defect)")

    eligible = (
        not technical_blockers
        and policy.overall_policy_state == POLICY_READY
        and scenario.compliance_review_acknowledged is True
        and not blockers
    )
    combined = ELIGIBLE if eligible and not blockers else NOT_ELIGIBLE

    # Overwrite conflict blocks the physical promotion step only.
    if scenario.existing_official_artifacts and \
            overwrite_result == "BLOCKED_NOT_AUTHORIZED" and combined == ELIGIBLE:
        combined = NOT_ELIGIBLE
        blockers.append("overwrite: existing official artifacts, not authorized")

    sep = separate_technical_from_policy(technical_blockers, policy)

    return {
        "scenario": scenario.scenario,
        "policy_result": policy.overall_policy_state,
        "technical_result": technical_result,
        "signing_result": signing_result,
        "compliance_result": compliance_result,
        "overwrite_result": overwrite_result,
        "combined_promotion_eligibility": combined,
        "unresolved_decisions": unresolved,
        "blockers": blockers,
        "technical_vs_policy": sep["technical_blockers_overridable_by_policy"] is False,
        "destination": RELEASE_DESTINATION,
        "read_only": True,
    }


def run_decision_matrix() -> Dict[str, Any]:
    """Run the required decision scenarios A-F (read-only, synthetic)."""
    scenarios = [
        # A - Unsigned Windows release permitted: all owner choices resolved,
        #     optional policy with acknowledged disclosure, no technical issue.
        ScenarioInput(
            scenario="A_unsigned_permitted",
            signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
            unsigned_disclosure_acknowledged=True,
            compliance_review_acknowledged=True,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=False,
            signing_states=["UNSIGNED_ALLOWED"],
            technical_blockers=[],
        ),
        # B - Signing required, binaries unsigned.
        ScenarioInput(
            scenario="B_signed_required_unsigned",
            signing_policy="SIGNED_REQUIRED",
            unsigned_disclosure_acknowledged=True,
            compliance_review_acknowledged=True,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=False,
            signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"],
            technical_blockers=[],
        ),
        # C - Optional unsigned, disclosure not acknowledged.
        ScenarioInput(
            scenario="C_disclosure_not_acknowledged",
            signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
            unsigned_disclosure_acknowledged=False,
            compliance_review_acknowledged=True,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=False,
            signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"],
            technical_blockers=[],
        ),
        # D - Compliance review not acknowledged.
        ScenarioInput(
            scenario="D_compliance_not_acknowledged",
            signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
            unsigned_disclosure_acknowledged=True,
            compliance_review_acknowledged=False,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=False,
            signing_states=["UNSIGNED_ALLOWED"],
            technical_blockers=[],
        ),
        # E - Technical certification/hash failure with ALL acknowledgments:
        #     policy can never rescue it.
        ScenarioInput(
            scenario="E_technical_failure_with_full_acknowledgment",
            signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
            unsigned_disclosure_acknowledged=True,
            compliance_review_acknowledged=True,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=True,
            signing_states=["UNSIGNED_ALLOWED"],
            technical_blockers=["certification failure", "bad artifact hash"],
        ),
        # F - Existing official artifacts + overwrite not authorized.
        ScenarioInput(
            scenario="F_overwrite_not_authorized",
            signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
            unsigned_disclosure_acknowledged=True,
            compliance_review_acknowledged=True,
            linux_exclusion_acknowledged=True,
            overwrite_authorized=False,
            signing_states=["UNSIGNED_ALLOWED"],
            technical_blockers=[],
            existing_official_artifacts=True,
        ),
    ]
    results = [evaluate_scenario(s) for s in scenarios]

    expected = {
        "A_unsigned_permitted": ELIGIBLE,
        "B_signed_required_unsigned": NOT_ELIGIBLE,
        "C_disclosure_not_acknowledged": NOT_ELIGIBLE,
        "D_compliance_not_acknowledged": NOT_ELIGIBLE,
        "E_technical_failure_with_full_acknowledgment": NOT_ELIGIBLE,
        "F_overwrite_not_authorized": NOT_ELIGIBLE,
    }
    matrix_ok = all(
        r["combined_promotion_eligibility"] == expected[r["scenario"]]
        for r in results
    )

    return {
        "schema": MATRIX_SCHEMA,
        "generated_at_utc": _utc_now(),
        "matrix": results,
        "expected_outcomes_matched": matrix_ok,
        "key_invariant": (
            "policy acknowledgment never converts failed certification, bad "
            "artifact hash, invalid signature under required-signing policy, "
            "or blocking compliance result into an eligible release; an "
            "unresolved policy choice is never reported as an application "
            "defect"),
        "decision_table": {
            "DECISION_REQUIRED": [
                "optional signing without disclosure acknowledgment",
                "compliance PASS_WITH_REVIEW not acknowledged",
                "any undecided owner choice",
            ],
            "POLICY_BLOCKED": [
                "SIGNED_REQUIRED while binaries remain unsigned",
                "technical failure (independent of policy)",
            ],
            "ELIGIBLE_FOR_TECHNICAL_PROMOTION": [
                "optional signing + disclosure acknowledged + compliance "
                "acknowledged + Linux excluded/acknowledged + no technical "
                "blocker (+ overwrite authorized where artifacts exist)",
            ],
        },
        "no_scenarios_chosen_for_release": True,
        "side_effects": "none - read-only matrix",
        "secrets_emitted": False,
        "destination": RELEASE_DESTINATION,
    }
