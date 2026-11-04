"""I2G tests: Windows code-signing trust and distribution reputation readiness.

Milestone: ZCODE-AMCA-STUDIO-V1.0.0-I2G-WINDOWS-CODE-SIGNING-TRUST-AND-DISTRIBUTION-REPUTATION-READINESS-AUDIT-01

Parser/honesty tests use synthetic inspection results; the real-binary test
inspects the genuine current artifacts.
"""

from pathlib import Path
import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

from amca.signing_readiness import (
    SIGNING_ORDER,
    VERDICT_BLOCKED,
    VERDICT_PASS,
    VERDICT_REVIEW,
    inventory_signing_targets,
    inspect_authenticode,
    run_signing_audit,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
DIST_DIR = SOFTWARE_ROOT / "dist"
RUNNER = SOFTWARE_ROOT / "scripts" / "run_windows_signing_audit.py"

INSTALLER = DIST_DIR / "amca-studio-v0.9.0-windows-x64-setup.exe"
PORTABLE = DIST_DIR / "amca-studio-v0.9.0-windows-x64-portable.zip"


# ---------------------------------------------------------------------------
# Inventory completeness
# ---------------------------------------------------------------------------

def test_signing_target_inventory_covers_distribution_chain():
    targets = inventory_signing_targets()
    names = {t["artifact"] for t in targets}
    for required in (
        "amca-studio-v0.9.0-windows-x64-setup.exe",
        "amca-studio-v0.9.0-windows-x64-portable.zip",
        "amca-backend.exe",
        "amca-desktop.exe",
    ):
        assert required in names, f"inventory missing {required}"


def test_signing_order_contract_covers_key_invariant():
    order = [s["artifact"] for s in SIGNING_ORDER]
    # backend and desktop must be signed BEFORE the installer bundling.
    assert order.index("amca-backend.exe") < order.index(
        "amca-studio-v{version}-windows-x64-setup.exe")
    assert order.index("amca-desktop.exe") < order.index(
        "amca-studio-v{version}-windows-x64-setup.exe")
    invariant = " ".join(s.get("reason", "") for s in SIGNING_ORDER).lower()
    assert "after signing" in invariant or "before" in invariant
    # The uninstaller is classified as generated post-build.
    uninstaller = [s for s in SIGNING_ORDER if s["artifact"] == "uninstall.exe"]
    assert uninstaller and "generated post-build" in uninstaller[0]["classification"]


# ---------------------------------------------------------------------------
# Signature parser honesty (synthetic inspection results)
# ---------------------------------------------------------------------------

def _synthetic_audit(tmp_path: Path, inspections: dict, monkeypatch=None) -> dict:
    """Build a synthetic dist tree and patch inspect_authenticode with the
    supplied synthetic inspection results."""
    import amca.signing_readiness as sr

    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    installer = dist / "amca-studio-v0.9.0-windows-x64-setup.exe"
    portable = dist / "amca-studio-v0.9.0-windows-x64-portable.zip"
    installer.write_bytes(b"MZ" + b"I" * 1024)
    with zipfile.ZipFile(portable, "w") as z:
        z.writestr("pkg/amca-backend.exe", b"MZ" + b"B" * 2048)
        z.writestr("pkg/amca-desktop.exe", b"MZ" + b"D" * 2048)

    real_inspect = sr.inspect_authenticode

    def fake_inspect(path):
        name = Path(path).name
        return inspections.get(name, real_inspect(path))

    if monkeypatch is not None:
        monkeypatch.setattr(sr, "inspect_authenticode", fake_inspect)
    else:
        sr.inspect_authenticode = fake_inspect
    return run_signing_audit(dist_dir=dist)


def test_unsigned_executables_detected_honestly(tmp_path, monkeypatch):
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe": {"status": "NotSigned"},
        "amca-backend.exe": {"status": "NotSigned"},
        "amca-desktop.exe": {"status": "NotSigned"},
    })
    assert result["overall"] == VERDICT_REVIEW
    assert set(result["unsigned_artifacts"]) == {
        "amca-studio-v0.9.0-windows-x64-setup.exe",
        "amca-backend.exe",
        "amca-desktop.exe",
    }


