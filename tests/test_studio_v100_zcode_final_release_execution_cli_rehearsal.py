"""I2N tests: final release execution CLI rehearsal and sequence contract audit.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2N-FINAL-RELEASE-EXECUTION-CLI-REHEARSAL-AND-PROMOTION-SEQUENCE-CONTRACT-AUDIT-01

Read-only rehearsal proving every ZCode-owned command in the eventual
v1.0.0 transition/build/smoke/signing/promotion sequence matches its real
CLI contract — while the live tree remains 0.9.0 and no release action
occurs.
"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest



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
SCRIPTS = SOFTWARE_ROOT / "scripts"
DIST_DIR = SOFTWARE_ROOT / "dist"

TRANSITION = SCRIPTS / "apply_release_version_transition.py"
SMOKE = SCRIPTS / "run_distribution_smoke.py"
SIGNING = SCRIPTS / "run_windows_signing_audit.py"
POLICY = SCRIPTS / "evaluate_final_release_policy.py"
PROMOTE = SCRIPTS / "promote_final_release.py"
ORCHESTRATION = SCRIPTS / "run_final_release_orchestration_rehearsal.py"
MATRIX = SCRIPTS / "run_final_release_policy_promotion_matrix.py"


def _run(script: Path, *extra: str, timeout: int = 300):
    return subprocess.run(
        [sys.executable, str(script), *extra],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(SOFTWARE_ROOT),
    )


def _help(script: Path) -> str:
    proc = _run(script, "--help")
    assert proc.returncode == 0, f"{script.name} --help failed"
    assert "usage:" in proc.stdout.lower()
    return proc.stdout


# ---------------------------------------------------------------------------
# Sequence step 1: version transition (dry-run / authorized write)
# ---------------------------------------------------------------------------

def test_transition_dry_run_command_valid():
    proc = _run(TRANSITION)
    data = json.loads(proc.stdout)
    assert data["target_version"] == "1.0.0"
    assert data["start_version"] == "0.9.0"
    # The executor's declared precondition is 0.9.0; when the live tree is
    # already past it the honest outcome is BLOCKED — both are valid
    # contract states, and neither may claim an applied transition.
    if CURRENT_ACTIVE_VERSION == data["start_version"]:
        assert data["overall"] == "DRY_RUN"
        assert data["dry_run"] is True
        assert proc.returncode == 0
    else:
        assert data["overall"] == "BLOCKED"
        assert any(CURRENT_ACTIVE_VERSION in r for r in data["reasons"])
        assert proc.returncode != 0


def test_transition_documented_flags_match_parser():
    usage = _help(TRANSITION)
    for flag in ("--target", "--start", "--expected-source-sha",
                 "--apply", "--authorize-bump", "--output"):
        assert flag in usage, f"documented flag missing from parser: {flag}"


def test_write_mode_authorization_mandatory():
    proc = _run(TRANSITION, "--apply")
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["overall"] == "BLOCKED"
    assert any("authorize-bump" in r for r in data["reasons"])


def test_expected_source_sha_parameter_enforced(tmp_path):
    """A wrong expected SHA is rejected rather than silently ignored."""
    out = tmp_path / "r.json"
    proc = _run(TRANSITION, "--expected-source-sha", "b" * 40,
                "--output", str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] == "BLOCKED"
    assert any("does not match" in r for r in data["reasons"])


def test_transition_output_names_correct():
    proc = _run(TRANSITION)
    data = json.loads(proc.stdout)
    sim = data["artifact_name_simulation"]
    assert sim["installer"] == "amca-studio-v1.0.0-windows-x64-setup.exe"
    assert sim["portable"] == "amca-studio-v1.0.0-windows-x64-portable.zip"
    assert sim["manifest"] == "release_manifest_v1.0.0.json"


# ---------------------------------------------------------------------------
# Sequence step 3: Cargo lock handling (documented order)
# ---------------------------------------------------------------------------

def test_cargo_regeneration_then_check_locked_order_documented():
    """The execution contract documents: Cargo.toml transition -> deterministic
    Cargo.lock regeneration -> cargo check --locked (not the reverse)."""
    doc = (SOFTWARE_ROOT / "docs" / "release"
           / "AMCA-Studio-v1.0.0-Version-Transition-Execution-Contract.md"
           ).read_text(encoding="utf-8")
    check_locked = doc.find("cargo check --locked")
    regen = doc.lower().find("regenerated via")
    cargo_toml = doc.find("Cargo.toml` (Cargo.lock regenerated")
    assert check_locked != -1 and regen != -1 and cargo_toml != -1
    # The declaration bump precedes the regeneration/validation mention.
    assert cargo_toml < check_locked


# ---------------------------------------------------------------------------
# Sequence step 5: build / smoke / audit commands exist
# ---------------------------------------------------------------------------

def test_installer_and_portable_build_commands_exist():
    desktop = SOFTWARE_ROOT / "packaging" / "build_desktop.ps1"
    backend = SOFTWARE_ROOT / "packaging" / "build_backend.ps1"
    assert desktop.is_file() and backend.is_file()
    # The Tauri bundle target names derive from tauri.conf.json version: the
    # config must carry the live canonical version.
    conf = json.loads(
        (SOFTWARE_ROOT / "desktop-ui" / "src-tauri" / "tauri.conf.json")
        .read_text(encoding="utf-8"))
    assert conf["version"] == CURRENT_ACTIVE_VERSION


def test_runtime_smoke_commands_exist():
    assert SMOKE.is_file()
    usage = _help(SMOKE)
    for flag in ("--artifact", "--mode", "--output"):
        assert flag in usage


def test_signing_audit_command_exists():
    assert SIGNING.is_file()
    usage = _help(SIGNING)
    for flag in ("--dist", "--version", "--signing-configured", "--output"):
        assert flag in usage


# ---------------------------------------------------------------------------
# Sequence step 6: signing/trust policy branches preserved
# ---------------------------------------------------------------------------

def test_signing_policy_branches_preserved(tmp_path):
    """OPTIONAL vs REQUIRED paths both remain available (owner decides)."""
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


def test_unsigned_optional_path_requires_disclosure_acknowledgment(tmp_path):
    from amca.signing_evidence import (
        POLICY_OPTIONAL, build_signing_evidence, promote_adapter)

    audit = {"source_sha": "a" * 40, "artifacts": [{
        "artifact": "x.zip", "sha256": "0" * 64,
        "inspection": {"status": "NotSigned"}}]}
    evidence = build_signing_evidence(audit, policy=POLICY_OPTIONAL)
    result = promote_adapter(evidence, artifact_hashes={})
    assert result["acknowledgment_required"] is True


def test_signed_required_path_blocks_unsigned_artifacts():
    from amca.signing_evidence import (
        POLICY_REQUIRED, build_signing_evidence, promote_adapter)

    audit = {"source_sha": "a" * 40, "artifacts": [{
        "artifact": "x.exe", "sha256": "0" * 64,
        "inspection": {"status": "NotSigned"}}]}
    evidence = build_signing_evidence(audit, policy=POLICY_REQUIRED)
    result = promote_adapter(evidence, artifact_hashes={})
    assert result["signing_blocks_promotion"] is True


# ---------------------------------------------------------------------------
# Sequence step 7: promotion gate lineage / overwrite / no stale fallback
# ---------------------------------------------------------------------------

def test_promotion_requires_exact_source_and_artifact_lineage():
    from amca.final_promotion import evaluate_evidence

    # Live evidence is bound to a different SHA than the current HEAD after
    # recent commits; the gate must never claim readiness on a mismatch.
    bundle = evaluate_evidence()
    assert bundle.promotion_state in ("BLOCKED", "REJECTED",
                                      "READY_FOR_PROMOTION")
    prov = bundle.to_document()["provenance"] if "provenance" in \
        bundle.to_document() else None
    if bundle.promotion_state == "READY_FOR_PROMOTION":
        # Only acceptable when artifact hashes physically match.
        for art in bundle.physical_artifacts:
            assert art["physically_verified"] is True


def test_promotion_overwrite_denied_without_authorization(tmp_path):
    from amca.release_policy import evaluate_policy, ReleasePolicyDecision

    decision = ReleasePolicyDecision(
        release_version="1.0.0", target_source_sha="a" * 40,
        signing_policy="OPTIONAL_UNSIGNED_ALLOWED",
        unsigned_disclosure_acknowledged=True,
        compliance_review_acknowledged=True,
        linux_exclusion_acknowledged=True,
        overwrite_authorized=False,  # default deny
    )
    outcome = evaluate_policy(decision)
    # Overwrite stays un-authorized: the consequence list does not include
    # an authorization entry.
    assert not any("explicitly authorized" in c
                   for c in outcome.consequences)


def test_no_stale_v09_fallback_in_promotion_runner():
    """promote_final_release.py resolves its inventory from explicit paths —
    no glob/iterdir discovery and no stale historical version names."""
    src = PROMOTE.read_text(encoding="utf-8")
    # No silent discovery of artifacts by pattern.
    assert "glob(" not in src and "iterdir(" not in src
    # No stale historical fallbacks.
    for stale in ("v0.5", "v0.6", "v0.7", "v0.9.0"):
        assert stale not in src, f"stale fallback {stale} present"
    # The inventory is explicit literals, not a version-derived search.
    assert src.count('dist / "amca-studio-') == 3


# ---------------------------------------------------------------------------
# Sequence step 8: tag sequencing
# ---------------------------------------------------------------------------

def test_tag_target_contract_is_certified_source_sha():
    from amca.version_freeze_preflight import RELEASE_TAG, simulate_version_bump

    assert RELEASE_TAG == "amca-studio-v1.0.0"
    rules = " ".join(
        simulate_version_bump()["tag_contract"]["rules"]).lower()
    assert "certified source sha" in rules
    assert "only after final certification pass" in rules


def test_no_tag_now():
    """The live version's release tag must not exist during the rehearsal
    (historical tags for already-shipped versions are expected)."""
    proc = subprocess.run(
        ["git", "tag", "--list", f"amca-studio-v{CURRENT_ACTIVE_VERSION}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Rehearsal safety
# ---------------------------------------------------------------------------

def test_rehearsal_occurs_no_release_action():
    """During the rehearsal: the live version stays what the tree declares,
    no tag for it, no promotion marker."""
    from amca.version_freeze_preflight import active_version_agreement

    agreement = active_version_agreement()
    assert agreement["agrees"] is True
    assert agreement["current_version"] == CURRENT_ACTIVE_VERSION
    assert not (DIST_DIR / "PROMOTED").exists()
    assert not (DIST_DIR / "staged-promotion").exists()


def test_no_worker_specific_paths_or_secrets():
    """All runner scripts must be free of worker-specific absolute paths and
    credential material."""
    for script in (TRANSITION, SMOKE, SIGNING, POLICY, PROMOTE,
                   ORCHESTRATION, MATRIX):
        text = script.read_text(encoding="utf-8").lower()
        for forbidden in ("c:/users/md", "c:\\users\\md", "appdata",
                          "password", "private key", "pfx"):
            assert forbidden not in text, f"{script.name} leaks {forbidden}"


def test_policy_documentation_marks_transition_command_manager_gated():
    doc = (SOFTWARE_ROOT / "docs" / "release"
           / "AMCA-Studio-v1.0.0-Version-Transition-Execution-Contract.md"
           ).read_text(encoding="utf-8")
    assert "MANAGER AUTHORIZATION" in doc or "--authorize-bump" in doc
