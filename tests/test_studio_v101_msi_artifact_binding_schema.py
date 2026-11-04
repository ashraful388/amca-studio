"""H1C MSI artifact binding + validation schema tests (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1C-MSI-ARTIFACT-BINDING-AND-VALIDATION-SCHEMA-IMPLEMENTATION-01
Tooling/schema/tests only. Builds nothing, installs nothing, promotes
nothing. Covers the required regression matrix: three-artifact v1.0.1
evidence accepted; missing MSI blocked; wrong MSI hash blocked; MSI
source-SHA mismatch blocked; EXE/MSI/ZIP version mismatch blocked;
historical v1.0.0 two-artifact evidence remains parseable; EXE renamed
to .msi cannot substitute; promotion cannot reach READY without all
three valid artifacts; no C-drive path accepted as canonical v1.0.1
output.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"

MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXE_MAGIC = b"MZ"

V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")
V100_PORTABLE_SHA = (
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454")

SOURCE_SHA = "ab" * 20
EXE_SHA = "a1" * 32
MSI_SHA = "b2" * 32
ZIP_SHA = "c3" * 32


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ab = _load("v101_ab", "v101_artifact_binding.py")
pi = _load("v101_pi", "build_ps2_promotion_input.py")
fv = _load("v101_fv", "run_final_package_compliance_verification.py")


def _entry(sha: str, name: str, version: str = "1.0.1",
           size: int = 1000) -> dict:
    return {"sha256": sha, "path": f"D:/ChatGPT/AMCA/release/{name}",
            "size_bytes": size, "version": version}


def _binding(version: str = "1.0.1", roles=("exe", "msi", "portable"),
             **overrides) -> dict:
    shas = {"exe": EXE_SHA, "msi": MSI_SHA, "portable": ZIP_SHA}
    names = {"exe": "amca-setup.exe", "msi": "amca-setup.msi",
             "portable": "amca-portable.zip"}
    artifacts = {role: _entry(shas[role], names[role]) for role in roles}
    artifacts.update(overrides)
    return {"schema": ab.SCHEMA, "release_version": version,
            "source_sha": SOURCE_SHA, "artifacts": artifacts}


# --- required case 1: three-artifact v1.0.1 evidence accepted -------------

def test_three_artifact_v101_binding_accepted():
    verdict, _ = ab.validate_artifact_binding(_binding())
    assert verdict == "PASS"


# --- required case 2: missing MSI blocked ----------------------------------

def test_missing_msi_blocked():
    verdict, reasons = ab.validate_artifact_binding(
        _binding(roles=("exe", "portable")))
    assert verdict == "BLOCKED"
    assert any("msi" in r for r in reasons)


# --- required case 3: wrong MSI hash blocked -------------------------------

def test_wrong_msi_hash_blocked(tmp_path):
    real = tmp_path / "amca-setup.msi"
    real.write_bytes(MSI_MAGIC + b"payload")
    binding = _binding()
    entry = binding["artifacts"]["msi"]
    entry["path"] = str(real).replace("\\", "/")
    entry["sha256"] = "00" * 32  # does not match physical bytes
    verdict, reasons = ab.validate_artifact_binding(
        binding, verify_files=True)
    assert verdict == "BLOCKED"
    assert any("physical bytes differ" in r for r in reasons)


# --- required case 4: MSI source-SHA mismatch blocked ----------------------

def test_msi_source_sha_mismatch_blocked():
    binding = _binding()
    manifest = {"provenance": {"source_git_sha": "cd" * 20},
                "version": "1.0.1", "artifacts": []}
    verdict, reasons = ab.verify_lineage(binding, manifest)
    assert verdict == "BLOCKED"
    assert any("certified" in r for r in reasons)


# --- required case 5: EXE/MSI/ZIP version mismatch blocked -----------------

def test_artifact_version_mismatch_blocked():
    binding = _binding()
    binding["artifacts"]["msi"] = _entry(
        MSI_SHA, "amca-setup.msi", version="1.0.0")
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert any("version" in r for r in reasons)


# --- required case 6: historical v1.0.0 two-artifact evidence parseable ----

def test_v100_historical_two_artifact_remains_valid():
    binding = _binding(version="1.0.0", roles=("exe", "portable"))
    binding["artifacts"]["exe"]["version"] = "1.0.0"
    binding["artifacts"]["portable"]["version"] = "1.0.0"
    verdict, _ = ab.validate_artifact_binding(binding)
    assert verdict == "PASS"


# --- required case 7: EXE renamed to .msi cannot substitute ----------------

def test_exe_renamed_msi_rejected(tmp_path):
    fake = tmp_path / "amca-setup.msi"
    fake.write_bytes(EXE_MAGIC + b"this is an exe")
    digest = hashlib.sha256(fake.read_bytes()).hexdigest()
    binding = _binding()
    entry = binding["artifacts"]["msi"]
    entry["path"] = str(fake).replace("\\", "/")
    entry["sha256"] = digest
    verdict, reasons = ab.validate_artifact_binding(
        binding, verify_files=True)
    assert verdict == "BLOCKED"
    assert any("role substitution" in r for r in reasons)


def test_i2o_harness_rejects_exe_renamed_msi(tmp_path):
    fake = tmp_path / "amca-setup.msi"
    fake.write_bytes(EXE_MAGIC + b"this is an exe")
    result = fv.run_verification(
        None, None, None,
        evidence_path=tmp_path / "missing-evidence.json",
        sbom_path=None, notices_path=None,
        payload_path=tmp_path / "missing-payload.json", msi=fake)
    assert result["overall"] == "FAIL"
    assert any("role substitution" in r for r in result["fail_findings"])


# --- required case 8: promotion cannot reach READY without all three -------

def _promo_doc(**fields) -> dict:
    doc = {
        "schema": pi.SCHEMA,
        "release_version": "1.0.1",
        "promotion_eligibility_ps2": "PS2_INPUT_READY",
        "ps2_aggregate_verdict": "PS2_RELEASE_READY",
        "source_sha": SOURCE_SHA,
        "installer_sha256": EXE_SHA,
        "msi_sha256": MSI_SHA,
        "portable_sha256": ZIP_SHA,
        "mpl_readiness": "PASS",
        "mpl_provenance_state": "PROVENANCE_READY",
    }
    doc.update(fields)
    return doc


def test_promotion_ready_with_three_artifacts():
    verdict, _ = pi.validate_promotion_input(_promo_doc())
    assert verdict == "PS2_INPUT_READY"


def test_promotion_blocked_without_msi():
    verdict, reasons = pi.validate_promotion_input(
        _promo_doc(msi_sha256=None))
    assert verdict == "PS2_INPUT_BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_promotion_v100_two_artifact_still_valid():
    verdict, _ = pi.validate_promotion_input(
        _promo_doc(release_version="1.0.0", msi_sha256=None))
    assert verdict == "PS2_INPUT_READY"


def test_promotion_rejects_v100_hash_reuse():
    verdict, _ = pi.validate_promotion_input(
        _promo_doc(msi_sha256=V100_INSTALLER_SHA))
    assert verdict == "PS2_INPUT_FAILED"


# --- required case 9: no C-drive path accepted -----------------------------

def test_c_drive_path_rejected():
    binding = _binding()
    binding["artifacts"]["msi"]["path"] = "C:/Windows/Temp/amca-setup.msi"
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert any("D:" in r for r in reasons)


# --- additional lineage / contract coverage --------------------------------

def test_v100_hashes_rejected_as_v101_evidence():
    binding = _binding()
    binding["artifacts"]["exe"]["sha256"] = V100_INSTALLER_SHA
    binding["artifacts"]["portable"]["sha256"] = V100_PORTABLE_SHA
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert sum("historical" in r for r in reasons) == 2


def test_duplicate_artifact_hashes_rejected():
    binding = _binding()
    binding["artifacts"]["msi"]["sha256"] = EXE_SHA
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert any("mutually distinct" in r for r in reasons)


def test_lineage_pass_with_consistent_manifest_and_promotion():
    binding = _binding()
    manifest = {
        "provenance": {"source_git_sha": SOURCE_SHA}, "version": "1.0.1",
        "artifacts": [
            {"package_type": "installer", "package_name": "amca-setup.exe",
             "sha256": EXE_SHA},
            {"package_type": "installer", "package_name": "amca-setup.msi",
             "sha256": MSI_SHA},
            {"package_type": "portable", "package_name": "amca-portable.zip",
             "sha256": ZIP_SHA},
        ],
    }
    promo = {"source_sha": SOURCE_SHA, "exe_sha256": EXE_SHA,
             "msi_sha256": MSI_SHA, "portable_sha256": ZIP_SHA}
    verdict, _ = ab.verify_lineage(binding, manifest, promo)
    assert verdict == "PASS"


def test_msi_validation_contract_fields():
    contract = ab.msi_validation_contract()
    required = set(contract["required_fields"])
    for field in ("msi_sha256", "size_bytes", "product_version",
                  "product_name", "silent_install_result",
                  "installed_payload_verification", "packaged_backend_health",
                  "desktop_launch", "install_result", "uninstall_result",
                  "residual_installation_check"):
        assert field in required, f"MSI contract missing field: {field}"
    blob = str(contract["command_semantics"])
    assert "msiexec /i" in blob and "/qn" in blob and "msiexec /x" in blob


def test_shipping_index_binds_three_artifacts():
    binding = _binding()
    index = {
        "schema": "amca-v101-shipping-index/1", "release_version": "1.0.1",
        "source_sha": SOURCE_SHA,
        "artifacts": [
            {"role": role, **binding["artifacts"][role]}
            for role in ("exe", "msi", "portable")],
    }
    verdict, _ = ab.validate_shipping_index(index, binding)
    assert verdict == "PASS"


def test_shipping_index_missing_msi_blocked():
    binding = _binding()
    index = {
        "schema": "amca-v101-shipping-index/1", "release_version": "1.0.1",
        "source_sha": SOURCE_SHA,
        "artifacts": [
            {"role": role, **binding["artifacts"][role]}
            for role in ("exe", "portable")],
    }
    verdict, reasons = ab.validate_shipping_index(index, binding)
    assert verdict == "BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_shipping_index_hash_mismatch_blocked():
    binding = _binding()
    index = {
        "schema": "amca-v101-shipping-index/1", "release_version": "1.0.1",
        "source_sha": SOURCE_SHA,
        "artifacts": [
            {"role": role, **binding["artifacts"][role]}
            for role in ("exe", "msi", "portable")],
    }
    index["artifacts"][1]["sha256"] = "ee" * 32
    verdict, reasons = ab.validate_shipping_index(index, binding)
    assert verdict == "BLOCKED"
    assert any("hash" in r for r in reasons)
