"""I2A guard tests: functional distribution build pipeline and stub elimination.

Milestone: AG-AMCA-STUDIO-V1.0.0-I2A-FUNCTIONAL-DISTRIBUTION-BUILD-PIPELINE-AND-STUB-ELIMINATION-01

These guards prevent regression back to simulated artifacts:
- checksums must always be computed from physical bytes
- fake/minimal packages must be structurally rejected
- fabricated Linux tarballs must never return
- manifests must record only verified physical artifacts
"""

from pathlib import Path
import hashlib
import json
import re
import zipfile

import pytest

from amca.distribution_build import (
    MIN_INSTALLER_BYTES,
    MIN_PORTABLE_BYTES,
    DistributionBuildError,
    StubArtifactRejected,
    build_physical_descriptor,
    reject_fake_or_minimal_package,
    run_distribution_pipeline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOFTWARE_ROOT = REPO_ROOT / "software"
DIST_DIR = SOFTWARE_ROOT / "dist"
#: Live canonical version: the manifest under test is the one this tree
#: currently declares, never a frozen historical version.
LIVE_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)
MANIFEST_PATH = DIST_DIR / f"release_manifest_v{LIVE_VERSION}.json"
INSTALLER_NAME = f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe"
PORTABLE_NAME = f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip"


def _physical_artifact_fixture(root: Path, version: str) -> Path:
    """Physical installer/MSI/portable fixture for descriptor guard tests.

    The auditor never fabricates descriptors, so exercising its contract
    requires a repository root whose software/dist holds real files for every
    artifact role. The live tree honestly skips the MSI when the WiX toolchain
    is unavailable, so the fixture supplies all three roles.
    """
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


# ---------------------------------------------------------------------------
# Structural rejection of fake/minimal packages
# ---------------------------------------------------------------------------

def test_fake_installer_rejected_by_size_floor(tmp_path: Path):
    """The historical 1062-byte fake setup.exe must be rejected."""
    fake = tmp_path / "fake-setup.exe"
    fake.write_bytes(b"MZ" + b"\x00" * 1000)
    with pytest.raises(StubArtifactRejected, match="below the .* floor"):
        reject_fake_or_minimal_package(fake, "installer")


def test_fake_installer_rejected_without_pe_magic(tmp_path: Path):
    not_pe = tmp_path / "not-pe.exe"
    not_pe.write_bytes(b"PK" + b"\x00" * (MIN_INSTALLER_BYTES + 10))
    with pytest.raises(StubArtifactRejected, match="not a Windows PE"):
        reject_fake_or_minimal_package(not_pe, "installer")


def test_text_only_portable_zip_rejected(tmp_path: Path):
    """The historical 466-byte 3-text-entry portable zip must be rejected."""
    fake = tmp_path / "portable.zip"
    with zipfile.ZipFile(fake, "w") as z:
        z.writestr("amca-studio/version.txt", "0.9.0")
        z.writestr("amca-studio/README.txt", "stub")
        z.writestr("amca-studio/bin/amca.cmd", "@echo off")
    with pytest.raises(StubArtifactRejected, match="no executables"):
        reject_fake_or_minimal_package(fake, "portable_archive")


def test_tiny_portable_zip_rejected_by_size_floor(tmp_path: Path):
    fake = tmp_path / "tiny.zip"
    with zipfile.ZipFile(fake, "w") as z:
        z.writestr("amca-studio/amca-backend.exe", "MZ")
    assert fake.stat().st_size < MIN_PORTABLE_BYTES
    with pytest.raises(StubArtifactRejected, match="below the .* floor"):
        reject_fake_or_minimal_package(fake, "portable_archive")


def test_unknown_package_type_rejected(tmp_path: Path):
    some = tmp_path / "some.bin"
    some.write_bytes(b"x" * 10)
    with pytest.raises(StubArtifactRejected, match="unknown package_type"):
        reject_fake_or_minimal_package(some, "tarball_mystery")


def test_physical_descriptor_checksum_matches_bytes(tmp_path: Path):
    """build_physical_descriptor must hash the file, never fabricate."""
    # Build a portable archive that passes structural checks: two exe entries.
    archive = tmp_path / "portable.zip"
    payload_a = b"MZ" + b"A" * (MIN_PORTABLE_BYTES)
    payload_b = b"MZ" + b"B" * 1000
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("amca-studio/amca-backend.exe", payload_a)
        z.writestr("amca-studio/amca-desktop.exe", payload_b)

    desc = build_physical_descriptor(
        archive, "portable.zip", "win32-x64", "portable_archive", "0.9.0"
    )
    assert desc.sha256_checksum == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert desc.file_size_bytes == archive.stat().st_size


def test_missing_artifact_raises_not_fabricates(tmp_path: Path):
    missing = tmp_path / "does-not-exist.exe"
    with pytest.raises(DistributionBuildError, match="not found"):
        build_physical_descriptor(
            missing, "does-not-exist.exe", "win32-x64", "installer", "0.9.0"
        )


# ---------------------------------------------------------------------------
# Release modules must never regenerate simulated descriptors
# ---------------------------------------------------------------------------

def test_release_packaging_descriptors_are_physical(tmp_path: Path):
    from amca.release_packaging import DistributionArtifactAuditor

    repo_root = _physical_artifact_fixture(tmp_path, LIVE_VERSION)
    auditor = DistributionArtifactAuditor(repo_root=repo_root)
    descriptors = auditor.generate_distribution_descriptors(version=LIVE_VERSION)
    assert len(descriptors) == 3
    for desc in descriptors:
        physical = repo_root / "software" / "dist" / desc.package_name
        assert physical.is_file()
        assert desc.sha256_checksum == hashlib.sha256(physical.read_bytes()).hexdigest()
        assert desc.file_size_bytes == physical.stat().st_size