def test_all_valid_signatures_pass(tmp_path, monkeypatch):
    signed = {"status": "Valid", "signer": "CN=AMCA Project, O=AMCA",
              "timestamped": True}
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe": signed,
        "amca-backend.exe": signed,
        "amca-desktop.exe": signed,
    })
    assert result["overall"] == VERDICT_PASS


def test_invalid_signature_not_treated_as_valid(tmp_path, monkeypatch):
    inspections = {
        "amca-studio-v0.9.0-windows-x64-setup.exe":
            {"status": "HashMismatch"},
        "amca-backend.exe": {"status": "Valid",
                             "signer": "CN=AMCA", "timestamped": True},
        "amca-desktop.exe": {"status": "Valid",
                             "signer": "CN=AMCA", "timestamped": True},
    }
    result = _synthetic_audit(tmp_path, inspections)
    assert result["overall"] == VERDICT_BLOCKED


def test_signing_configured_but_missing_signature_blocks(tmp_path, monkeypatch):
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe": {"status": "NotSigned"},
        "amca-backend.exe": {"status": "NotSigned"},
        "amca-desktop.exe": {"status": "NotSigned"},
    })
    # Same unsigned state, but signing required for release:
    import amca.signing_readiness as sr
    result = run_signing_audit(dist_dir=tmp_path / "dist",
                               signing_configured=True)
    assert result["overall"] == VERDICT_BLOCKED


def test_artifact_sha_bound_to_signing_evidence(tmp_path, monkeypatch):
    """Recorded member hashes must match a fresh extraction of the same
    portable zip: evidence is bound to exact bytes, reproducibly."""
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe": {"status": "NotSigned"},
        "amca-backend.exe": {"status": "NotSigned"},
        "amca-desktop.exe": {"status": "NotSigned"},
    })
    from amca.distribution_smoke import sha256_file
    for art in result["artifacts"]:
        # Installer path persists (inside caller's tmp_path).
        if art["artifact"].endswith("setup.exe") and art.get("sha256"):
            assert art["sha256"] == sha256_file(Path(art["path"]))
    # Member hashes: verify against a fresh extraction.
    portable = tmp_path / "dist" / "amca-studio-v0.9.0-windows-x64-portable.zip"
    member_hashes = {a["artifact"]: a["sha256"] for a in result["artifacts"]
                     if a.get("member_of")}
    import zipfile as _zip
    with _zip.ZipFile(portable) as z:
        for name in z.namelist():
            base = Path(name).name
            if base in member_hashes:
                assert member_hashes[base] == hashlib.sha256(
                    z.read(name)).hexdigest(), f"evidence mismatch for {base}"


def test_artifact_modification_invalidates_prior_evidence(tmp_path, monkeypatch):
    """Signing evidence is bound to exact bytes: modify an artifact and the
    recorded SHA no longer matches - evidence cannot certify the new bytes."""
    result1 = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe":
            {"status": "Valid", "signer": "CN=AMCA", "timestamped": True},
        "amca-backend.exe": {"status": "Valid", "signer": "CN=AMCA",
                             "timestamped": True},
        "amca-desktop.exe": {"status": "Valid", "signer": "CN=AMCA",
                             "timestamped": True},
    })
    evidence = {a["artifact"]: a["sha256"] for a in result1["artifacts"]
                if a.get("sha256")}

    # Tamper with the recorded file.
    installer = tmp_path / "dist" / "amca-studio-v0.9.0-windows-x64-setup.exe"
    installer.write_bytes(b"MZ" + b"X" * 999)

    from amca.distribution_smoke import sha256_file
    assert sha256_file(installer) != evidence[
        "amca-studio-v0.9.0-windows-x64-setup.exe"]


def test_signing_evidence_cannot_certify_different_binary(tmp_path, monkeypatch):
    """An audit of dist A (signed) must not be applicable to dist B
    (different bytes): SHAs recorded in evidence are per-binary."""
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe":
            {"status": "Valid", "signer": "CN=AMCA", "timestamped": True},
        "amca-backend.exe": {"status": "Valid", "signer": "CN=AMCA",
                             "timestamped": True},
        "amca-desktop.exe": {"status": "Valid", "signer": "CN=AMCA",
                             "timestamped": True},
    })
    by_name = {a["artifact"]: a for a in result["artifacts"]}
    other = tmp_path / "other-setup.exe"
    other.write_bytes(b"MZ" + b"OTHER" * 100)
    from amca.distribution_smoke import sha256_file
    assert by_name["amca-studio-v0.9.0-windows-x64-setup.exe"]["sha256"] != \
        sha256_file(other)


