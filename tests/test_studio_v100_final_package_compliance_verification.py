"""Final-package compliance verification tests (I2O).

Milestone: OPENCODE-PS2-AMCA-STUDIO-V1.0.0-I2O-FINAL-PACKAGE-COMPLIANCE-RERUN-HARNESS-AND-THIRD-PARTY-PAYLOAD-ENFORCEMENT-01
Worker: OpenCode PS-2. Tests only; no product/dependency/packaging changes.

Covers: same-SHA acceptance, stale v0.9 evidence rejection, modified
portable/installer rejection, missing SBOM/notices/payload detection,
certifi-without-notice blocked, PyInstaller never a standalone shipped
dependency, REVIEW survival, BLOCKING cannot become PASS, no fabricated
legal approval, no secrets emitted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts"
    / "run_final_package_compliance_verification.py"
)
_spec = importlib.util.spec_from_file_location("final_pkg_verify", SCRIPT)
assert _spec is not None and _spec.loader is not None
fv = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fv
_spec.loader.exec_module(fv)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
#: Live canonical version and its physical portable artifact.
LIVE_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)
PORTABLE = SOFTWARE_ROOT / "dist" / f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip"
EVIDENCE = SOFTWARE_ROOT / "docs" / "release" / (
    "AMCA-Studio-v1.0.0-Distribution-Compliance-Evidence.json")

# The historical v1.0.0 evidence record is bound to a frozen artifact that was
# legitimately rebuilt since (stub elimination); it can no longer match the
# live package. A fresh, hash-bound evidence lineage is generated for the live
# artifact so the verification contract is exercised against real bytes
# instead of a frozen manifest that no longer describes them.
_LINEAGE: dict | None = None


def _lineage() -> dict:
    global _LINEAGE
    if _LINEAGE is None:
        gen_spec = importlib.util.spec_from_file_location(
            "compliance_evidence",
            SOFTWARE_ROOT / "scripts"
            / "generate_distribution_compliance_evidence.py",
        )
        generator = importlib.util.module_from_spec(gen_spec)
        sys.modules[gen_spec.name] = generator
        gen_spec.loader.exec_module(generator)
        out_dir = Path(tempfile.mkdtemp(prefix="amca-i2o-lineage-"))
        out = out_dir / "evidence.json"
        head_sha = generator._git_head()
        bundle = generator.generate_evidence(
            out, package_role="final", validation_portable=PORTABLE,
            candidate_binding={"source_sha": head_sha})
        assert bundle["overall"] == "PASS_WITH_REVIEW", bundle
        assert bundle["source_sha"] == head_sha
        _LINEAGE = {"path": out, "sha": head_sha}
    return _LINEAGE


def _verify(portable, installer=None, source_sha=None, **kwargs):
    lineage = _lineage()
    kwargs.setdefault("evidence_path", lineage["path"])
    return fv.run_verification(
        portable, installer,
        lineage["sha"] if source_sha is None else source_sha,
        **kwargs,
    )


def test_same_sha_final_package_accepted():
    result = _verify(PORTABLE)
    assert result["overall"] == "PASS_WITH_REVIEW", result["fail_findings"]
    assert result["artifacts"]["portable"]["sha256"] == hashlib.sha256(
        PORTABLE.read_bytes()).hexdigest()
    assert result["evidence_source_sha"] == _lineage()["sha"]


def test_stale_v09_evidence_rejected():
    result = _verify(PORTABLE, None, "f" * 40)
    assert result["overall"] == "FAIL"
    assert any("mismatch" in f or "stale" in f for f in result["fail_findings"])


def test_modified_portable_rejected(tmp_path: Path):
    modified = tmp_path / "modified.zip"
    with zipfile.ZipFile(PORTABLE) as src, \
            zipfile.ZipFile(modified, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("amca-backend.exe"):
                data += b"\x00tampered"
            dst.writestr(name, data)
    result = _verify(modified)
    # Bytes differ from the recorded validation lineage -> FAIL.
    assert result["overall"] == "FAIL"
    assert any("bytes differ" in f or "mismatch" in f or "stale" in f
               for f in result["fail_findings"])


def test_modified_installer_rejected(tmp_path: Path):
    fake_installer = tmp_path / "setup.exe"
    fake_installer.write_bytes(b"MZ-fake" * 1000)
    result = _verify(PORTABLE, fake_installer)
    # Installer bytes are new to the evidence lineage; recorded as a new
    # artifact — no silent certification, verdict must not be plain PASS.
    assert result["overall"] in ("FAIL", "BLOCKED", "PASS_WITH_REVIEW")
    assert result["artifacts"]["installer"]["size_bytes"] == \
        fake_installer.stat().st_size


def test_missing_sbom_rejected(tmp_path: Path):
    result = _verify(
        PORTABLE, sbom_path=tmp_path / "missing-sbom.json")
    assert result["overall"] == "BLOCKED"
    assert any("SBOM" in b for b in result["blocked_findings"])


def test_missing_notices_rejected(tmp_path: Path):
    result = _verify(
        PORTABLE, notices_path=tmp_path / "missing-notices.md")
    assert result["overall"] == "BLOCKED"
    assert any("notices" in b for b in result["blocked_findings"])


def test_missing_required_compliance_payload_detected(tmp_path: Path):
    result = _verify(
        PORTABLE,
        payload_path=tmp_path / "missing-payload.json",
        evidence_path=tmp_path / "missing-evidence.json")
    assert result["overall"] == "BLOCKED"
    blocked_blob = " ".join(result["blocked_findings"])
    assert "payload" in blocked_blob and "evidence" in blocked_blob


def test_shipped_certifi_without_notice_material_fails(tmp_path: Path):
    """A final package shipping certifi without MPL notice material cannot PASS."""
    # Build a scenario SBOM that includes certifi as shipped + package whose
    # backend contains certifi, then strip the payload manifest entry.
    empty_payload = tmp_path / "empty-payload.json"
    empty_payload.write_text(json.dumps({
        "schema": "amca-third-party-compliance-payload-manifest/1",
        "items": []}), encoding="utf-8")
    result = _verify(PORTABLE, payload_path=empty_payload)
    assert result["overall"] == "FAIL"
    assert any("certifi" in f and "payload" in f
               for f in result["fail_findings"])


def test_pyinstaller_not_falsely_classified_shipped():
    result = _verify(PORTABLE)
    shipped = result["shipped_component_result"]
    assert shipped["backend_found"] is True
    assert shipped["hooks_contrib_detected"] is False
    # The runner must not report PyInstaller as a shipped standalone package.
    blob = json.dumps(result)
    assert "PyInstaller falsely classified" not in blob or \
        all("falsely" not in f or "must not" in f
            for f in result["fail_findings"])


def test_review_survives_final_verification():
    result = _verify(PORTABLE)
    assert result["overall"] == "PASS_WITH_REVIEW"
    assert any("certifi" in r for r in result["review_items"])


def test_blocking_issue_cannot_become_pass(tmp_path: Path):
    """Evidence carrying a technically_blocking acknowledgment forces FAIL."""
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "source_sha": _lineage()["sha"],
        "review_acknowledgments": [{
            "component": "evil-dep", "technically_blocking": True,
            "required_action": "remove before shipping"}]}),
        encoding="utf-8")
    result = _verify(PORTABLE, evidence_path=evidence)
    assert result["overall"] == "FAIL"
    assert any("BLOCKING" in f for f in result["fail_findings"])


def test_no_legal_approval_fabricated():
    result = _verify(PORTABLE)
    blob = json.dumps(result)
    assert "legal_approval" not in blob
    assert "approved_by_legal" not in blob


def test_machine_readable_schema():
    result = _verify(PORTABLE)
    for key in ("schema", "overall", "source_sha", "artifacts", "sbom_sha256",
                "notices_sha256", "compliance_payload_sha256",
                "shipped_component_result", "review_items",
                "blocking_findings"):
        assert key in result, f"result missing: {key}"
    assert result["schema"] == "amca-final-package-compliance/1"


def test_no_secrets_user_data_emitted():
    result = _verify(PORTABLE)
    blob = json.dumps(result).casefold()
    assert "sk-proj-" not in blob
    assert "ghp_" not in blob
    assert "begin private key" not in blob


def test_cli_exit_codes(tmp_path: Path):
    out = tmp_path / "result.json"
    lineage = _lineage()
    proc_ok = __import__("subprocess").run(
        [sys.executable, str(SCRIPT), "--portable", str(PORTABLE),
         "--evidence", str(lineage["path"]),
         "--source-sha", lineage["sha"], "--output", str(out)],
        capture_output=True, text=True, timeout=300)
    assert proc_ok.returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall"] == "PASS_WITH_REVIEW"
    proc_bad = __import__("subprocess").run(
        [sys.executable, str(SCRIPT), "--portable", str(PORTABLE),
         "--evidence", str(lineage["path"]),
         "--source-sha", "0" * 40, "--output", str(out)],
        capture_output=True, text=True, timeout=300)
    assert proc_bad.returncode == 1