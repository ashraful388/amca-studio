"""I2H tests: Windows signing evidence contract and promotion adapter.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2H-WINDOWS-SIGNING-EVIDENCE-CONTRACT-UNSIGNED-RELEASE-DISCLOSURE-AND-PROMOTION-ADAPTER-PREFLIGHT-01
"""

from pathlib import Path
import json
import zipfile

import pytest

from amca.signing_evidence import (
    POLICY_OPTIONAL,
    POLICY_REQUIRED,
    ST_SIGNED_VALID,
    ST_SIGNATURE_INVALID,
    ST_SIGNING_ERROR,
    ST_SIGNING_REQUIRED_BUT_UNAVAILABLE,
    ST_UNSIGNED_ALLOWED,
    ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT,
    build_signing_evidence,
    promote_adapter,
    verify_evidence_binding,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"


def _audit(installer="NotSigned", backend="NotSigned", desktop="NotSigned",
           signer=None, timestamped=False, source_sha="a" * 40) -> dict:
    def rec(status):
        return {
            "artifact": None,
            "sha256": None,
            "inspection": {"status": status, "signer": signer,
                           "timestamped": timestamped},
        }
    out = {
        "schema": "amca-windows-signing-audit/1",
        "source_sha": source_sha,
        "artifacts": [
            {**rec(installer), "artifact":
                "amca-studio-v0.9.0-windows-x64-setup.exe"},
            {**rec(backend), "artifact": "amca-backend.exe"},
            {**rec(desktop), "artifact": "amca-desktop.exe"},
        ],
    }
    return out


def _hashes_from_evidence(evidence) -> dict:
    return {a["artifact"]: a["sha256"] for a in evidence.artifacts
            if a["sha256"]}


def _with_hashes(audit: dict, artifacts: dict) -> dict:
    """Attach real SHA256 values to the audit records."""
    for art in audit["artifacts"]:
        name = art["artifact"]
        if name in artifacts:
            art["sha256"] = artifacts[name]
    return audit


# ---------------------------------------------------------------------------
# Policy semantics
# ---------------------------------------------------------------------------

def test_unsigned_optional_policy_requires_acknowledgment():
    evidence = build_signing_evidence(_audit(), policy=POLICY_OPTIONAL)
    states = {a["state"] for a in evidence.artifacts}
    assert ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT in states
    assert ST_SIGNED_VALID not in states
    assert evidence.overall_technical_verdict == "REVIEW"


def test_unsigned_required_policy_is_unavailable():
    evidence = build_signing_evidence(_audit(), policy=POLICY_REQUIRED)
    states = {a["state"] for a in evidence.artifacts}
    assert ST_SIGNING_REQUIRED_BUT_UNAVAILABLE in states
    assert evidence.overall_technical_verdict == "BLOCKED"


def test_valid_signed_behavior():
    evidence = build_signing_evidence(
        _audit(installer="Valid", backend="Valid", desktop="Valid",
               signer="CN=AMCA Project", timestamped=True),
        policy=POLICY_REQUIRED,
    )
    states = {a["state"] for a in evidence.artifacts}
    assert states == {ST_SIGNED_VALID}
    assert evidence.overall_technical_verdict == "PASS"
    assert evidence.publisher_identity == "CN=AMCA Project"
    assert evidence.timestamp_status == "PRESENT"


def test_invalid_signature_blocks():
    evidence = build_signing_evidence(
        _audit(installer="HashMismatch", backend="Valid", desktop="Valid",
               signer="CN=AMCA", timestamped=True),
        policy=POLICY_OPTIONAL,
    )
    states = {a["state"] for a in evidence.artifacts}
    assert ST_SIGNATURE_INVALID in states
    assert evidence.overall_technical_verdict == "FAIL"


def test_signing_error_state():
    evidence = build_signing_evidence(
        _audit(installer="INSPECTION_ERROR"), policy=POLICY_OPTIONAL)
    assert ST_SIGNING_ERROR in {a["state"] for a in evidence.artifacts}
    assert evidence.overall_technical_verdict == "FAIL"


def test_unsigned_allowed_state_requires_optional_policy(tmp_path):
    """A policy explicitly accepting unsigned (acknowledged) yields
    UNSIGNED_ALLOWED semantics through the adapter - never SIGNED_VALID."""
    audit = _audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL,
                                      acknowledged=True)
    result = promote_adapter(
        evidence, artifact_hashes=_hashes_from_evidence(evidence))
    assert result["signing_state"] in (
        ST_UNSIGNED_ALLOWED, ST_UNSIGNED_REQUIRES_ACKNOWLEDGMENT)
    assert result["signing_state"] != ST_SIGNED_VALID
    assert result["signing_blocks_promotion"] is False
    assert result["acknowledgment_required"] is False  # already acknowledged


# ---------------------------------------------------------------------------
# Adapter behavior
# ---------------------------------------------------------------------------

