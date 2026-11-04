"""I2K tests: final policy-to-promotion decision matrix and no-side-effect rehearsal.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2K-FINAL-POLICY-TO-PROMOTION-DECISION-MATRIX-AND-NO-SIDE-EFFECT-REHEARSAL-01
"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest

from amca.policy_promotion_matrix import (
    ELIGIBLE,
    NOT_ELIGIBLE,
    ScenarioInput,
    evaluate_scenario,
    run_decision_matrix,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
RUNNER = SOFTWARE_ROOT / "scripts" / "run_final_release_policy_promotion_matrix.py"

# ---------------------------------------------------------------------------
# Required decision scenarios A-F
# ---------------------------------------------------------------------------

def test_scenario_a_unsigned_permitted_eligible():
    r = evaluate_scenario(ScenarioInput(
        scenario="A",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_ALLOWED"],
        technical_blockers=[],
    ))
    assert r["combined_promotion_eligibility"] == ELIGIBLE
    assert r["policy_result"] == "POLICY_READY"
    assert r["technical_result"] == "PASS"


def test_scenario_b_signed_required_unsigned_blocked():
    r = evaluate_scenario(ScenarioInput(
        scenario="B",
        signing_policy="SIGNED_REQUIRED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"],
        technical_blockers=[],
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert r["signing_result"] == "SIGNING_REQUIRED_BUT_UNAVAILABLE"


def test_scenario_c_disclosure_not_acknowledged_decision_required():
    r = evaluate_scenario(ScenarioInput(
        scenario="C",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=False,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"],
        technical_blockers=[],
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert r["policy_result"] == "POLICY_READY"  # choices made, but refusal
    assert any("unsigned release" in b for b in r["blockers"])


def test_scenario_d_compliance_not_acknowledged_decision_required():
    r = evaluate_scenario(ScenarioInput(
        scenario="D",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=False,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_ALLOWED"],
        technical_blockers=[],
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert any("PASS_WITH_REVIEW" in b for b in r["blockers"])


def test_scenario_e_technical_failure_blocks_despite_full_acknowledgment():
    r = evaluate_scenario(ScenarioInput(
        scenario="E",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=True,
        signing_states=["UNSIGNED_ALLOWED"],
        technical_blockers=["certification failure", "bad artifact hash"],
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert r["technical_result"] == "FAIL"
    # The key invariant: acknowledgment never rescues a technical failure.
    assert any("technical: certification failure" in b for b in r["blockers"])
    assert any("technical: bad artifact hash" in b for b in r["blockers"])


def test_scenario_f_overwrite_not_authorized_blocks():
    r = evaluate_scenario(ScenarioInput(
        scenario="F",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_ALLOWED"],
        technical_blockers=[],
        existing_official_artifacts=True,
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert r["overwrite_result"] == "BLOCKED_NOT_AUTHORIZED"


def test_full_matrix_matches_expected_outcomes():
    result = run_decision_matrix()
    assert result["expected_outcomes_matched"] is True
    assert len(result["matrix"]) == 6


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_unsigned_optional_path_distinct_from_signed_valid():
    optional = evaluate_scenario(ScenarioInput(
        scenario="opt",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["UNSIGNED_ALLOWED"],
    ))
    signed = evaluate_scenario(ScenarioInput(
        scenario="signed",
        signing_policy="SIGNED_REQUIRED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        signing_states=["SIGNED_VALID"],
    ))
    assert optional["signing_result"] == "UNSIGNED_ALLOWED"
    assert signed["signing_result"] == "SIGNED_VALID"
    assert optional["signing_result"] != signed["signing_result"]
    # Both may be eligible, but the states are distinct artifacts of policy.
    assert optional["combined_promotion_eligibility"] == \
        signed["combined_promotion_eligibility"] == ELIGIBLE


def test_pass_with_review_remains_visible_after_acknowledgment():
    r = evaluate_scenario(ScenarioInput(
        scenario="preserve",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        signing_states=["UNSIGNED_ALLOWED"],
    ))
    text = json.dumps(r)
    assert "PASS_WITH_REVIEW" in text
    assert "preserved" in text


def test_linux_exclusion_does_not_affect_windows_technical_certification():
    ack = evaluate_scenario(ScenarioInput(
        scenario="linux-ack",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        signing_states=["UNSIGNED_ALLOWED"],
    ))
    no_ack = evaluate_scenario(ScenarioInput(
        scenario="linux-noack",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=False,
        signing_states=["UNSIGNED_ALLOWED"],
    ))
    # Technical result identical; only the policy blockers differ.
    assert ack["technical_result"] == no_ack["technical_result"] == "PASS"
    assert no_ack["combined_promotion_eligibility"] == NOT_ELIGIBLE


def test_destination_remains_exact():
    r = run_decision_matrix()
    for row in r["matrix"]:
        assert row["destination"] == "D:/ChatGPT/AMCA/software/dist"


def test_overwrite_remains_deny_by_default():
    r = evaluate_scenario(ScenarioInput(
        scenario="default-deny",
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=None,  # undecided
        signing_states=["UNSIGNED_ALLOWED"],
        existing_official_artifacts=True,
    ))
    assert r["combined_promotion_eligibility"] == NOT_ELIGIBLE
    # Undecided overwrite is an unresolved decision, never auto-allowed.
    assert any("overwrite" in d.lower() or "undecided" in d.lower()
               for d in r["unresolved_decisions"]) or \
        r["overwrite_result"] == "BLOCKED_NOT_AUTHORIZED"


def test_no_policy_values_inferred():
    undecided = evaluate_scenario(ScenarioInput(
        scenario="undecided",
    ))
    # With nothing decided, the scenario must not be eligible and must list
    # unresolved decisions.
    assert undecided["combined_promotion_eligibility"] == NOT_ELIGIBLE
    assert undecided["unresolved_decisions"]


def test_no_secret_or_certificate_material_emitted():
    result = run_decision_matrix()
    text = json.dumps(result).lower()
    for forbidden in ("password", "private key", "pfx", "pvk", "api_key"):
        assert forbidden not in text
    assert result["secrets_emitted"] is False


def test_runner_is_side_effect_free(tmp_path):
    """Runner creates only its output file; repository/dist untouched."""
    out = tmp_path / "matrix.json"
    dist_before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((SOFTWARE_ROOT / "dist").iterdir()) if p.is_file()
    }
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    dist_after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((SOFTWARE_ROOT / "dist").iterdir()) if p.is_file()
    }
    assert dist_before == dist_after
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "amca-final-policy-promotion-matrix/1"
    assert data["expected_outcomes_matched"] is True
    assert proc.returncode == 0
    assert data["side_effects"] == "none - read-only matrix"
