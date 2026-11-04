"""H1D three-artifact pipeline gap-audit tests (PS-2).

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1D-THREE-ARTIFACT-RELEASE-MANIFEST-AND-PROMOTION-GAP-AUDIT-01
Read-only audit + tests/contract additions only. Proves v1.0.1 cannot:
omit MSI from the release manifest; omit MSI from SHA256SUMS; omit MSI
from the promotion inventory; omit MSI from the shipping index;
substitute EXE/ZIP for MSI; bind any artifact to a different source SHA.
Historical v1.0.0 records must remain unaffected.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
RELEASE_DOCS = SOFTWARE_ROOT / "docs" / "release"

AUDIT_DOC = RELEASE_DOCS / (
    "AMCA-Studio-v1.0.1-H1D-Three-Artifact-Pipeline-Gap-Audit.md")

SOURCE_SHA = "ab" * 20
EXE_SHA = "a1" * 32
MSI_SHA = "b2" * 32
ZIP_SHA = "c3" * 32

CLASSIFICATIONS = {
    "READY_FOR_THREE_ARTIFACTS", "NEEDS_ZCODE_IMPLEMENTATION",
    "NEEDS_PS2_SCHEMA_EXTENSION", "HISTORICAL_V100_ONLY", "NOT_APPLICABLE",
}

AUDIT_SURFACES = [
    "Release-manifest generation",
    "SHA256SUMS generation",
    "Distribution inventory",
    "Packaging evidence",
    "Final promotion gate",
    "Promotion copy roles",
    "Shipping-document index",
    "Release close-out record",
    "Runtime-smoke aggregation",
    "Upgrade/migration evidence",
    "Installer inventory checks",
    "Release notes / certification templates",
]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ab = _load("h1d_ab", "v101_artifact_binding.py")
pi = _load("h1d_pi", "build_ps2_promotion_input.py")


def _entry(sha: str, name: str) -> dict:
    return {"sha256": sha, "path": f"D:/ChatGPT/AMCA/release/{name}",
            "size_bytes": 1000, "version": "1.0.1"}


def _binding(version: str = "1.0.1", roles=("exe", "msi", "portable")) -> dict:
    shas = {"exe": EXE_SHA, "msi": MSI_SHA, "portable": ZIP_SHA}
    names = {"exe": "amca-setup.exe", "msi": "amca-setup.msi",
             "portable": "amca-portable.zip"}
    return {"schema": ab.SCHEMA, "release_version": version,
            "source_sha": SOURCE_SHA,
            "artifacts": {r: _entry(shas[r], names[r]) for r in roles}}


def _manifest(roles=("exe", "msi", "portable"), source_sha=SOURCE_SHA,
              version="1.0.1", hashes=None) -> dict:
    shas = {"exe": EXE_SHA, "msi": MSI_SHA, "portable": ZIP_SHA}
    if hashes:
        shas.update(hashes)
    names = {"exe": "amca-setup.exe", "msi": "amca-setup.msi",
             "portable": "amca-portable.zip"}
    return {"version": version,
            "provenance": {"source_git_sha": source_sha},
            "artifacts": [{"package_name": names[r], "sha256": shas[r]}
                          for r in roles]}


def _sums(roles=("exe", "msi", "portable"), hashes=None) -> str:
    shas = {"exe": EXE_SHA, "msi": MSI_SHA, "portable": ZIP_SHA}
    if hashes:
        shas.update(hashes)
    names = {"exe": "amca-setup.exe", "msi": "amca-setup.msi",
             "portable": "amca-portable.zip"}
    return "\n".join(f"{shas[r]}  {names[r]}" for r in roles) + "\n"


# --- cannot omit MSI from the release manifest ------------------------------

def test_release_manifest_with_three_artifacts_passes():
    verdict, _ = ab.validate_release_manifest(_manifest(), _binding())
    assert verdict == "PASS"


def test_release_manifest_omitting_msi_blocked():
    verdict, reasons = ab.validate_release_manifest(
        _manifest(roles=("exe", "portable")), _binding())
    assert verdict == "BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_release_manifest_wrong_msi_hash_blocked():
    verdict, reasons = ab.validate_release_manifest(
        _manifest(hashes={"msi": "ee" * 32}), _binding())
    assert verdict == "BLOCKED"
    assert any("hash" in r for r in reasons)


def test_release_manifest_foreign_source_sha_blocked():
    verdict, reasons = ab.validate_release_manifest(
        _manifest(source_sha="cd" * 20), _binding())
    assert verdict == "BLOCKED"
    assert any("source" in r for r in reasons)


def test_release_manifest_v100_two_artifact_still_valid():
    binding = _binding(version="1.0.0", roles=("exe", "portable"))
    verdict, _ = ab.validate_release_manifest(
        _manifest(roles=("exe", "portable"), version="1.0.0"), binding)
    assert verdict == "PASS"


# --- cannot omit MSI from SHA256SUMS ----------------------------------------

def test_sha256sums_with_three_artifacts_passes():
    verdict, _ = ab.validate_sha256sums(_sums(), _binding())
    assert verdict == "PASS"


def test_sha256sums_omitting_msi_blocked():
    verdict, reasons = ab.validate_sha256sums(
        _sums(roles=("exe", "portable")), _binding())
    assert verdict == "BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_sha256sums_msi_hash_mismatch_blocked():
    verdict, reasons = ab.validate_sha256sums(
        _sums(hashes={"msi": "ee" * 32}), _binding())
    assert verdict == "BLOCKED"
    assert any("hash" in r for r in reasons)


def test_sha256sums_exe_substituted_for_msi_blocked():
    verdict, reasons = ab.validate_sha256sums(
        _sums(hashes={"msi": EXE_SHA}), _binding())
    assert verdict == "BLOCKED"
    assert any("substitution" in r for r in reasons)


def test_sha256sums_zip_substituted_for_msi_blocked():
    verdict, reasons = ab.validate_sha256sums(
        _sums(hashes={"msi": ZIP_SHA}), _binding())
    assert verdict == "BLOCKED"
    assert any("hash" in r for r in reasons)


def test_sha256sums_v100_two_artifact_still_valid():
    binding = _binding(version="1.0.0", roles=("exe", "portable"))
    verdict, _ = ab.validate_sha256sums(
        _sums(roles=("exe", "portable")), binding)
    assert verdict == "PASS"


# --- cannot omit MSI from promotion inventory / shipping index --------------

def _promo_doc(**fields) -> dict:
    doc = {
        "schema": pi.SCHEMA, "release_version": "1.0.1",
        "promotion_eligibility_ps2": "PS2_INPUT_READY",
        "ps2_aggregate_verdict": "PS2_RELEASE_READY",
        "source_sha": SOURCE_SHA, "installer_sha256": EXE_SHA,
        "msi_sha256": MSI_SHA, "portable_sha256": ZIP_SHA,
        "mpl_readiness": "PASS",
        "mpl_provenance_state": "PROVENANCE_READY",
    }
    doc.update(fields)
    return doc


def test_promotion_inventory_cannot_omit_msi():
    verdict, _ = pi.validate_promotion_input(_promo_doc(msi_sha256=None))
    assert verdict == "PS2_INPUT_BLOCKED"


def test_shipping_index_cannot_omit_msi():
    binding = _binding()
    index = {"schema": "amca-v101-shipping-index/1",
             "release_version": "1.0.1", "source_sha": SOURCE_SHA,
             "artifacts": [{"role": r, **binding["artifacts"][r]}
                           for r in ("exe", "portable")]}
    verdict, _ = ab.validate_shipping_index(index, binding)
    assert verdict == "BLOCKED"


# --- cannot bind any artifact to a different source SHA ---------------------

def test_lineage_blocks_foreign_source_sha():
    binding = _binding()
    manifest = _manifest(source_sha="cd" * 20)
    verdict, reasons = ab.verify_lineage(binding, manifest)
    assert verdict == "BLOCKED"
    assert any("certified" in r for r in reasons)


# --- audit report sentinel ---------------------------------------------------

def test_audit_doc_covers_all_surfaces_with_valid_classifications():
    assert AUDIT_DOC.is_file(), "H1D audit report missing"
    text = AUDIT_DOC.read_text(encoding="utf-8")
    for surface in AUDIT_SURFACES:
        assert surface in text, f"surface not audited: {surface}"
    used = {c for c in CLASSIFICATIONS if c in text}
    assert used, "no classifications present"
    assert "READY_FOR_THREE_ARTIFACTS" in used or \
        "NEEDS_ZCODE_IMPLEMENTATION" in used
    assert "H1D_THREE_ARTIFACT_PIPELINE_AUDIT=PASS" in text
    assert "NEEDS_ZCODE_IMPLEMENTATION" in text
    assert "smallest post-H1 integration sequence" in text.casefold() or \
        "Smallest post-H1 integration sequence" in text


def test_audit_doc_assigns_owner_for_every_gap_row():
    text = AUDIT_DOC.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines()
            if line.startswith("| ") and "NEEDS_" in line]
    assert len(rows) >= 10, "expected >=10 classified gap rows"
    for row in rows:
        cells = [c.strip() for c in row.split("|")]
        assert any(c in ("ZCode", "PS-2", "—") for c in cells), (
            f"row without owner: {row[:80]}")