def test_adapter_acknowledgment_required_when_not_acknowledged():
    evidence = build_signing_evidence(_audit(), policy=POLICY_OPTIONAL)
    result = promote_adapter(
        evidence, artifact_hashes=_hashes_from_evidence(evidence))
    assert result["acknowledgment_required"] is True
    assert result["read_only"] is True


def test_signer_mismatch_recorded():
    audit = _audit(installer="Valid", backend="Valid", desktop="Valid",
                   signer="CN=Unexpected Publisher", timestamped=True)
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    assert evidence.publisher_identity == "CN=Unexpected Publisher"
    # The promotion adapter surfaces the recorded identity for the gate to
    # compare against the expected publisher.
    result = promote_adapter(evidence,
                             artifact_hashes=_hashes_from_evidence(evidence))
    assert result["evidence_valid_for_promotion"] is True
    doc = evidence.to_document()
    assert doc["publisher_identity"] == "CN=Unexpected Publisher"


def test_stale_evidence_rejected():
    audit = _audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    hashes = _hashes_from_evidence(evidence)
    # Simulate re-hashing after bytes changed: one artifact hash differs.
    changed = dict(hashes)
    changed["amca-backend.exe"] = "f" * 64
    result = promote_adapter(evidence, artifact_hashes=changed)
    assert result["stale_or_mismatched_evidence"] == ["amca-backend.exe"]
    assert result["evidence_valid_for_promotion"] is False


def test_v09_artifact_cannot_certify_v10_artifact():
    """v0.9.0-bound evidence must reject v1.0.0 artifact hashes."""
    audit = _audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    v10_hashes = {a["artifact"]: "1" * 64 for a in evidence.artifacts}
    result = promote_adapter(evidence, artifact_hashes=v10_hashes)
    assert result["evidence_valid_for_promotion"] is False
    assert len(result["stale_or_mismatched_evidence"]) == 3


def test_artifact_hash_mismatch_detected():
    audit = _audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    hashes = _hashes_from_evidence(evidence)
    hashes["amca-desktop.exe"] = "0" * 64
    result = promote_adapter(evidence, artifact_hashes=hashes)
    assert result["evidence_valid_for_promotion"] is False


def test_exact_byte_binding_with_real_files(tmp_path):
    """Evidence bound to real physical bytes; modification breaks binding."""
    exe = tmp_path / "amca-backend.exe"
    exe.write_bytes(b"MZ" + b"B" * 512)
    from amca.distribution_smoke import sha256_file

    original = sha256_file(exe)
    audit = _audit()
    audit["artifacts"] = [{
        "artifact": "amca-backend.exe", "sha256": original,
        "inspection": {"status": "NotSigned", "signer": None,
                       "timestamped": False},
    }]
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    assert verify_evidence_binding(evidence, {"amca-backend.exe": original})

    # Signing (modifying bytes) invalidates the old evidence.
    exe.write_bytes(b"MZ" + b"SIGNED!" * 64)
    assert not verify_evidence_binding(
        evidence, {"amca-backend.exe": sha256_file(exe)})


# ---------------------------------------------------------------------------
# Secrets + conservatism + read-only adapter
# ---------------------------------------------------------------------------

def test_no_certificate_secrets_in_evidence():
    evidence = build_signing_evidence(_audit(), policy=POLICY_OPTIONAL)
    text = json.dumps(evidence.to_document()).lower()
    for forbidden in ("password", "private key", "pfx", "pvk"):
        assert forbidden not in text


def test_adapter_is_read_only(tmp_path):
    """The adapter must not write or modify anything (contract: read-only)."""
    audit = _audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    hashes = _hashes_from_evidence(evidence)
    before = json.dumps(evidence.to_document(), sort_keys=True)
    promote_adapter(evidence, artifact_hashes=hashes)
    promote_adapter(evidence, artifact_hashes=hashes)
    after = json.dumps(evidence.to_document(), sort_keys=True)
    assert before == after


def test_smartscreen_language_conservative():
    from amca.signing_evidence import SMARTSCREEN_LIMITATION

    text = SMARTSCREEN_LIMITATION.lower()
    assert "not guaranteed" in text
    assert "unknown publisher" in text or "smartscreen" in text
    assert "complement to, not a replacement" in text
    for overclaim in ("guarantees warning-free", "no warnings", "trusted by windows"):
        assert overclaim not in text


# ---------------------------------------------------------------------------
# Real evidence build (from the genuine I2G audit path)
# ---------------------------------------------------------------------------

def test_real_evidence_builds_from_live_audit():
    from amca.signing_readiness import run_signing_audit

    if not (SOFTWARE_ROOT / "dist" / "amca-studio-v0.9.0-windows-x64-setup.exe").is_file():
        pytest.skip("real distribution artifacts not present")
    audit = run_signing_audit()
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    assert evidence.schema == "amca-windows-signing-evidence/1"
    assert evidence.overall_technical_verdict in ("REVIEW", "PASS", "FAIL")
    doc = evidence.to_document()
    assert doc["smartscreen_limitation"]
    assert len(evidence.artifacts) >= 3
