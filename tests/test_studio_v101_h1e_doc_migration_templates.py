"""H1E I2S/MSI/migration/doc-template extension tests (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1E-I2S-MSI-MIGRATION-DOC-TEMPLATE-EXTENSIONS-01
Surfaces 7 (I2S v1.0.1 export), 10 (migration/upgrade evidence), and 12
(v1.0.1 doc templates). Proves: v1.0.1 I2S export requires all three
artifacts; MSI omission blocks v1.0.1 shipping output; historical v1.0.0
two-artifact export remains valid; MSI migration evidence cannot be PASS
without a real execution record; templates cannot omit MSI; v1.0.0
artifact hashes cannot appear as v1.0.1 bindings; C: paths rejected;
certified source SHA carried consistently.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
RELEASE_DOCS = SOFTWARE_ROOT / "docs" / "release"
TMP = SOFTWARE_ROOT / "dist" / "h1e-tmp"

MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SOURCE_SHA = "ab" * 20
V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")
V100_PORTABLE_SHA = (
    "41efd240b6e8cdca3d6e1f69bf2cb4aa8461eb2fa7f0caf543b564d315494454")

TEMPLATES = [
    RELEASE_DOCS / "AMCA-Studio-v1.0.1-Release-Notes-Template.md",
    RELEASE_DOCS / ("AMCA-Studio-v1.0.1-Release-Certification-Summary"
                    "-Template.md"),
    RELEASE_DOCS / "AMCA-Studio-v1.0.1-Known-Limitations-Template.md",
    RELEASE_DOCS / "AMCA-Studio-v1.0.1-Shipping-Index-Template.json",
]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ex = _load("h1e_export", "export_final_release_documentation.py")
mc = _load("h1e_migration", "generate_migration_certification_evidence.py")


@pytest.fixture
def artifacts():
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    exe = TMP / "amca-studio-v1.0.1-windows-x64-setup.exe"
    exe.write_bytes(b"MZ" + b"x" * 200)
    msi = TMP / "amca-studio-v1.0.1-windows-x64.msi"
    msi.write_bytes(MSI_MAGIC + b"y" * 200)
    zip_ = TMP / "amca-studio-v1.0.1-windows-x64-portable.zip"
    zip_.write_bytes(b"PK\x03\x04" + b"z" * 200)
    yield {"exe": exe, "msi": msi, "portable": zip_}
    shutil.rmtree(TMP, ignore_errors=True)


# --- surface 7: v1.0.1 I2S export requires all three artifacts -------------

def test_bindings_require_all_three_artifacts(artifacts):
    bindings, reasons = ex.build_artifact_bindings(
        artifacts["exe"], None, artifacts["portable"], "1.0.1")
    assert bindings == []
    assert any("msi" in r.casefold() for r in reasons)


def test_msi_omission_blocks_v101_shipping_export(artifacts, tmp_path):
    result = ex.export_bundle(
        tmp_path / "staging", SOURCE_SHA,
        tmp_path / "no-certification.json",
        portable=artifacts["portable"], installer=artifacts["exe"],
        release_version="1.0.1", msi=None)
    assert result["overall"] == "BLOCKED"
    assert any("MSI" in r for r in result["reasons"])


def test_three_artifact_bindings_pass_and_carry_hashes(artifacts):
    bindings, reasons = ex.build_artifact_bindings(
        artifacts["exe"], artifacts["msi"], artifacts["portable"], "1.0.1")
    assert reasons == []
    roles = {b["role"] for b in bindings}
    assert roles == {"exe", "msi", "portable"}
    for b in bindings:
        assert len(b["sha256"]) == 64
        assert b["size_bytes"] > 0
        assert b["path"].startswith("D:/")
        assert b["release_version"] == "1.0.1"


def test_v100_two_artifact_export_path_unchanged(tmp_path):
    # Historical v1.0.0 export never consults the MSI binding contract and
    # must not fail because of a missing MSI.
    result = ex.export_bundle(
        tmp_path / "staging", SOURCE_SHA,
        tmp_path / "no-certification.json",
        portable=tmp_path / "p.zip", installer=tmp_path / "i.exe")
    assert result["release_version"] == "1.0.0"
    assert result["overall"] == "BLOCKED"
    assert not any("MSI" in r for r in result["reasons"])
    assert any("certification" in r.casefold() for r in result["reasons"])


def test_v100_names_not_accepted_for_v101_bindings(artifacts, tmp_path):
    stale = TMP / "amca-studio-v1.0.0-windows-x64.msi"
    shutil.copyfile(artifacts["msi"], stale)
    _, reasons = ex.build_artifact_bindings(
        artifacts["exe"], stale, artifacts["portable"], "1.0.1")
    assert any("v1.0.0 names" in r for r in reasons)


# --- surface 10: MSI migration evidence cannot PASS without execution ------

def test_msi_evidence_pending_without_execution_record(artifacts):
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA)
    assert doc["paths"]["msi"]["state"] == mc.MSI_PENDING_STATE
    assert doc["overall"] == mc.MSI_PENDING_STATE
    assert doc["paths"]["exe"]["state"] == "PASS"
    assert doc["paths"]["portable"]["state"] == "PASS"


def test_msi_pass_with_real_execution_record(artifacts):
    digest = mc._digest_bytes(artifacts["msi"].read_bytes())
    record = {"msi_sha256": digest, "install_result": "PASS",
              "uninstall_result": "PASS", "upgrade_result": "PASS"}
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA,
        msi_execution=record)
    assert doc["paths"]["msi"]["state"] == "PASS"
    assert doc["overall"] == "PASS"
    verdict, _ = mc.validate_upgrade_path_evidence(doc)
    assert verdict == "PASS"


def test_fabricated_msi_pass_rejected(artifacts):
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA)
    doc["paths"]["msi"]["state"] = "PASS"  # tampered: no execution record
    verdict, reasons = mc.validate_upgrade_path_evidence(doc)
    assert verdict == "BLOCKED"
    assert any("fabricated" in r for r in reasons)


def test_msi_execution_bound_to_other_bytes_fails(artifacts):
    record = {"msi_sha256": "ee" * 32, "install_result": "PASS",
              "uninstall_result": "PASS", "upgrade_result": "PASS"}
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA,
        msi_execution=record)
    assert doc["paths"]["msi"]["state"] == "FAIL"
    assert doc["overall"] == "FAIL"


# --- surface 12: templates cannot omit MSI ----------------------------------

def test_templates_exist_and_enumerate_all_three_artifacts():
    for tpl in TEMPLATES:
        assert tpl.is_file(), f"template missing: {tpl.name}"
        text = tpl.read_text(encoding="utf-8")
        for token in ("EXE", "MSI", "Portable"):
            assert token in text, f"{tpl.name} omits {token}"
        assert "{{MSI_SHA256}}" in text
        assert "{{CERTIFIED_SOURCE_SHA}}" in text


def test_templates_carry_required_semantics():
    blob = "\n".join(t.read_text(encoding="utf-8") for t in TEMPLATES)
    assert "PASS_WITH_REVIEW" in blob
    assert "NOT CERTIFIED" in blob or "not certified" in blob.casefold()
    assert "MODEL_RESPONSE_RECEIVED=YES" in blob
    assert "f9111c3f" in blob  # v1.0.0 hashes explicitly rejected
    assert "msiexec" in blob
    assert "D:/ChatGPT/AMCA" in blob or "D:\\ChatGPT\\AMCA" in blob


def test_shipping_index_template_parses_with_three_roles():
    doc = json.loads(TEMPLATES[3].read_text(encoding="utf-8"))
    assert doc["schema"] == "amca-v101-shipping-index/1"
    roles = [a["role"] for a in doc["artifact_roles"]]
    assert roles == ["exe", "msi", "portable"]
    assert all(a["mandatory"] for a in doc["artifact_roles"])
    assert doc["linux_included"] is False


# --- cross-cutting: v1.0.0 hashes, C: paths, source-SHA consistency --------

def test_v100_hashes_rejected_as_v101_bindings(artifacts):
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA)
    doc["paths"]["exe"]["artifact"]["sha256"] = V100_INSTALLER_SHA
    verdict, reasons = mc.validate_upgrade_path_evidence(doc)
    assert verdict == "BLOCKED"
    assert any("v1.0.0" in r for r in reasons)


def test_c_drive_paths_rejected_for_v101_outputs(tmp_path):
    exe = tmp_path / "amca-studio-v1.0.1-setup.exe"
    exe.write_bytes(b"MZ" + b"x" * 10)
    _, reasons = ex.build_artifact_bindings(
        exe, None, None, "1.0.1")
    assert any("D:" in r for r in reasons)
    doc = mc.build_upgrade_path_evidence(exe=exe, source_sha=SOURCE_SHA)
    assert doc["paths"]["exe"]["state"] == "FAIL"
    assert any("D:" in r for r in doc["paths"]["exe"]["reasons"])


def test_certified_source_sha_carried_consistently(artifacts):
    doc = mc.build_upgrade_path_evidence(
        exe=artifacts["exe"], msi=artifacts["msi"],
        portable=artifacts["portable"], source_sha=SOURCE_SHA)
    assert doc["source_sha"] == SOURCE_SHA
    for role in ("exe", "msi", "portable"):
        assert doc["paths"][role]["artifact"]["source_sha"] == SOURCE_SHA
    bindings, _ = ex.build_artifact_bindings(
        artifacts["exe"], artifacts["msi"], artifacts["portable"], "1.0.1")
    assert all(b["release_version"] == "1.0.1" for b in bindings)
    verdict, _ = mc.validate_upgrade_path_evidence(doc)
    assert verdict == mc.MSI_PENDING_STATE
