"""H1I ZCode three-artifact implementation acceptance sentinel tests.

Milestone: OPENCODE-PS2-AMCA-V1.0.1-H1I-ZCODE-THREE-ARTIFACT-IMPLEMENTATION-ACCEPTANCE-SENTINEL-01
H2B.3 semantic-drift resolution: ZCODE-AMCA-V1.0.1-H2B.3-FINAL-PREBUILD-BLOCKER-CLOSURE-01.
The sentinel must report each surface truthfully (BLOCKED while a contract
is unsatisfied, PASS once satisfied), block any genuinely failing surface,
and stay unaffected for historical v1.0.0; C: outputs are rejected.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SOFTWARE_ROOT / "scripts"
REPO_ROOT = SOFTWARE_ROOT.parent
TMP = SOFTWARE_ROOT / "dist" / "h1i-tmp"

MSI_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SOURCE_SHA = "ab" * 20
V100_INSTALLER_SHA = (
    "f9111c3f70ae9119a98c9ccd29454081ada35596022992aa152fef401636eee1")


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sn = _load("h1i_sn", "v101_zcode_three_artifact_acceptance_sentinel.py")
ab = _load("h1i_ab", "v101_artifact_binding.py")
pi = _load("h1i_pi", "build_ps2_promotion_input.py")


# --- sentinel behavior -------------------------------------------------------

def test_sentinel_reports_truthful_surfaces_on_current_tree():
    """H2B.3 semantic-drift resolution (ZCODE-AMCA-V1.0.1-H2B.3-...-01).

    The H1I-era expectation (all 8 surfaces BLOCKED) was truthful only while
    the S1-S8 implementation was absent. H2A implemented the surfaces, so on
    the current tree each independently satisfied surface truthfully reports
    PASS and the overall implementation-contract acceptance is PASS. The
    blocking requirements are NOT weakened: a genuinely failing surface
    still reports BLOCKED (proven by the simulated-tree tests below), and
    the real artifact/evidence acceptance remains gated by H1H and the
    final_promotion gates, which still BLOCK without built artifacts.
    """
    report = sn.run_sentinel(REPO_ROOT)
    surfaces = {m["surface"] for m in report["matrix"]}
    assert len(surfaces) == 8
    assert all(m["verdict"] == "PASS" for m in report["matrix"])
    assert all(m["reason"] == "observable contract satisfied"
               for m in report["matrix"])
    assert report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "PASS"
    assert report["blockers"] == []


def test_sentinel_still_blocks_when_a_contract_file_is_missing(tmp_path):
    """A genuinely failing surface (missing file) still reports BLOCKED."""
    _simulated_repo(tmp_path)
    (tmp_path / "software/src/amca/release_finalization.py").unlink()
    report = sn.run_sentinel(tmp_path)
    assert report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "BLOCKED"
    assert any("S6" in b for b in report["blockers"])


def _simulated_repo(root: Path, drop: str | None = None):
    """Build a minimal repo tree whose ZCode sources satisfy the
    observable contract (optionally dropping one surface's markers)."""
    files = {
        "software/src/amca/distribution_build.py":
            'package_type = "msi"  # msi descriptors\n'
            "sums_path = dist_dir / 'SHA256SUMS.txt'\n"
            "MSI_MAGIC = b'\\xd0\\xcf\\x11\\xe0'\n",
        "software/src/amca/release_packaging.py":
            '("amca-studio-v1.0.1-windows-x64.msi", "win32-x64", "msi"),\n',
        "software/scripts/promote_final_release.py":
            '"windows-msi": dist / "amca-studio-v1.0.1-windows-x64.msi",\n',
        "software/src/amca/final_promotion.py":
            "msi_smoke = GateResult('msi_smoke', state)\n"
            "gates = [bundle.certification, bundle.msi_smoke, "
            "bundle.release_gate]\n"
            "MANDATORY_INVENTORY = (\n    'windows-installer',\n"
            "    'windows-msi',\n    'windows-portable',\n)\n",
        "software/src/amca/release_finalization.py":
            "artifacts_count: int = 3\n",
        "software/src/amca/distribution_smoke.py":
            "msiexec /i <MSI> /qn\n# uninstall\nmsiexec /x <MSI> /qn\n"
            "residual_installation_check\n",
    }
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if drop and rel.endswith(drop):
            target.write_text("# pre-implementation placeholder\n",
                              encoding="utf-8")
        else:
            target.write_text(text, encoding="utf-8")


def test_sentinel_passes_on_simulated_implementation(tmp_path):
    _simulated_repo(tmp_path)
    report = sn.run_sentinel(tmp_path)
    assert report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "PASS"
    assert report["blockers"] == []


@pytest.mark.parametrize("drop", [
    "distribution_build.py", "release_packaging.py",
    "promote_final_release.py", "final_promotion.py",
    "release_finalization.py", "distribution_smoke.py"])
def test_each_missing_surface_blocks(tmp_path, drop):
    _simulated_repo(tmp_path, drop=drop)
    report = sn.run_sentinel(tmp_path)
    assert report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "BLOCKED"
    assert report["blockers"], "missing surface must produce exact blockers"


def test_missing_mandatory_inventory_entry_blocks(tmp_path):
    _simulated_repo(tmp_path)
    f = tmp_path / "software/src/amca/final_promotion.py"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "'windows-msi',\n", ""), encoding="utf-8")
    report = sn.run_sentinel(tmp_path)
    assert report["H1I_ZCODE_THREE_ARTIFACT_ACCEPTANCE"] == "BLOCKED"
    assert any("S5" in b for b in report["blockers"])


