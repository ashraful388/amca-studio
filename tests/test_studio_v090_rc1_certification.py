"""Unit tests for AMCA Studio v0.9.0 Release Candidate 1 Certification.

Milestone: ZCODE-AMCA-STUDIO-V0.9.0-RELEASE-CANDIDATE-1-CERTIFICATION-AND-REGRESSION-AUDIT-01
I2A update: certification requires physical artifacts and measured regression
evidence; fabricated defaults and simulated descriptors were removed.

Version-aware: artifact certification is exercised against a physical-artifact
fixture for the live canonical version, so the contract holds for any version.
"""

from pathlib import Path
import hashlib
import json
import re
import zipfile

from amca.distribution_build import MIN_INSTALLER_BYTES, MIN_PORTABLE_BYTES
from amca.rc1_certification import (
    RC1CertificationEngine,
    RegressionCertificationSummary,
)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
LIVE_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)


def _physical_artifact_fixture(root: Path, version: str) -> Path:
    """Physical installer/MSI/portable fixture for the RC1 artifact gate."""
    dist = root / "software" / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    (dist / f"amca-studio-v{version}-windows-x64-setup.exe").write_bytes(
        b"MZ" + b"\x00" * (MIN_INSTALLER_BYTES + 1)
    )
    (dist / f"amca-studio-v{version}-windows-x64.msi").write_bytes(
        bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) +
        b"\x00" * (MIN_INSTALLER_BYTES + 1)
    )
    with zipfile.ZipFile(
        dist / f"amca-studio-v{version}-windows-x64-portable.zip",
        "w", compression=zipfile.ZIP_STORED,
    ) as z:
        z.writestr("amca-studio/amca-backend.exe", b"MZ" + b"B" * MIN_PORTABLE_BYTES)
        z.writestr("amca-studio/amca-desktop.exe", b"MZ" + b"D" * 1024)

    (root / "software" / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "software" / "src" / "amca").mkdir(parents=True, exist_ok=True)
    (root / "software" / "src" / "amca" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "software" / "desktop-ui").mkdir(parents=True, exist_ok=True)
    (root / "software" / "desktop-ui" / "package.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    return root


def test_rc1_regression_suite_health():
    engine = RC1CertificationEngine()
    summary = engine.certify_regression_health(
        backend_pass_count=125,
        backend_fail_count=0,
        frontend_pass_count=59,
        frontend_fail_count=0,
        build_clean=True,
    )

    assert summary.healthy is True
    assert summary.backend_passed >= 125
    assert summary.backend_failed == 0
    assert summary.frontend_passed >= 59
    assert summary.frontend_failed == 0
    assert summary.build_clean is True


def test_rc1_regression_health_requires_measured_counts():
    """I2A: without measured regression evidence the gate must refuse to certify."""
    engine = RC1CertificationEngine()
    cert = engine.generate_rc1_certification()
    assert cert.regression_summary.healthy is False
    assert cert.certified is False
    assert cert.verdict == "CERTIFICATION_REJECTED"


def test_rc1_security_and_governance_invariants():
    engine = RC1CertificationEngine()
    sec = engine.audit_security_and_governance()

    assert sec.certified is True
    assert sec.telemetry_zero is True
    assert sec.hidden_network_dependency is False
    assert sec.hidden_reasoning_storage is False
    assert sec.unsupported_autonomy_claims is False
    assert sec.approval_boundaries_enforced is True


def test_rc1_known_limitations_registry():
    engine = RC1CertificationEngine()
    limits = engine.get_known_limitations_registry()

    assert len(limits.current_limitations) >= 3
    assert len(limits.future_roadmap_items) >= 3
    assert len(limits.unsupported_features) >= 3

    # Confirm explicit unsupported boundaries
    unsupported_text = " ".join(limits.unsupported_features).lower()
    assert "autonomous ai employee" in unsupported_text
    assert "unrestricted background automation" in unsupported_text
    assert "telemetry" in unsupported_text


def test_rc1_artifact_manifest_integrity(tmp_path: Path):
    """I2A: descriptors are physical — checksums must match bytes on disk."""
    repo_root = _physical_artifact_fixture(tmp_path, LIVE_VERSION)
    dist_dir = repo_root / "software" / "dist"
    engine = RC1CertificationEngine(repo_root=repo_root)
    cert = engine.generate_rc1_certification()

    assert cert.release_tag == f"v{LIVE_VERSION}-rc1"
    assert cert.target_version == LIVE_VERSION
    assert len(cert.distribution_artifacts) == 3

    names = {art.package_name for art in cert.distribution_artifacts}
    assert names == {
        f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe",
        f"amca-studio-v{LIVE_VERSION}-windows-x64.msi",
        f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip",
    }

    for art in cert.distribution_artifacts:
        assert len(art.sha256_checksum) == 64
        assert art.file_size_bytes > 0
        assert art.version == LIVE_VERSION

        physical = dist_dir / art.package_name
        assert physical.is_file(), f"{art.package_name} must physically exist"
        assert physical.stat().st_size == art.file_size_bytes
        disk_hash = hashlib.sha256(physical.read_bytes()).hexdigest()
        assert disk_hash == art.sha256_checksum, "checksum must be physical"


def test_rc1_final_certification_verdict(tmp_path: Path):
    """I2A: full certification requires measured regression evidence."""
    repo_root = _physical_artifact_fixture(tmp_path, LIVE_VERSION)
    engine = RC1CertificationEngine(repo_root=repo_root)
    measured = RegressionCertificationSummary(
        backend_passed=191,
        backend_failed=0,
        frontend_passed=59,
        frontend_failed=0,
        build_clean=True,
        healthy=True,
    )
    cert = engine.generate_rc1_certification(regression=measured)

    assert cert.certified is True
    assert cert.verdict == "RC1_CERTIFIED_FOR_DISTRIBUTION"
    assert "RECOMMEND RELEASE CANDIDATE 1" in cert.recommendation
