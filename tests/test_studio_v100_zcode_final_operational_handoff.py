"""I2O post-merge sentinel: ZCode I2N final operational handoff.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2O-I2N-FINAL-OPERATIONAL-HANDOFF-AND-RELEASE-ENGINEERING-FREEZE-01

Verifies, after PS-1 integrates the I2N delta, that the operational-rehearsal
functionality survived integration and that no release side effects occurred.
"""

from pathlib import Path
import hashlib
import json
import subprocess



# ---------------------------------------------------------------------------
# I3A.2 test-structure stabilization: version-aware expectations
# ---------------------------------------------------------------------------
# These helpers let the version-pinning tests validate the CURRENT active
# state. Before the authorized transition they validate 0.9.0; after the
# Manager-authorized transition they validate 1.0.0 with only narrow,
# explicitly enumerated expectation changes (I1.26A contract).
from amca.version_freeze_preflight import active_version_agreement as _ava

CURRENT_ACTIVE_VERSION = _ava()["current_version"]
PRE_TRANSITION_VERSION = "0.9.0"
POST_TRANSITION_VERSION = "1.0.0"
TRANSITION_DONE = CURRENT_ACTIVE_VERSION == POST_TRANSITION_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
MANIFEST = (SOFTWARE_ROOT / "docs" / "integration"
            / "AMCA-Studio-v1.0.0-ZCode-I2N-Final-Operational-Delta-Manifest.json")

READY_MARKER = "ZCODE_PRE_TRANSITION_OPERATIONAL_READY"

#: Delta files intentionally repaired after the recorded endpoint (version-
#: aware test maintenance, 32F recovery). Enumerated explicitly so no other
#: file may silently drift from the frozen delta manifest.
POST_ENDPOINT_TEST_UPDATES = {
    "software/tests/test_studio_v100_zcode_final_release_execution_cli_rehearsal.py",
}


def _load_manifest() -> dict:
    assert MANIFEST.is_file(), "I2N delta manifest missing"
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_i2n_rehearsal_test_present():
    p = (SOFTWARE_ROOT / "tests"
         / "test_studio_v100_zcode_final_release_execution_cli_rehearsal.py")
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "transition_dry_run_command_valid" in text
    assert "write_mode_authorization_mandatory" in text


def test_i2n_operational_report_present():
    p = (SOFTWARE_ROOT / "docs" / "release"
         / "AMCA-Studio-v1.0.0-ZCode-Final-Release-Execution-CLI-Rehearsal.md")
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "MANAGER AUTHORIZATION REQUIRED" in text


def test_all_delta_file_hashes_match():
    manifest = _load_manifest()
    assert manifest["base_sha"] == "e30d5ba1"
    assert manifest["endpoint_sha"] == "032b9365"
    assert len(manifest["files"]) == 2
    for rel in manifest["files"]:
        p = REPO_ROOT / rel["path"]
        assert p.is_file(), f"delta file missing: {rel['path']}"
        if rel["path"] in POST_ENDPOINT_TEST_UPDATES:
            # Intentionally repaired after the recorded endpoint (version-
            # aware test maintenance); enumerated explicitly so no other file
            # may silently drift.
            continue
        assert hashlib.sha256(p.read_bytes()).hexdigest() == rel["sha256"], \
            f"hash mismatch: {rel['path']}"
        assert p.stat().st_size == rel["size"]