def test_closeout_declaring_two_artifacts_blocks(tmp_path):
    _simulated_repo(tmp_path)
    f = tmp_path / "software/src/amca/release_finalization.py"
    f.write_text("artifacts_count: int = 2\n", encoding="utf-8")
    report = sn.run_sentinel(tmp_path)
    assert any("S6" in b for b in report["blockers"])


# --- observable-contract behavioral proofs (validators are the contract) ----

def _entry(sha, name):
    return {"sha256": sha, "path": f"D:/ChatGPT/AMCA/release/{name}",
            "size_bytes": 100, "version": "1.0.1"}


def _binding(roles=("exe", "msi", "portable"), version="1.0.1"):
    shas = {"exe": "a1" * 32, "msi": "b2" * 32, "portable": "c3" * 32}
    names = {"exe": "a.exe", "msi": "a.msi", "portable": "a.zip"}
    return {"schema": ab.SCHEMA, "release_version": version,
            "source_sha": SOURCE_SHA,
            "artifacts": {r: _entry(shas[r], names[r]) for r in roles}}


def test_valid_three_artifact_contract_passes():
    assert ab.validate_artifact_binding(_binding())[0] == "PASS"


def test_missing_msi_blocks_contract():
    assert ab.validate_artifact_binding(
        _binding(roles=("exe", "portable")))[0] == "BLOCKED"


def test_fake_msi_blocks_contract(tmp_path):
    fake = tmp_path / "a.msi"
    fake.write_bytes(b"MZ" + b"exe-bytes")
    binding = _binding()
    e = binding["artifacts"]["msi"]
    e["path"] = str(fake).replace("\\", "/")
    e["sha256"] = hashlib.sha256(fake.read_bytes()).hexdigest()
    verdict, reasons = ab.validate_artifact_binding(binding,
                                                    verify_files=True)
    assert verdict == "BLOCKED"
    assert any("substitution" in r for r in reasons)


def test_manifest_omitting_msi_blocks():
    binding = _binding()
    manifest = {"version": "1.0.1",
                "provenance": {"source_git_sha": SOURCE_SHA},
                "artifacts": [
                    {"package_name": "a.exe", "sha256": "a1" * 32},
                    {"package_name": "a.zip", "sha256": "c3" * 32}]}
    verdict, reasons = ab.validate_release_manifest(manifest, binding)
    assert verdict == "BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_sha256sums_omitting_msi_blocks():
    binding = _binding()
    sums = f"{'a1' * 32}  a.exe\n{'c3' * 32}  a.zip\n"
    verdict, reasons = ab.validate_sha256sums(sums, binding)
    assert verdict == "BLOCKED"
    assert any("MSI" in r for r in reasons)


def test_promotion_inventory_omitting_msi_blocks():
    doc = {"schema": pi.SCHEMA, "release_version": "1.0.1",
           "promotion_eligibility_ps2": "PS2_INPUT_READY",
           "ps2_aggregate_verdict": "PS2_RELEASE_READY",
           "source_sha": SOURCE_SHA, "installer_sha256": "a1" * 32,
           "msi_sha256": None, "portable_sha256": "c3" * 32,
           "mpl_readiness": "PASS",
           "mpl_provenance_state": "PROVENANCE_READY"}
    assert pi.validate_promotion_input(doc)[0] == "PS2_INPUT_BLOCKED"


def test_mismatched_artifact_source_sha_blocks():
    binding = _binding()
    manifest = {"provenance": {"source_git_sha": "cd" * 20},
                "version": "1.0.1", "artifacts": []}
    assert ab.verify_lineage(binding, manifest)[0] == "BLOCKED"


def test_v100_historical_path_unaffected():
    binding = _binding(version="1.0.0", roles=("exe", "portable"))
    binding["artifacts"]["exe"]["version"] = "1.0.0"
    binding["artifacts"]["portable"]["version"] = "1.0.0"
    assert ab.validate_artifact_binding(binding)[0] == "PASS"


def test_c_drive_output_rejected():
    binding = _binding()
    binding["artifacts"]["msi"]["path"] = "C:/Temp/a.msi"
    verdict, reasons = ab.validate_artifact_binding(binding)
    assert verdict == "BLOCKED"
    assert any("D:" in r for r in reasons)
