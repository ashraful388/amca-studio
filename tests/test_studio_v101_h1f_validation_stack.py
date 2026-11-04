"""H1F PS-2 validation-stack consolidation + integration-handoff tests.

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1F-VALIDATION-STACK-CONSOLIDATION-AND-INTEGRATION-HANDOFF-01
Consolidation/handoff only. Verifies: accepted commit lineage
(H1B->H1C->H1D->H1E), the handoff manifest, schema-registry
consistency, honest prebuild states (nothing promotion-ready before
real artifacts), legacy C: path absence, and that v1.0.0 artifact
hashes can never satisfy a v1.0.1 gate.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
RELEASE_DOCS = SOFTWARE_ROOT / "docs" / "release"
REPO_ROOT = SOFTWARE_ROOT.parent

HANDOFF = RELEASE_DOCS / (
    "AMCA-Studio-v1.0.1-H1F-PS2-Validation-Stack-Integration-Handoff.md")

LINEAGE = [("H1B", "e99c8826"), ("H1C", "6e6b2e08"), ("H1D", "be1bf538"),
           ("H1E", "05d884e8")]

V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")
V100_PORTABLE_SHA = (
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454")

PS2_V101_FILES = [
    SCRIPTS / "v101_artifact_binding.py",
    SCRIPTS / "build_ps2_promotion_input.py",
    SCRIPTS / "export_final_release_documentation.py",
    SCRIPTS / "generate_migration_certification_evidence.py",
    SCRIPTS / "run_final_package_compliance_verification.py",
    SCRIPTS / "run_ps2_final_release_validation.py",
    RELEASE_DOCS / "AMCA-Studio-v1.0.1-Package-Validation-Plan.md",
    RELEASE_DOCS / ("AMCA-Studio-v1.0.1-Package-Validation-Command-"
                    "Manifest.json"),
    HANDOFF,
]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ab = _load("h1f_ab", "v101_artifact_binding.py")
pi = _load("h1f_pi", "build_ps2_promotion_input.py")
mc = _load("h1f_mc", "generate_migration_certification_evidence.py")
ex = _load("h1f_ex", "export_final_release_documentation.py")


# --- accepted lineage --------------------------------------------------------

def test_handoff_manifest_present_and_complete():
    assert HANDOFF.is_file(), "H1F handoff manifest missing"
    text = HANDOFF.read_text(encoding="utf-8")
    assert "H1F_PS2_VALIDATION_STACK_READY=PASS" in text
    for _, sha in LINEAGE:
        assert sha in text, f"lineage commit {sha} not in handoff"
    for schema in ("amca-v101-artifact-binding/1",
                   "amca-v101-shipping-index/1",
                   "amca-v101-upgrade-path-evidence/1",
                   "amca-ps2-promotion-input/1"):
        assert schema in text
    assert "MSI_EXECUTION_PENDING" in text
    assert "MODEL_RESPONSE_RECEIVED=YES" in text


def test_commit_lineage_ancestry():
    try:
        for (label_a, sha_a), (label_b, sha_b) in zip(LINEAGE, LINEAGE[1:]):
            subprocess.run(
                ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor",
                 sha_a, sha_b],
                check=True, capture_output=True)
    except FileNotFoundError:
        pytest.skip("git unavailable")


# --- honest prebuild states ---------------------------------------------------

def test_prebuild_binding_blocked_without_artifacts():
    verdict, _ = ab.validate_artifact_binding(
        {"release_version": "1.0.1", "source_sha": "ab" * 20,
         "artifacts": {}})
    assert verdict == "BLOCKED"


def test_prebuild_upgrade_paths_pending():
    doc = mc.build_upgrade_path_evidence()
    assert doc["overall"] == mc.MSI_PENDING_STATE
    states = {r: p["state"] for r, p in doc["paths"].items()}
    assert states == {"exe": "PENDING", "msi": "PENDING",
                      "portable": "PENDING"}
    assert doc["overall"] != "PASS"


def test_prebuild_promotion_blocked_without_msi():
    doc = {"schema": pi.SCHEMA, "release_version": "1.0.1",
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": "ab" * 20, "installer_sha256": "a1" * 32,
           "msi_sha256": None, "portable_sha256": "c3" * 32,
           "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY"}
    verdict, _ = pi.validate_promotion_input(doc)
    assert verdict == "PS2_INPUT_BLOCKED"


def test_prebuild_i2s_export_blocked(tmp_path):
    result = ex.export_bundle(tmp_path / "staging", "ab" * 20,
                              tmp_path / "no-cert.json",
                              release_version="1.0.1")
    assert result["overall"] == "BLOCKED"
    assert any("MSI" in r for r in result["reasons"])


# --- schema registry consistency ----------------------------------------------

def test_schema_registry_consistent_across_stack():
    assert ab.SCHEMA == "amca-v101-artifact-binding/1"
    assert pi.SCHEMA == "amca-ps2-promotion-input/1"
    assert mc.UPGRADE_PATH_SCHEMA == "amca-v101-upgrade-path-evidence/1"
    assert ex.SCHEMA == "amca-final-release-documentation-export/1"
    fv = _load("h1f_fv", "run_final_package_compliance_verification.py")
    assert fv.SCHEMA == "amca-final-package-compliance/1"
    ag = _load("h1f_ag", "run_ps2_final_release_validation.py")
    assert ag.SCHEMA == "amca-ps2-final-release-evidence/1"


# --- legacy C: path + v1.0.0 hash gates ---------------------------------------

def test_no_legacy_c_drive_references_in_ps2_v101_files():
    for path in PS2_V101_FILES:
        assert path.is_file(), f"expected PS-2 v1.0.1 file missing: {path}"
        text = path.read_text(encoding="utf-8")
        assert "Documents\\ChatGPT\\AMCA" not in text
        assert "Documents/ChatGPT/AMCA" not in text


def test_v100_hashes_cannot_satisfy_v101_gates():
    binding = {"schema": ab.SCHEMA, "release_version": "1.0.1",
               "source_sha": "ab" * 20,
               "artifacts": {
                   "exe": {"sha256": V100_INSTALLER_SHA,
                           "path": "D:/ChatGPT/AMCA/r/a.exe",
                           "size_bytes": 10, "version": "1.0.1"},
                   "msi": {"sha256": "b2" * 32,
                           "path": "D:/ChatGPT/AMCA/r/a.msi",
                           "size_bytes": 10, "version": "1.0.1"},
                   "portable": {"sha256": V100_PORTABLE_SHA,
                                "path": "D:/ChatGPT/AMCA/r/a.zip",
                                "size_bytes": 10, "version": "1.0.1"}}}
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert sum("historical" in r for r in reasons) == 2
    promo = {"schema": pi.SCHEMA, "release_version": "1.0.1",
             "promotion_eligibility_ps2": "PS2_INPUT_FAILED",
             "ps2_aggregate_verdict": "PS2_RELEASE_FAILED",
             "source_sha": "ab" * 20,
             "installer_sha256": V100_INSTALLER_SHA,
             "msi_sha256": "b2" * 32,
             "portable_sha256": V100_PORTABLE_SHA,
             "mpl_readiness": "PASS",
             "mpl_provenance_state": "PROVENANCE_READY"}
    verdict, _ = pi.validate_promotion_input(promo)
    assert verdict == "PS2_INPUT_FAILED"