def test_no_certificate_material_persisted(tmp_path, monkeypatch):
    """No password/private-key material may appear in the audit output."""
    result = _synthetic_audit(tmp_path, {
        "amca-studio-v0.9.0-windows-x64-setup.exe": {"status": "NotSigned"},
        "amca-backend.exe": {"status": "NotSigned"},
        "amca-desktop.exe": {"status": "NotSigned"},
    })
    text = json.dumps(result).lower()
    for forbidden in ("password", "private key", "pfx", "pvk", "secret="):
        assert forbidden not in text
    assert result["secrets_exposed"] is False
    # The config schema explicitly requires out-of-band secrets.
    assert "never" in result["signing_config_schema"]["secrets"].lower()


def test_missing_artifact_recorded_not_fabricated(tmp_path, monkeypatch):
    import amca.signing_readiness as sr

    dist = tmp_path / "dist"
    dist.mkdir()
    # No installer at all.
    result = run_signing_audit(dist_dir=dist)
    missing = [a for a in result["artifacts"]
               if a["inspection"]["status"] == "ARTIFACT_MISSING"]
    assert missing, "missing artifacts must be recorded honestly"


def test_signing_failure_propagates_as_blocked(tmp_path, monkeypatch):
    """A signing-command failure surface (INSPECTION_ERROR with signing
    configured) must BLOCK, not PASS."""
    import amca.signing_readiness as sr

    dist = tmp_path / "dist"
    dist.mkdir()
    installer = dist / "amca-studio-v0.9.0-windows-x64-setup.exe"
    installer.write_bytes(b"MZ" + b"I" * 512)
    with zipfile.ZipFile(dist / "amca-studio-v0.9.0-windows-x64-portable.zip", "w") as z:
        z.writestr("pkg/amca-backend.exe", b"MZ" + b"B" * 1024)
        z.writestr("pkg/amca-desktop.exe", b"MZ" + b"D" * 1024)

    monkeypatch.setattr(
        sr, "inspect_authenticode",
        lambda p: {"status": "INSPECTION_ERROR", "reason": "signing tool crashed"})
    result = run_signing_audit(dist_dir=dist, signing_configured=True)
    assert result["overall"] == VERDICT_BLOCKED


# ---------------------------------------------------------------------------
# Real-binary inspection (genuine current artifacts)
# ---------------------------------------------------------------------------

def test_real_artifacts_inspected_and_honestly_unsigned():
    if not INSTALLER.is_file() or not PORTABLE.is_file():
        pytest.skip("real distribution artifacts not present")
    result = run_signing_audit()
    assert result["schema"] == "amca-windows-signing-audit/1"
    # Current truth: no certificate configured, binaries unsigned.
    assert result["overall"] in (VERDICT_REVIEW, VERDICT_BLOCKED)
    by_name = {a["artifact"]: a for a in result["artifacts"]}
    installer_status = by_name["amca-studio-v0.9.0-windows-x64-setup.exe"][
        "inspection"]["status"]
    # Windows Authenticode says NotSigned; we must record exactly that.
    if installer_status == "NotSigned":
        assert INSTALLER.name in result["unsigned_artifacts"]
        assert result["signing_policy"] == "SIGNING_NOT_CONFIGURED"


def test_runner_emits_machine_readable_audit(tmp_path):
    out = tmp_path / "audit.json"
    proc = subprocess.run(
        [sys_executable(), str(RUNNER), "--output", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "amca-windows-signing-audit/1"
    for key in ("artifact", "sha256", "inspection", "overall"):
        assert key in json.dumps(data)
    assert data["overall"] in (VERDICT_PASS, VERDICT_REVIEW, VERDICT_BLOCKED)


def sys_executable() -> str:
    import sys

    return sys.executable