def test_no_pseudo_checksum_fabrication_pattern_in_release_modules():
    """Guard against reintroduction of sha256(f"...") string-based checksums."""
    import re

    release_sources = [
        REPO_ROOT / "software" / "src" / "amca" / "release_packaging.py",
        REPO_ROOT / "software" / "src" / "amca" / "release_certification.py",
        REPO_ROOT / "software" / "src" / "amca" / "rc1_certification.py",
        REPO_ROOT / "software" / "src" / "amca" / "distribution_build.py",
    ]
    fabrication = re.compile(r"sha256\(\s*f[\"']")
    for src in release_sources:
        text = src.read_text(encoding="utf-8")
        assert not fabrication.search(text), (
            f"{src.name} contains a fabricated string-seeded checksum; "
            "checksums must be computed from physical file bytes"
        )


def test_rc1_certification_rejects_unmeasured_regression():
    from amca.rc1_certification import RC1CertificationEngine

    cert = RC1CertificationEngine(repo_root=REPO_ROOT).generate_rc1_certification()
    assert cert.regression_summary.healthy is False
    assert cert.certified is False
    assert cert.verdict == "CERTIFICATION_REJECTED"


def test_rc1_certification_accepts_measured_regression_with_physical_artifacts(tmp_path: Path):
    from amca.rc1_certification import (
        RC1CertificationEngine,
        RegressionCertificationSummary,
    )

    repo_root = _physical_artifact_fixture(tmp_path, LIVE_VERSION)
    measured = RegressionCertificationSummary(
        backend_passed=191,
        backend_failed=0,
        frontend_passed=59,
        frontend_failed=0,
        build_clean=True,
        healthy=True,
    )
    cert = RC1CertificationEngine(repo_root=repo_root).generate_rc1_certification(
        regression=measured
    )
    assert cert.certified is True
    assert cert.verdict == "RC1_CERTIFIED_FOR_DISTRIBUTION"
    assert len(cert.distribution_artifacts) == 3


# ---------------------------------------------------------------------------
# Physical dist state on this machine
# ---------------------------------------------------------------------------

def test_dist_manifest_records_only_physical_artifacts():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["version"] == LIVE_VERSION
    artifacts = manifest["artifacts"]
    assert len(artifacts) >= 1
    for art in artifacts:
        assert art["version"] == LIVE_VERSION
        physical = DIST_DIR / art["package_name"]
        assert physical.is_file(), f"{art['package_name']} must exist physically"
        assert physical.stat().st_size == art["size_bytes"]
        assert (
            hashlib.sha256(physical.read_bytes()).hexdigest() == art["sha256"]
        )
        # Real artifact floors — no stub/minimal package may be recorded.
        assert art["size_bytes"] >= MIN_PORTABLE_BYTES


def test_dist_installer_is_real_pe_executable():
    installer = DIST_DIR / INSTALLER_NAME
    assert installer.is_file()
    assert installer.stat().st_size >= MIN_INSTALLER_BYTES
    assert installer.read_bytes()[:2] == b"MZ"


def test_dist_portable_carries_real_executables():
    portable = DIST_DIR / PORTABLE_NAME
    assert portable.is_file()
    with zipfile.ZipFile(portable) as z:
        entries = z.namelist()
    exe_entries = [e for e in entries if e.lower().endswith(".exe")]
    assert len(exe_entries) >= 2, f"portable must carry real executables, got {entries}"


def test_sha256_sums_file_matches_manifest():
    sums = DIST_DIR / "SHA256SUMS.txt"
    assert sums.is_file()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = {
        art["package_name"]: art["sha256"] for art in manifest["artifacts"]
    }
    lines = sums.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "SHA256SUMS.txt must list the active release artifacts"
    for line in lines:
        checksum, name = line.split(None, 1)
        name = name.strip()
        assert expected.get(name) == checksum, f"SHA256SUMS mismatch for {name}"


def test_linux_tarball_eliminated_from_dist():
    """The fabricated 160-byte Linux tarball must be gone; honest skip recorded."""
    linux_fake = DIST_DIR / "amca-studio-v0.9.0-linux-x64.tar.gz"
    assert not linux_fake.exists(), (
        "fabricated Linux tarball must not ship from a Windows host"
    )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    skipped = {s["artifact"]: s["reason"] for s in manifest.get("skipped_artifacts", [])}
    linux_key = "amca-studio-v0.9.0-linux-x64.tar.gz"
    if linux_key in skipped:
        assert "BLOCKED" in skipped[linux_key].upper() or "not fabricated" in skipped[linux_key]


def test_pipeline_reuse_mode_end_to_end(tmp_path: Path):
    """Reuse-mode pipeline run must reproduce physical artifacts in a temp dir."""
    result = run_distribution_pipeline(
        SOFTWARE_ROOT,
        version=LIVE_VERSION,
        publish_target=tmp_path / "published",
        run_builds=False,
    )
    assert result.success is True
    assert result.installer is not None
    assert result.portable is not None
    assert result.installer.file_size_bytes >= MIN_INSTALLER_BYTES
    assert result.portable.file_size_bytes >= MIN_PORTABLE_BYTES
    for art in (result.installer, result.portable):
        published = tmp_path / "published" / art.package_name
        assert published.is_file()
        assert hashlib.sha256(published.read_bytes()).hexdigest() == art.sha256_checksum