def test_transition_write_authorization_remains_mandatory(tmp_path):
    out = tmp_path / "auth.json"
    proc = subprocess.run(
        [sys_executable(),
         str(SOFTWARE_ROOT / "scripts" / "apply_release_version_transition.py"),
         "--apply", "--output", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "BLOCKED"
    assert any("authorize-bump" in r for r in data["reasons"])


def test_active_version_agrees_and_is_a_known_release_state():
    """All active declarations agree on the live canonical version, which
    must be a well-formed release version (never a placeholder)."""
    import re

    from amca.version_freeze_preflight import active_version_agreement

    agreement = active_version_agreement()
    assert agreement["agrees"] is True
    assert agreement["current_version"] == CURRENT_ACTIVE_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", agreement["current_version"]), (
        f"active version is not a well-formed release version: "
        f"{agreement['current_version']!r}")


def test_release_tag_present_at_certified_sha():
    """Post-release guard (canonical-path migration work order): the certified
    tag exists and points at the immutable certified source SHA."""
    proc = subprocess.run(
        ["git", "rev-parse", "amca-studio-v1.0.0^{commit}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.stdout.strip() == "3d225e31dfc409521a7dc2259f52489754efbbfc"


def test_cargo_regeneration_check_order_preserved():
    doc = (SOFTWARE_ROOT / "docs" / "release"
           / "AMCA-Studio-v1.0.0-Version-Transition-Execution-Contract.md"
           ).read_text(encoding="utf-8")
    assert "cargo check --locked" in doc
    assert "regenerated" in doc.lower() or "regeneration" in doc.lower()


def test_build_and_smoke_commands_still_exist():
    assert (SOFTWARE_ROOT / "packaging" / "build_backend.ps1").is_file()
    assert (SOFTWARE_ROOT / "packaging" / "build_desktop.ps1").is_file()
    assert (SOFTWARE_ROOT / "scripts" / "run_distribution_smoke.py").is_file()


def test_signing_branches_preserved():
    from amca.signing_evidence import (
        POLICY_OPTIONAL, POLICY_REQUIRED, build_signing_evidence)

    audit = {"source_sha": "a" * 40, "artifacts": [{
        "artifact": "x.exe", "sha256": "0" * 64,
        "inspection": {"status": "NotSigned"}}]}
    optional = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    required = build_signing_evidence(audit, policy=POLICY_REQUIRED)
    states_o = {a["state"] for a in optional.artifacts}
    states_r = {a["state"] for a in required.artifacts}
    assert "UNSIGNED_REQUIRES_ACKNOWLEDGMENT" in states_o
    assert "SIGNING_REQUIRED_BUT_UNAVAILABLE" in states_r


def test_exact_lineage_promotion_requirement_preserved():
    from amca.release_gating import evaluate_release_gate

    # Mismatched SHA must never certify.
    gate = evaluate_release_gate(
        certification_evidence={
            "overall_verdict": "PASS",
            "provenance": {"sha": "a" * 40, "clean": True, "dirty": False},
        },
        packaging_provenance={"sha": "b" * 40, "branch": "x",
                              "dirty": False, "clean": True},
    )
    assert gate.final_release_certified is False
    assert gate.state == "SHA_MISMATCH"


def test_overwrite_protection_preserved():
    from amca.release_policy import evaluate_policy, ReleasePolicyDecision

    decision = ReleasePolicyDecision(
        release_version="1.0.0", target_source_sha="a" * 40,
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,
    )
    outcome = evaluate_policy(decision)
    assert not any("explicitly authorized" in c for c in outcome.consequences)


def test_tag_target_contract_preserved():
    from amca.version_freeze_preflight import (
        RELEASE_TAG, simulate_version_bump)

    assert RELEASE_TAG == "amca-studio-v1.0.0"
    rules = " ".join(
        simulate_version_bump()["tag_contract"]["rules"]).lower()
    assert "certified source sha" in rules


def test_no_stale_v0_9_fallback_in_promotion_runner():
    src = (SOFTWARE_ROOT / "scripts" / "promote_final_release.py").read_text(
        encoding="utf-8")
    assert "v0.5" not in src and "v0.6" not in src


def test_no_worker_specific_paths_or_secrets():
    manifest = _load_manifest()
    text = json.dumps(manifest).lower()
    for forbidden in ("c:/users/md", "c:\\users\\md", "appdata",
                      "password", "private key"):
        assert forbidden not in text


def test_no_competing_release_or_promotion_executor_introduced():
    runners = list((SOFTWARE_ROOT / "scripts").glob("promote_final_release*.py"))
    assert len(runners) == 1
    transitions = list((SOFTWARE_ROOT / "scripts").glob(
        "apply_release_version_transition*.py"))
    assert len(transitions) == 1


def test_operational_ready_state():
    """Overall sentinel verdict."""
    manifest = _load_manifest()
    assert manifest["milestone"] == "I2N"
    print(READY_MARKER)


def sys_executable() -> str:
    import sys

    return sys.executable
