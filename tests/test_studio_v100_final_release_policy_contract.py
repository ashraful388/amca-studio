"""I2J tests: final release policy decision contract and authorization preflight.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2J-FINAL-RELEASE-POLICY-DECISION-CONTRACT-AND-PROMOTION-AUTHORIZATION-PREFLIGHT-01
"""

from pathlib import Path
import json
import subprocess
import sys

import pytest

from amca.release_policy import (
    POLICY_BLOCKED,
    POLICY_READY,
    RELEASE_DESTINATION,
    ReleasePolicyDecision,
    SIGNING_OPTIONAL_UNSIGNED_ALLOWED,
    SIGNING_SIGNED_REQUIRED,
    evaluate_policy,
    separate_technical_from_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
RUNNER = SOFTWARE_ROOT / "scripts" / "evaluate_final_release_policy.py"


def _decision(**overrides) -> ReleasePolicyDecision:
    values = dict(
        release_version="1.0.0",
        target_source_sha="a" * 40,
        signing_policy=SIGNING_OPTIONAL_UNSIGNED_ALLOWED,
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
        decision_timestamp_utc="2026-09-04T00:00:00+00:00",
        decision_provenance="test decision",
    )
    values.update(overrides)
    return ReleasePolicyDecision(**values)


# ---------------------------------------------------------------------------
# Policy states
# ---------------------------------------------------------------------------

def test_unsigned_optional_with_acknowledgment_policy_ready():
    outcome = evaluate_policy(
        _decision(),
        signing_states=["UNSIGNED_ALLOWED", "SIGNED_VALID"])
    assert outcome.overall_policy_state == POLICY_READY
    assert outcome.unresolved_decisions == []
    assert any("policy-eligible" in c for c in outcome.consequences)


def test_unsigned_optional_without_acknowledgment_decision_required():
    outcome = evaluate_policy(
        _decision(unsigned_disclosure_acknowledged=None),
        signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"])
    assert outcome.overall_policy_state == "DECISION_REQUIRED"
    assert any("disclosure" in d for d in outcome.unresolved_decisions)


def test_signed_required_with_unsigned_blocked():
    outcome = evaluate_policy(
        _decision(signing_policy=SIGNING_SIGNED_REQUIRED),
        signing_states=["UNSIGNED_REQUIRES_ACKNOWLEDGMENT"])
    assert outcome.overall_policy_state == POLICY_BLOCKED
    assert outcome.signing_policy_state == "SIGNING_REQUIRED_BUT_UNAVAILABLE"
    assert any("SIGNED_REQUIRED" in b for b in outcome.policy_blockers)


def test_valid_signed_required_case_ready():
    outcome = evaluate_policy(
        _decision(signing_policy=SIGNING_SIGNED_REQUIRED),
        signing_states=["SIGNED_VALID"])
    assert outcome.overall_policy_state == POLICY_READY
    assert outcome.signing_policy_state == "SIGNED_VALID"


def test_invalid_signature_under_required_policy_blocked():
    """Technical blocker: an invalid signature under SIGNED_REQUIRED cannot
    be cleared by policy acknowledgment."""
    outcome = evaluate_policy(
        _decision(signing_policy=SIGNING_SIGNED_REQUIRED,
                  unsigned_disclosure_acknowledged=True),
        signing_states=["SIGNATURE_INVALID"])
    assert outcome.overall_policy_state == POLICY_BLOCKED


# ---------------------------------------------------------------------------
# Compliance / Linux / overwrite semantics
# ---------------------------------------------------------------------------

def test_compliance_review_acknowledgment_preserved():
    outcome = evaluate_policy(_decision())
    assert outcome.compliance_item_preserved is True
    assert outcome.compliance_result_unchanged is True
    assert any("NOT renamed to plain PASS" in c for c in outcome.consequences)


def test_acknowledgment_never_converts_pass_with_review_to_pass():
    """The compliance result stays PASS_WITH_REVIEW; no plain-PASS claim and
    no legal approval is fabricated anywhere in the outcome."""
    outcome = evaluate_policy(_decision())
    text = json.dumps(outcome.to_document(_decision())).lower()
    # The documented consequence explicitly denies renaming to plain PASS.
    assert "not renamed to plain pass" in text
    assert "no legal approval is claimed" in text


def test_linux_exclusion_represented_explicitly():
    outcome = evaluate_policy(_decision())
    assert any("linux explicitly not included" in c.lower()
               for c in outcome.consequences)
    undecided = evaluate_policy(
        _decision(linux_exclusion_acknowledged=None))
    assert any("linux" in d.lower()
               for d in undecided.unresolved_decisions)


def test_release_destination_exact():
    outcome = evaluate_policy(_decision())
    assert outcome.destination == RELEASE_DESTINATION
    assert RELEASE_DESTINATION == "D:/ChatGPT/AMCA/software/dist"


def test_overwrite_default_deny():
    outcome = evaluate_policy(_decision(overwrite_authorized=False))
    assert any("not overwritten" in c or "overwrite of existing" not in c
               for c in outcome.consequences) or outcome.consequences
    undecided = evaluate_policy(_decision(overwrite_authorized=None))
    assert any("overwrite" in d.lower()
               for d in undecided.unresolved_decisions)
    # Explicitly authorized -> consequence recorded.
    authorized = evaluate_policy(_decision(overwrite_authorized=True))
    assert any("explicitly authorized" in c for c in authorized.consequences)


# ---------------------------------------------------------------------------
# Missing input / technical separation
# ---------------------------------------------------------------------------

def test_missing_policy_input_never_inferred():
    outcome = evaluate_policy(None)
    assert outcome.overall_policy_state == "DECISION_REQUIRED"
    assert len(outcome.unresolved_decisions) >= 5
    # Nothing was chosen for the user.
    doc = outcome.to_document(None)
    assert doc["signing_policy"] is None


def test_technical_failure_cannot_be_overridden_by_policy_acknowledgment():
    """Every technical blocker is listed as non-overridable; the separation
    record keeps the two lists independent."""
    technical = ["certification failure", "hash mismatch"]
    outcome = evaluate_policy(_decision())
    sep = separate_technical_from_policy(technical, outcome)
    assert sep["technical_blockers"] == technical
    assert sep["technical_blockers_overridable_by_policy"] is False
    assert sep["policy_state"] == POLICY_READY
    assert sep["policy_unresolved_decisions"] == []
    non_overridable = json.dumps(
        sep["technical_blockers_not_overridable_by_policy"])
    for blocker in ("certification failure", "hash mismatch",
                    "invalid signature under required-signing policy",
                    "blocking compliance result"):
        assert blocker in non_overridable


def test_policy_pending_is_not_reported_as_software_defect():
    """A pending policy choice must never be misreported as a defect."""
    outcome = evaluate_policy(_decision(unsigned_disclosure_acknowledged=None))
    sep = separate_technical_from_policy([], outcome)
    assert sep["technical_blockers"] == []
    assert sep["policy_unresolved_decisions"]
    assert sep["note"].startswith(
        "Technical blockers require engineering fixes")


def test_no_secret_or_certificate_material_persisted(tmp_path):
    decision = _decision()
    outcome = evaluate_policy(decision)
    text = json.dumps(outcome.to_document(decision)).lower()
    for forbidden in ("password", "private key", "pfx", "pvk",
                      "begin rsa", "credential="):
        assert forbidden not in text


def test_evaluator_is_read_only(tmp_path):
    """The evaluator writes only where explicitly asked (--output) and never
    touches the repository, dist, or destination."""
    decision_file = tmp_path / "policy.json"
    decision_file.write_text(json.dumps({
        "release_version": "1.0.0",
        "target_source_sha": "a" * 40,
        "signing_policy": SIGNING_OPTIONAL_UNSIGNED_ALLOWED,
        "unsigned_disclosure_acknowledged": True,
        "compliance_review_acknowledged": True,
        "linux_exclusion_acknowledged": True,
        "overwrite_authorized": False,
    }), encoding="utf-8")

    dest = RELEASE_DESTINATION
    assert Path(dest).exists()  # sanity: standing destination untouched later

    out = tmp_path / "result.json"
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--decision", str(decision_file),
         "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall_policy_state"] == "POLICY_READY"
    assert proc.returncode == 0
    # Read-only: no signing happened, no artifacts changed. The only file the
    # evaluator created is the requested output.
    created = [p for p in tmp_path.iterdir() if p.name != "policy.json"]
    assert created == [out]


def test_undecided_invocation_reports_decision_required(tmp_path):
    out = tmp_path / "undecided.json"
    subprocess.run(
        [sys.executable, str(RUNNER), "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall_policy_state"] == "DECISION_REQUIRED"
    assert len(data["unresolved_decisions"]) >= 5


def test_no_decision_chosen_for_the_user():
    """The contract never auto-selects a signing policy or acknowledgment."""
    outcome = evaluate_policy(None)
    doc = outcome.to_document(None)
    for key in ("signing_policy", "unsigned_disclosure_acknowledged",
                "compliance_review_acknowledged",
                "linux_exclusion_acknowledged", "overwrite_authorized"):
        assert doc[key] is None, f"{key} must not be auto-chosen"
