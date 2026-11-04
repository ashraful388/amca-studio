"""Tests for AMCA Studio v0.9.0 Release Packaging and Distribution Validation.

Milestone: ZCODE-AMCA-STUDIO-V0.9.0-RELEASE-PACKAGING-AND-DISTRIBUTION-VALIDATION-01

Version-aware: the live canonical version is read from src/amca/__init__.py so
the packaging/distribution contract is validated for whatever version the tree
currently declares.
"""

from pathlib import Path
import hashlib
import json
import re
import sqlite3
import zipfile

from amca.distribution_build import MIN_INSTALLER_BYTES, MIN_PORTABLE_BYTES
from amca.release_packaging import (
    CleanInstallationValidator,
    DistributionArtifactAuditor,
    MigrationSafetyValidator,
    OfflineReadinessAuditor,
    ReleaseCertificationEngine,
)

SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
LIVE_VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SOFTWARE_ROOT / "src" / "amca" / "__init__.py").read_text(encoding="utf-8"),
).group(1)


def _synthetic_repo_root(root: Path, version: str) -> Path:
    """Build a physical artifact fixture for the distribution auditor.

    The auditor refuses to fabricate descriptors, so a repository root whose
    software/dist holds real (structurally valid) files is required to exercise
    its naming/checksum contract. The live tree legitimately skips the MSI on
    hosts without the WiX toolchain, so the fixture supplies all three roles.
    """
    dist = root / "software" / "dist"
    dist.mkdir(parents=True, exist_ok=True)

    installer = dist / f"amca-studio-v{version}-windows-x64-setup.exe"
    installer.write_bytes(b"MZ" + b"\x00" * (MIN_INSTALLER_BYTES + 1))

    msi = dist / f"amca-studio-v{version}-windows-x64.msi"
    msi.write_bytes(bytes([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) +
                    b"\x00" * (MIN_INSTALLER_BYTES + 1))

    portable = dist / f"amca-studio-v{version}-windows-x64-portable.zip"
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_STORED) as z:
        z.writestr("amca-studio/amca-backend.exe", b"MZ" + b"B" * MIN_PORTABLE_BYTES)
        z.writestr("amca-studio/amca-desktop.exe", b"MZ" + b"D" * 1024)

    # Minimal canonical metadata so version consistency resolves to `version`.
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


def test_distribution_artifact_metadata_and_version_consistency():
    repo_root = Path(__file__).resolve().parent.parent.parent
    auditor = DistributionArtifactAuditor(repo_root=repo_root)
    audit = auditor.audit_version_consistency()

    # Every active metadata source must agree with the live canonical version.
    assert audit["consistent"] is True
    assert audit["unified_version"] == LIVE_VERSION
    assert audit["pyproject_version"] == LIVE_VERSION
    assert audit["frontend_package_version"] == LIVE_VERSION
    assert audit["backend_module_version"] == LIVE_VERSION


def test_distribution_checksum_and_package_naming(tmp_path: Path):
    """Descriptors are physical — checksums must match bytes on disk."""
    repo_root = _synthetic_repo_root(tmp_path, LIVE_VERSION)
    dist_dir = repo_root / "software" / "dist"
    auditor = DistributionArtifactAuditor(repo_root=repo_root)
    descriptors = auditor.generate_distribution_descriptors(version=LIVE_VERSION)

    # One descriptor per artifact role, all bound to the requested version.
    assert len(descriptors) == 3
    names = {desc.package_name for desc in descriptors}
    assert names == {
        f"amca-studio-v{LIVE_VERSION}-windows-x64-setup.exe",
        f"amca-studio-v{LIVE_VERSION}-windows-x64.msi",
        f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip",
    }
    for desc in descriptors:
        assert desc.version == LIVE_VERSION
        assert len(desc.sha256_checksum) == 64
        assert desc.file_size_bytes > 0
        assert "AMCA-Studio-v0.9.0-Release-Notes.md" in desc.release_notes_ref
        assert desc.package_type in ("installer", "windows-msi", "portable_archive")

        physical = dist_dir / desc.package_name
        assert physical.is_file(), f"{desc.package_name} must physically exist"
        assert physical.stat().st_size == desc.file_size_bytes

        disk_hash = hashlib.sha256(physical.read_bytes()).hexdigest()
        assert disk_hash == desc.sha256_checksum, "checksum must be physical"

    # The auditor must refuse to fabricate a descriptor for a missing file.
    (dist_dir / f"amca-studio-v{LIVE_VERSION}-windows-x64-portable.zip").unlink()
    try:
        auditor.generate_distribution_descriptors(version=LIVE_VERSION)
        raise AssertionError("missing physical artifact must never be fabricated")
    except FileNotFoundError:
        pass


def test_clean_installation_validation(tmp_path: Path):
    target = tmp_path / "fresh_install_root"
    res = CleanInstallationValidator.validate_clean_installation(target)

    assert res.success is True
    assert res.workspace_initialized is True
    assert res.config_initialized is True
    assert res.database_initialized is True
    assert res.network_calls_attempted == 0
    assert (target / ".amca" / "amca_desktop.db").exists()


def test_migration_safety_preserves_existing_assets(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO workspaces VALUES ('ws-a', 'Alpha Workspace')")
        conn.execute("CREATE TABLE artifacts (id TEXT PRIMARY KEY, provenance_hash TEXT)")
        conn.execute("INSERT INTO artifacts VALUES ('art-a', 'hash-aaa')")
        conn.execute("CREATE TABLE knowledge_corpora (id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO knowledge_corpora VALUES ('know-a', 'Alpha Docs')")
        conn.commit()

    res = MigrationSafetyValidator.validate_migration(db_path)

    assert res.migration_safe is True
    assert res.destructive_actions_detected == 0
    assert res.existing_workspaces_preserved == 1
    assert res.existing_artifacts_preserved == 1
    assert res.existing_knowledge_records_preserved == 1


def test_offline_operation_and_zero_telemetry():
    res = OfflineReadinessAuditor.audit_offline_readiness()

    assert res.verdict == "OFFLINE_CERTIFIED"
    assert res.offline_launch_success is True
    assert res.local_workflows_available is True
    assert res.mandatory_cloud_dependency is False
    assert res.telemetry_network_requests == 0


def test_release_certification_engine_verdict(tmp_path: Path):
    repo_root = _synthetic_repo_root(tmp_path, LIVE_VERSION)
    engine = ReleaseCertificationEngine(repo_root=repo_root)
    record = engine.certify_release_candidate(tmp_path / "cert_work")

    assert record.overall_certification_passed is True
    assert record.release_tag == f"v{LIVE_VERSION}-rc1"
    assert record.target_version == LIVE_VERSION
    assert "RECOMMEND RELEASE CANDIDATE 1" in record.recommendation
    # One physically verified descriptor per artifact role (installer, MSI,
    # portable); the Linux tarball is honestly skipped on Windows.
    assert len(record.distribution_artifacts) == 3
    assert {a.package_type for a in record.distribution_artifacts} == {
        "installer",
        "windows-msi",
        "portable_archive",
    }

